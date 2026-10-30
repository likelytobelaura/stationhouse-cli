"""The push gate: before a push or PR goes up to a Station House project, check whether it's an
experiment, and if so, whether its design went through the Experimentation Wizard (three rounds
answered, then finalized as it is now). Three ways in:

- `stationhouse design gate`                 : check the current branch (exit 0 pass, 2 blocked)
- `stationhouse design gate --hook`          : Claude Code PreToolUse hook (the plugin's); reads the
                                                tool call on stdin, blocks experiment pushes with exit 2
- `stationhouse design gate --pre-push ...`  : git pre-push hook (`stationhouse hooks install`)

It fails open: when the platform can't be reached or nobody is signed in, the push goes ahead with a
warning, and the watcher notes the PR afterwards. Every check is logged (gate events) for coverage.
"""
import json
import os
import re
import shlex
from pathlib import Path

from . import wizard
from .auth import AuthError
from .client import ApiError, request

PASS, BLOCK = 0, 2

PUSHES = re.compile(
    r"(?:^|[;&|(]\s*|\s)(?:"
    r"git\s+(?:-C\s+\S+\s+)?push\b"
    r"|gh\s+pr\s+create\b"
    r"|(?:hf|huggingface-cli)\s+upload\b[^;&|]*--create[-_]pr"
    r"|create_pr\s*=\s*True"
    r")"
)


def default_base(root) -> str | None:
    head = wizard.git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    candidates = [head.replace("refs/remotes/", "")] if head else []
    for ref in (*candidates, "origin/main", "origin/master"):
        if wizard.git(root, "rev-parse", "--verify", "--quiet", ref):
            return ref
    return None


def changed_files(root, base: str | None = None) -> list[str]:
    """Files this branch adds or changes compared with its base (committed work: what a push sends)."""
    base = base or default_base(root)
    if not base:
        return []
    out = wizard.git(root, "diff", "--name-only", "--diff-filter=AMR", f"{base}...HEAD")
    return [f for f in (out or "").splitlines() if f]


def _log(repo, branch, kind, outcome, session_id=None, head_sha=None):
    try:
        request("POST", "/api/v1/design/gate-events", {"repo": repo, "branch": branch, "headSha": head_sha, "kind": kind,
                                                       "outcome": outcome, "sessionId": session_id})
    except (ApiError, AuthError):
        pass


def check(cwd=".", *, repo: str | None = None, files: list[str] | None = None, base: str | None = None,
          body: str = "", title: str = "", root: Path | None = None) -> dict:
    """{outcome: passed|blocked|not-experiment|not-project|unchecked, kind, reason, repo, branch, sessionId?, marker?, message}"""
    root = root or wizard.repo_root(cwd) or Path(cwd).resolve()
    branch = wizard.current_branch(root)
    candidates = ([repo] if repo else []) + wizard.remote_repos(root)
    project = wizard.project_repo(candidates)
    if not project:
        return {"outcome": "not-project", "message": "Not a Station House project repo; nothing to check."}
    repo = project["repo"]
    files = changed_files(root, base) if files is None else files
    verdict = wizard.classify(files, title or (wizard.git(root, "log", "-1", "--format=%s") or ""), body)
    out = {"repo": repo, "branch": branch, "kind": verdict["kind"], "reason": verdict["reason"]}
    head = wizard.git(root, "rev-parse", "HEAD")
    if verdict["kind"] != "experiment":
        _log(repo, branch, verdict["kind"], "not-experiment", head_sha=head)
        return {**out, "outcome": "not-experiment", "message": f"Not an experiment ({verdict['reason']}); no design review needed."}

    try:
        session = wizard.find(repo, branch)
    except (ApiError, AuthError) as e:
        return {**out, "outcome": "unchecked",
                "message": f"This looks like an experiment, but the design review couldn't be checked ({e}). "
                           "Pushing anyway; the PR will be noted if it has no completed review."}

    def blocked(why):
        _log(repo, branch, "experiment", "blocked", session and session.get("id"), head)
        return {**out, "outcome": "blocked", "sessionId": session and session.get("id"), "message": why + "\n\n" + HOW}

    if not session:
        return blocked(f"This push is an experiment ({verdict['reason']}) and its design hasn't been through the "
                       "Station House Experimentation Wizard yet.")
    if session.get("roundsAnswered", 0) < wizard.ROUNDS:
        return blocked(f"The design review for this branch ({session['id']}) has {session.get('roundsAnswered', 0)} of "
                       f"{wizard.ROUNDS} rounds answered. Finish the rounds first.")
    if not session.get("finalSha256"):
        return blocked(f"All three rounds of {session['id']} are answered, but the design isn't finalized. "
                       "Finalize it (design_finalize, or `stationhouse design finalize <file>`).")
    doc = wizard.find_design_doc(files, root)
    text = doc.read_text(encoding="utf-8") if doc else wizard.pr_body_design(body)
    if text is not None and wizard.design_hash(text) != session["finalSha256"]:
        where = doc.relative_to(root) if doc else "the PR description's ## Design"
        return blocked(f"{where} changed after the design was finalized ({session['id']}). If the change is small, "
                       "finalize again; if it changes the experiment, run another review.")
    _log(repo, branch, "experiment", "passed", session["id"], head)
    marker = f"Design-Session: {session['id']}"
    return {**out, "outcome": "passed", "sessionId": session["id"], "marker": marker,
            "message": f"Design reviewed ({session['id']}). Put this line in the PR description:\n{marker}"}


