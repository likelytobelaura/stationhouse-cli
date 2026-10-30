"""The push gate: before a push or PR goes up to a Station House project, check whether it's an
experiment, and if so, whether its design went through the Experimentation Wizard (three rounds
answered, then finalized as it is now). Three ways in:

- `stationhouse design gate`                 : check the current branch (exit 0 pass, 2 blocked)
- `stationhouse design gate --hook`          : Claude Code PreToolUse hook (the plugin's); reads the
                                                tool call on stdin, blocks experiment pushes with exit 2,
                                                and blocks a coding agent answering the review itself
- `stationhouse design gate --pre-push ...`  : git pre-push hook (`stationhouse hooks install`); checks
                                                each ref git is about to push

It's friction, not a lock: it fails open when nobody is signed in or the platform can't be reached,
and the watcher notes the PR afterwards. Every check is logged as a gate event, including the ones it
couldn't make (unchecked / error), queued locally until the platform takes them.
"""
import json
import os
import re
import shlex
import sys
from pathlib import Path

from . import client, wizard
from .auth import AuthError
from .client import ApiError, request
from .config import config_dir

PASS, BLOCK = 0, 2
ZERO_SHA = "0" * 40
HOOK_TIMEOUT = 8  # seconds per platform call inside a hook: a slow platform must not stall the push

_SEP = r"(?:^|[;&|(]\s*|\s)"
GIT_PUSH = re.compile(_SEP + r"(?:\S*/)?git(?:\s+(?:-C\s+(?:\"[^\"]*\"|'[^']*'|\S+)|-c\s+\S+|--[\w-]+(?:=\S+)?))*\s+push\b")
GH_PR = re.compile(_SEP + r"gh\s+(?:pr\s+create\b|api\b[^;&|]*\bpulls\b)")
HF_UPLOAD = re.compile(_SEP + r"(?:hf|huggingface-cli)\s+upload(?:-large-folder)?\b")
PY_UPLOAD = re.compile(r"\bcreate_pr\s*=\s*(?:True|1)\b|\b(?:upload_folder|upload_file|upload_large_folder|create_commit)\s*\(")
PUSHES = [GIT_PUSH, GH_PR, HF_UPLOAD, PY_UPLOAD]
# A coding agent typing the contributor's answers: the review is theirs to answer.
ANSWERING = re.compile(r"\b(?:stationhouse|sth)\b[^;&|]*\bdesign\s+answer\b|/api/v1/design/sessions/[^\s'\"]*/answers\b")


