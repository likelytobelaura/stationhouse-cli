"""The Experimentation Wizard, CLI side: before an experiment PR is pushed, its design gets three
rounds of questions from the platform (claim and controls, data and noise, metric and readout), and
the contributor answers each round themselves. This module has the shared pieces: the design hash,
which repo and branch we're in, whether a change is an experiment, where its design document is,
and the /api/v1/design calls. gate.py, mcp_server.py and the `design` commands build on it.
The API contract is the platform's docs/design-wizard.md.
"""
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path

from .client import ApiError, request
from .config import config_dir

ROUNDS = 3
MIN_ANSWER = 40
VIAS = ("mcp-elicit", "web", "cli-tty")

MARKER_LINE = re.compile(r"^\s*Design-Session:\s*ds_[A-Za-z0-9]+\s*$")
MARKER = re.compile(r"Design-Session:\s*(ds_[A-Za-z0-9]{22})\b")


def normalize(text: str) -> str:
    """What the design hash covers; the platform and hf-watch use the same rule and fixtures."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n") if not MARKER_LINE.match(line)).strip()


def design_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def marker_in(text: str) -> str | None:
    m = MARKER.search(text or "")
    return m.group(1) if m else None


# --- which repo ---------------------------------------------------------------------------------

# Used when the platform can't be reached; GET /api/v1/design/repos is the live list.
KNOWN_REPOS = [
    {"repo": "hf:Station-house/ConspiracyBench", "projectId": "conspiracybench"},
    {"repo": "github:Station-House/marin-neuron-hunt", "projectId": "marin-neuron-atlas"},
    {"repo": "hf:Station-house/Consciousness-Self-Attribution", "projectId": "llm-self-attribution"},
    {"repo": "github:Station-House/LLMConsciousnessSelfAttributionRepo", "projectId": "llm-self-attribution"},
]
REPOS_CACHE_SECONDS = 24 * 3600

_REMOTES = [
    (re.compile(r"(?:huggingface\.co|hf\.co)[/:](?:spaces/)([^/\s]+/[^/\s]+?)(?:\.git)?/?$"), "hf"),
    (re.compile(r"github\.com[/:]([^/\s]+/[^/\s]+?)(?:\.git)?/?$"), "github"),
]


def repo_from_url(url: str) -> str | None:
    """`hf:owner/name` or `github:owner/name` for a git remote URL, or None for anything else."""
    for pattern, kind in _REMOTES:
        m = pattern.search(url.strip())
        if m:
            return f"{kind}:{m.group(1)}"
    return None


def git(cwd, *args) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def repo_root(cwd) -> Path | None:
    top = git(cwd, "rev-parse", "--show-toplevel")
    return Path(top) if top else None


def current_branch(cwd) -> str | None:
    b = git(cwd, "rev-parse", "--abbrev-ref", "HEAD")
    return None if not b or b == "HEAD" else b


def remote_repos(cwd) -> list[str]:
    out = git(cwd, "remote", "-v") or ""
    seen = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            r = repo_from_url(parts[1])
            if r and r not in seen:
                seen.append(r)
    return seen


def known_repos() -> list[dict]:
    """The repos the wizard covers, from the platform (cached for a day), else the built-in list."""
    cache = config_dir() / "design-repos.json"
    try:
        cached = json.loads(cache.read_text())
        if time.time() - cached["at"] < REPOS_CACHE_SECONDS:
            return cached["repos"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        repos = (request("GET", "/api/v1/design/repos") or {}).get("repos") or KNOWN_REPOS
    except Exception:  # noqa: BLE001 - offline or signed out: the built-in list is good enough
        return KNOWN_REPOS
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"at": time.time(), "repos": repos}))
    except OSError:
        pass
    return repos


def project_repo(candidates: list[str]) -> dict | None:
    """The first candidate repo the wizard covers, as {repo, projectId} with the canonical spelling."""
    known = {r["repo"].lower(): r for r in known_repos()}
    for c in candidates:
        if c and c.lower() in known:
            return known[c.lower()]
    return None


# --- is it an experiment ------------------------------------------------------------------------

# The same file rules as hf-watch's assess_pr.py (rule_verdict), plus experiment requests and
# design documents, which come before any results.
RESULT_HINTS = [
    (re.compile(r"(^|/)results?/", re.I), "a results/ directory"),
    (re.compile(r"manifest[^/]*\.json$", re.I), "a run manifest"),
    (re.compile(r"(^|/)EXPERIMENT[^/]*\.(md|txt)$", re.I), "an EXPERIMENT write-up"),
    (re.compile(r"\.(eval|csv)$", re.I), ".eval or .csv output"),
    (re.compile(r"(summary|metrics)[^/]*\.json$", re.I), "a summary/metrics JSON"),
]
REQUESTS = [
    (re.compile(r"^requests/[^/]+(\.ya?ml|/DESIGN\.md)$", re.I), "an experiment request"),
    (re.compile(r"^experiment_requests/.+/[^/]+\.md$|^experiment_requests/(?!README|TEMPLATE)[^/]+\.md$", re.I), "an experiment request"),
    (re.compile(r"(^|/)EXPERIMENT\.md$", re.I), "an EXPERIMENT.md"),
    (re.compile(r"(^|/)DESIGN\.md$", re.I), "a DESIGN.md"),
]
OTHER_ONLY = [
    (re.compile(r"^questions/"), "benchmark questions"),
    (re.compile(r"^explanations/"), "neuron explanations"),
    (re.compile(r"(^|/)(README|CONTRIBUTING|AGENTS|CLAUDE|LICENSE|CHANGELOG)[^/]*$|^docs/|^\.github/", re.I), "docs or repo settings"),
]


def classify_files(files: list[str]) -> tuple[str | None, str]:
    """('experiment' | 'other', reason) when the file list settles it, else (None, reason)."""
    if not files:
        return "other", "no changed files"
    for pattern, what in REQUESTS:
        hit = next((f for f in files if pattern.search(f)), None)
        if hit:
            return "experiment", f"adds or changes {what} ({hit})"
    kinds = [what for pattern, what in RESULT_HINTS if any(pattern.search(f) for f in files)]
    if len(kinds) >= 2:
        return "experiment", f"adds {' and '.join(kinds[:3])}, so it reports a run"
    if all(any(p.search(f) for p, _ in OTHER_ONLY) for f in files):
        return "other", "only " + ", ".join(sorted({w for f in files for p, w in OTHER_ONLY if p.search(f)}))
    return None, "the file list doesn't settle it"


def classify(files: list[str], title: str = "", body: str = "") -> dict:
    """{kind, reason, method}: the file rules, then the platform's classifier. Can't reach it: 'other'."""
    kind, reason = classify_files(files)
    if kind:
        return {"kind": kind, "reason": reason, "method": "rules"}
    try:
        res = request("POST", "/api/v1/design/classify", {"files": files[:200], "title": title, "body": body}) or {}
        return {"kind": res.get("kind", "other"), "reason": res.get("reason", ""), "method": "model"}
    except ApiError as e:
        return {"kind": "other", "reason": f"couldn't classify it ({e}); letting it through", "method": "unavailable"}


# --- the design document ------------------------------------------------------------------------

def find_design_doc(files: list[str], root: Path) -> Path | None:
    """The file that holds the design, first match: requests/<id>/DESIGN.md, the EXPERIMENT.md
    nearest the change, any DESIGN.md. None means it's in the PR description (`## Design`)."""
    existing = [f for f in files if (root / f).is_file()]
    for f in existing:
        if re.match(r"^requests/[^/]+/DESIGN\.md$", f, re.I):
            return root / f
    for f in existing:
        if re.search(r"(^|/)EXPERIMENT\.md$", f, re.I):
            return root / f
    # An EXPERIMENT.md next to (or above) the changed files, even if this change doesn't touch it.
    for f in existing:
        for parent in (root / f).parents:
            if parent == root.parent or not str(parent).startswith(str(root)):
                break
            if (parent / "EXPERIMENT.md").is_file():
                return parent / "EXPERIMENT.md"
    for f in existing:
        if re.search(r"(^|/)DESIGN\.md$", f, re.I):
            return root / f
    return None