HOW = """How to run it: with the Station House plugin or MCP server, call the `design_start` tool, then
`design_submit_round` with the design file for each of the three rounds; the contributor answers each
round in the form that appears. Then `design_finalize`, and put the `Design-Session:` line it gives
you in the PR description. Without MCP: `stationhouse design start`, `stationhouse design round <file>`,
answer at the link it prints, and `stationhouse design finalize <file>`.
Coding agents: never answer the questions yourself; they're for the contributor."""


# --- Claude Code hook ---------------------------------------------------------------------------

def _hf_upload_target(words: list[str]) -> tuple[str | None, list[str], str | None]:
    """(hf repo id, files as they'll land in the repo, local folder) for an `hf upload` command."""
    try:
        i = next(i for i, w in enumerate(words) if w == "upload")
    except StopIteration:
        return None, [], None
    args, k, space = [], i + 1, False
    while k < len(words):
        w = words[k]
        if w in ("--repo-type",) and k + 1 < len(words):
            space = words[k + 1] == "space"
            k += 2
            continue
        if w.startswith("--repo-type="):
            space = w.split("=", 1)[1] == "space"
        elif not w.startswith("-"):
            args.append(w)
        elif w in ("--commit-message", "--commit-description", "--revision", "--include", "--exclude", "--token",
                   "--delete", "--every", "--pr-title"):
            k += 1
        k += 1
    if not args:
        return None, [], None
    repo_id = args[0]
    local = Path(args[1] if len(args) > 1 else ".").expanduser()
    in_repo = (args[2] if len(args) > 2 else ("" if local.is_dir() else local.name)).strip("/")
    files = []
    if local.is_dir():
        for p in sorted(local.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                files.append(f"{in_repo}/{p.relative_to(local).as_posix()}".lstrip("/"))
            if len(files) >= 2000:
                break
    elif local.is_file():
        files = [in_repo or local.name]
    return (f"hf:{repo_id}" if space else None), files, str(local)


def hook(stdin_text: str, cwd: str | None = None) -> tuple[int, str]:
    """A Claude Code PreToolUse call: (exit code, message). Exit 2 blocks the tool call and shows Claude the message."""
    try:
        event = json.loads(stdin_text or "{}")
    except ValueError:
        return PASS, ""
    command = ((event.get("tool_input") or {}).get("command") or "") if isinstance(event, dict) else ""
    if not PUSHES.search(command):
        return PASS, ""
    cwd = cwd or event.get("cwd") or os.getcwd()
    m = re.search(r"git\s+-C\s+(\S+)", command)
    if m:
        cwd = str((Path(cwd) / m.group(1)).resolve())
    try:
        words = shlex.split(command)
    except ValueError:
        words = command.split()
    kwargs = {}
    if re.search(r"(hf|huggingface-cli)\s+upload\b", command):
        repo, files, local = _hf_upload_target(words)
        if repo is None and not files:
            return PASS, ""
        kwargs = {"repo": repo, "files": files}
        if local and Path(local).is_dir():
            cwd = local
    body = ""
    bm = re.search(r"--body(?:-file)?[ =](\"[^\"]*\"|'[^']*'|\S+)", command)
    if bm and "--body-file" in bm.group(0):
        try:
            body = Path(bm.group(1).strip("'\"")).expanduser().read_text(encoding="utf-8")
        except OSError:
            body = ""
    elif bm:
        body = bm.group(1).strip("'\"")
    try:
        result = check(cwd, body=body, **kwargs)
    except Exception as e:  # noqa: BLE001 - never wedge the agent on a gate bug; the watcher still notes the PR
        return PASS, f"Station House design gate skipped: {e}"
    if result["outcome"] == "blocked":
        return BLOCK, "Station House: " + result["message"]
    if result["outcome"] == "passed":
        return PASS, result["message"]
    return PASS, result.get("message", "")


def pre_push(remote_url: str | None, cwd=".") -> tuple[int, str]:
    """git pre-push: exit 1 blocks the push (git's convention)."""
    repo = wizard.repo_from_url(remote_url) if remote_url else None
    result = check(cwd, repo=repo)
    if result["outcome"] == "blocked":
        return 1, "Station House: " + result["message"] + "\n(To push anyway: git push --no-verify. The PR will be noted.)"
    return 0, result.get("message", "")


PRE_PUSH_HOOK = """#!/bin/sh
# stationhouse design gate (written by `stationhouse hooks install`): experiment pushes need a design
# reviewed by the Station House Experimentation Wizard. Remove this file to turn it off.
exec {command} design gate --pre-push "$2"
"""