def default_base(root) -> str | None:
    head = wizard.git(root, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
    candidates = [head.replace("refs/remotes/", "")] if head else []
    for ref in (*candidates, "origin/main", "origin/master"):
        if wizard.git(root, "rev-parse", "--verify", "--quiet", ref):
            return ref
    return None


def changed_files(root, base: str | None = None, ref: str = "HEAD") -> list[str]:
    """Files `ref` adds or changes compared with its base (committed work: what a push sends)."""
    base = base or default_base(root)
    if not base:
        return []
    out = wizard.git(root, "diff", "--name-only", "--diff-filter=AMR", f"{base}...{ref}")
    return [f for f in (out or "").splitlines() if f]


# --- gate events (with an offline queue) ------------------------------------------------------------

def _queue_path() -> Path:
    return config_dir() / "gate-events-queue.jsonl"


def _log(repo, branch, kind, outcome, session_id=None, head_sha=None):
    """Record the check on the platform; if that fails, keep it locally and send it with the next one."""
    event = {"repo": repo, "branch": branch, "headSha": head_sha, "kind": kind, "outcome": outcome, "sessionId": session_id}
    queue = _queue_path()
    try:
        pending = [json.loads(l) for l in queue.read_text().splitlines() if l.strip()][-50:] if queue.exists() else []
    except (OSError, ValueError):
        pending = []
    sent = 0
    try:
        for e in [*pending, event]:
            request("POST", "/api/v1/design/gate-events", e)
            sent += 1
        queue.unlink(missing_ok=True)
    except (ApiError, AuthError, OSError):
        try:
            queue.parent.mkdir(parents=True, exist_ok=True)
            queue.write_text("".join(json.dumps(e) + "\n" for e in [*pending, event][sent:]))
        except OSError:
            pass


# --- the check ------------------------------------------------------------------------------------

def check(cwd=".", *, repo: str | None = None, files: list[str] | None = None, base: str | None = None,
          body: str = "", title: str = "", ref: str | None = None, branch: str | None = None,
          doc_root: Path | None = None, doc_files: list[str] | None = None) -> dict:
    """{outcome: passed|blocked|not-experiment|not-project|unchecked, kind, reason, repo, branch, sessionId?, marker?, message}

    ref/branch: what's being pushed (default: the checked-out branch). doc_root/doc_files: where to
    look for the design document when the upload isn't a git checkout (`hf upload <folder>`)."""
    root = wizard.repo_root(cwd) or Path(cwd).resolve()
    git_root = wizard.repo_root(cwd) is not None
    branch = branch or (ref.removeprefix("refs/heads/") if ref and ref != "HEAD" else None) or (wizard.current_branch(root) if git_root else None)
    candidates = ([repo] if repo else []) + (wizard.remote_repos(root) if git_root else [])
    project = wizard.project_repo(candidates)
    if not project:
        return {"outcome": "not-project", "message": "Not a Station House project repo; nothing to check."}
    repo = project["repo"]
    if files is None:
        files = changed_files(root, base, ref or "HEAD") if git_root else []
    head = wizard.git(root, "rev-parse", ref or "HEAD") if git_root else None
    verdict = wizard.classify(files, title or ((wizard.git(root, "log", "-1", "--format=%s", ref or "HEAD") or "") if git_root else ""), body)
    out = {"repo": repo, "branch": branch, "kind": verdict["kind"], "reason": verdict["reason"]}
    if verdict["kind"] != "experiment":
        _log(repo, branch, verdict["kind"], "not-experiment", head_sha=head)
        return {**out, "outcome": "not-experiment", "message": f"Not an experiment ({verdict['reason']}); no design review needed."}

    try:
        session = wizard.find(repo, branch)
    except (ApiError, AuthError) as e:
        _log(repo, branch, "experiment", "unchecked", head_sha=head)
        return {**out, "outcome": "unchecked",
                "message": f"This looks like an experiment, but the design review couldn't be checked ({e}). "
                           "Pushing anyway; the PR will be noted if it has no completed review."}

    def blocked(why):
        _log(repo, branch, "experiment", "blocked", session and session.get("id"), head)
        return {**out, "outcome": "blocked", "sessionId": session and session.get("id"), "message": why + "\n\n" + HOW}

    if not session:
        where = f"branch {branch}" if branch else "this folder (no git branch: start the review from here)"
        return blocked(f"This push is an experiment ({verdict['reason']}) and the design for {where} hasn't been through "
                       "the Station House Experimentation Wizard yet.")
    if session.get("roundsAnswered", 0) < wizard.ROUNDS:
        return blocked(f"The design review for this branch ({session['id']}) has {session.get('roundsAnswered', 0)} of "
                       f"{wizard.ROUNDS} rounds answered. Finish the rounds first.")
    if not session.get("finalSha256"):
        return blocked(f"All three rounds of {session['id']} are answered, but the design isn't finalized. "
                       "Finalize it (design_finalize, or `stationhouse design finalize <file>`).")
    if doc_root is not None:
        doc = wizard.find_design_doc(doc_files or [], doc_root)
        shown = doc.relative_to(doc_root) if doc else None
    else:
        doc = wizard.find_design_doc(files, root)
        shown = doc.relative_to(root) if doc else None
    text = doc.read_text(encoding="utf-8") if doc else wizard.pr_body_design(body)
    if text is None:
        return blocked(f"This experiment push has no design document (DESIGN.md, EXPERIMENT.md or a `## Design` section) "
                       f"to compare with the finalized review {session['id']}. Push the design with it.")
    if wizard.design_hash(text) != session["finalSha256"]:
        where = shown or "the PR description's ## Design"
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


# --- reading the command --------------------------------------------------------------------------

def _segments(command: str) -> list[tuple[str, str | None]]:
    """[(segment, directory it runs in)] for a shell line, following `cd X &&`."""
    out, cwd = [], None
    for seg in re.split(r"&&|\|\||;|\n", command):
        seg = seg.strip()
        m = re.match(r"^cd\s+(\"[^\"]*\"|'[^']*'|\S+)\s*$", seg)
        if m:
            target = m.group(1).strip("'\"")
            cwd = target if os.path.isabs(target) or cwd is None else os.path.join(cwd, target)
            continue
        out.append((seg, cwd))
    return out


def _git_push_target(words: list[str]) -> tuple[str | None, str | None]:
    """(git -C directory, local ref being pushed) for a `git ... push ...` command."""
    directory, i = None, 0
    while i < len(words) and not words[i].endswith("git"):
        i += 1
    i += 1
    while i < len(words) and words[i] != "push":
        if words[i] == "-C" and i + 1 < len(words):
            directory = words[i + 1]
            i += 1
        elif words[i] == "-c":
            i += 1
        i += 1
    positional, i = [], i + 1
    takes_value = {"-o", "--push-option", "--repo", "--receive-pack", "--exec"}
    while i < len(words):
        w = words[i]
        if w in takes_value:
            i += 2
            continue
        if not w.startswith("-"):
            positional.append(w)
        i += 1
    refspecs = positional[1:]
    if not refspecs:
        return directory, None
    src = refspecs[0].lstrip("+").split(":", 1)[0]
    return directory, (None if src in ("", "HEAD") else src)


def _hf_upload_target(words: list[str], cwd: str) -> tuple[str | None, list[str], Path | None, list[str]]:
    """(hf repo id, files as they'll land in the repo, local folder, files relative to it) for `hf upload`."""
    try:
        i = next(i for i, w in enumerate(words) if w in ("upload", "upload-large-folder"))
    except StopIteration:
        return None, [], None, []
    large = words[i] == "upload-large-folder"
    args, k, space = [], i + 1, False
    while k < len(words):
        w = words[k]
        if w == "--repo-type" and k + 1 < len(words):
            space = words[k + 1] == "space"
            k += 2
            continue
        if w.startswith("--repo-type="):
            space = w.split("=", 1)[1] == "space"
        elif not w.startswith("-"):
            args.append(w)
        elif w in ("--commit-message", "--commit-description", "--revision", "--include", "--exclude", "--token",
                   "--delete", "--every", "--pr-title", "--num-workers"):
            k += 1
        k += 1
    if not args:
        return None, [], None, []
    repo_id = args[0]
    local = Path(cwd, args[1] if len(args) > 1 else ".").expanduser().resolve()
    in_repo = "" if large else (args[2] if len(args) > 2 else ("" if local.is_dir() else local.name)).strip("/")
    files, local_files = [], []
    if local.is_dir():
        for p in sorted(local.rglob("*")):
            if p.is_file() and ".git" not in p.parts:
                rel = p.relative_to(local).as_posix()
                local_files.append(rel)
                files.append(f"{in_repo}/{rel}".lstrip("/"))
            if len(files) >= 2000:
                break
    elif local.is_file():
        files, local_files = [in_repo or local.name], [local.name]
        local = local.parent
    return (f"hf:{repo_id}" if space else None), files, local, local_files


def hook(stdin_text: str, cwd: str | None = None) -> tuple[int, str]:
    """A Claude Code PreToolUse call: (exit code, message). Exit 2 blocks the tool call and shows Claude the message."""
    try:
        event = json.loads(stdin_text or "{}")
    except ValueError:
        return PASS, ""
    command = ((event.get("tool_input") or {}).get("command") or "") if isinstance(event, dict) else ""
    if ANSWERING.search(command):
        return BLOCK, ("Station House: the design review's questions are for the contributor to answer themselves, not the "
                       "coding agent. Show them the questions and wait; they answer in the form, at the /wizard link, or with "
                       "`stationhouse design answer` in their own terminal.")
    base_cwd = cwd or event.get("cwd") or os.getcwd()
    target = next(((seg, d) for seg, d in _segments(command) if any(p.search(seg) for p in PUSHES)), None)
    if not target:
        return PASS, ""
    seg, seg_cwd = target
    run_cwd = str(Path(base_cwd, seg_cwd).resolve()) if seg_cwd else base_cwd
    try:
        words = shlex.split(seg)
    except ValueError:
        words = seg.split()
    kwargs: dict = {}
    if HF_UPLOAD.search(seg):
        repo, files, local, local_files = _hf_upload_target(words, run_cwd)
        if repo is None and not files:
            return PASS, ""
        kwargs = {"repo": repo, "files": files, "doc_root": local, "doc_files": local_files}
        if local and local.is_dir():
            run_cwd = str(local)
    elif GIT_PUSH.search(seg):
        directory, ref = _git_push_target(words)
        if directory:
            run_cwd = str(Path(run_cwd, directory).resolve())
        if ref:
            kwargs = {"ref": ref}
    body = ""
    bm = re.search(r"--body(?:-file)?[ =](\"[^\"]*\"|'[^']*'|\S+)", seg)
    if bm and "--body-file" in bm.group(0):
        try:
            body = Path(run_cwd, bm.group(1).strip("'\"")).expanduser().read_text(encoding="utf-8")
        except OSError:
            body = ""
    elif bm:
        body = bm.group(1).strip("'\"")
    client.TIMEOUT = HOOK_TIMEOUT
    try:
        result = check(run_cwd, body=body, **kwargs)
    except Exception as e:  # noqa: BLE001 - never wedge the agent on a gate bug; log it, the watcher still notes the PR
        _log(kwargs.get("repo") or "unknown:unknown/unknown", None, "experiment", "error")
        return PASS, f"Station House design gate skipped: {e}"
    if result["outcome"] == "blocked":
        return BLOCK, "Station House: " + result["message"]
    return PASS, result.get("message", "")


def pre_push(remote_url: str | None, stdin_text: str = "", cwd=".") -> tuple[int, str]:
    """git pre-push: checks each ref git is pushing (from stdin); exit 1 blocks the push (git's convention)."""
    client.TIMEOUT = HOOK_TIMEOUT
    repo = wizard.repo_from_url(remote_url) if remote_url else None
    root = wizard.repo_root(cwd) or Path(cwd)
    messages = []
    lines = [l.split() for l in (stdin_text or "").splitlines() if len(l.split()) == 4]
    targets = [(local_ref, local_sha, remote_sha) for local_ref, local_sha, _remote_ref, remote_sha in lines if local_sha != ZERO_SHA]
    if not lines:
        targets = [("HEAD", "HEAD", ZERO_SHA)]
    for local_ref, local_sha, remote_sha in targets:
        base = remote_sha if remote_sha != ZERO_SHA else None
        branch = local_ref.removeprefix("refs/heads/") if local_ref.startswith("refs/heads/") else None
        try:
            result = check(cwd, repo=repo, files=changed_files(root, base, local_sha), ref=local_sha, branch=branch)
        except Exception as e:  # noqa: BLE001 - a gate bug must not block every push
            _log(repo or "unknown:unknown/unknown", branch, "experiment", "error")
            messages.append(f"Station House design gate skipped: {e}")
            continue
        if result["outcome"] == "blocked":
            return 1, "Station House: " + result["message"] + "\n(To push anyway: git push --no-verify. The PR will be noted.)"
        if result.get("message"):
            messages.append(result["message"])
    return 0, "\n".join(messages)


PRE_PUSH_HOOK = """#!/bin/sh
# stationhouse design gate (written by `stationhouse hooks install`): experiment pushes need a design
# reviewed by the Station House Experimentation Wizard. Remove this file to turn it off.
exec {command} design gate --pre-push "$2"
"""


def main_pre_push(remote_url: str | None) -> int:
    code, message = pre_push(remote_url, sys.stdin.read() if not sys.stdin.isatty() else "")
    if message:
        print(message, file=sys.stderr)
    return code