def pr_body_design(body: str) -> str | None:
    """The `## Design` section of a PR description, if there is one."""
    m = re.search(r"^##\s+Design\s*$(.*?)(?=^##\s|\Z)", body or "", re.M | re.S)
    return m.group(1).strip() if m and m.group(1).strip() else None


def results_summary(files: list[str], root: Path, limit: int = 40) -> str:
    """Result files in the change, plus the first lines of any summary/metrics file, for the reviewer."""
    hits = [f for f in files if any(p.search(f) for p, _ in RESULT_HINTS) and not re.search(r"EXPERIMENT", f)]
    if not hits:
        return ""
    lines = ["Result files:", *hits[:50]]
    for f in hits:
        if re.search(r"(summary|metrics)[^/]*\.json$", f, re.I) and (root / f).is_file():
            head = (root / f).read_text(encoding="utf-8", errors="replace").splitlines()[:limit]
            lines += ["", f"{f} (first {len(head)} lines):", *head]
            break
    return "\n".join(lines)[:8000]


# --- sessions -----------------------------------------------------------------------------------

def _state_path() -> Path:
    return config_dir() / "design-sessions.json"


def remember(repo: str, branch: str | None, session_id: str) -> None:
    path = _state_path()
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        state = {}
    state[f"{repo.lower()}|{branch or ''}"] = session_id
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=1))


def remembered(repo: str, branch: str | None) -> str | None:
    try:
        return json.loads(_state_path().read_text()).get(f"{repo.lower()}|{branch or ''}")
    except (OSError, ValueError, AttributeError):
        return None


def start(repo: str, branch: str | None, title: str, project_id: str | None = None) -> dict:
    body = {"repo": repo, "branch": branch, "title": title, **({"projectId": project_id} if project_id else {})}
    session = request("POST", "/api/v1/design/sessions", body)["session"]
    remember(repo, branch, session["id"])
    return session


def get(session_id: str) -> dict:
    return request("GET", f"/api/v1/design/sessions/{session_id}")


def find(repo: str, branch: str | None) -> dict | None:
    """This repo and branch's session: the one this machine started, else the newest on the platform."""
    sid = remembered(repo, branch)
    if sid:
        try:
            return get(sid)["session"]
        except ApiError as e:
            if e.status != 404:
                raise
    query = {"repo": repo, **({"branch": branch} if branch else {})}
    sessions = (request("GET", "/api/v1/design/sessions", query=query) or {}).get("sessions") or []
    return sessions[0] if sessions else None


def ask_round(session_id: str, draft: str, results: str = "", format_check: list[str] | None = None) -> dict:
    return request("POST", f"/api/v1/design/sessions/{session_id}/rounds",
                   {"draft": draft, "resultsSummary": results, "formatCheck": format_check or []})


def answer(session_id: str, n: int, answers: dict, via: str) -> dict:
    return request("POST", f"/api/v1/design/sessions/{session_id}/answers", {"n": n, "answers": answers, "via": via})


def finalize(session_id: str, draft: str) -> dict:
    return request("POST", f"/api/v1/design/sessions/{session_id}/finalize", {"draft": draft})


def answer_problems(questions: list[dict], answers: dict) -> dict:
    """{question id: problem} by the platform's rule, checked before sending."""
    out = {}
    for q in questions:
        text = (answers.get(q["id"]) or "").strip()
        if not text:
            out[q["id"]] = "No answer."
        elif text.lower().startswith("n/a:"):
            if len(text) < 14:
                out[q["id"]] = 'Say why after "n/a:".'
        elif len(text) < MIN_ANSWER:
            out[q["id"]] = f'Answer in at least {MIN_ANSWER} characters, or start with "n/a:" and say why.'
    return out


def format_check(text: str) -> list[str]:
    """The local design check's findings, as lines for the reviewer."""
    from . import design
    return [f"{f['level']}: {f['section']}: {f['message']}" for f in design.check(text)]


def describe_round(round_: dict) -> str:
    """A round's feedback as plain text: summary, blocking issues, then each question with its quote and why."""
    fb = round_["feedback"]
    lines = [f"Round {round_['n']} of {ROUNDS}: {round_.get('focus', '')}", "", fb.get("summary", "")]
    if fb.get("blocking"):
        lines += ["", "Blocking issues:", *[f"  - {b['issue']}" for b in fb["blocking"]]]
    for q in fb["questions"]:
        lines += ["", f"[{q['id']}] {q['question']}"]
        if q.get("quote"):
            lines.append(f"    about: “{q['quote']}”")
        lines.append(f"    why it matters: {q['why']}")
    return "\n".join(lines)
