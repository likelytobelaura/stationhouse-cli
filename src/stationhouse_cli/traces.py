"""Opt-in agent transcripts for a design session (`stationhouse design attach-trace`, or the consent
question after design_finalize). Finds the coding agent's session log that mentions the design
session, cuts it down to the conversation (no file contents, tool output trimmed), removes anything
that looks like a key, token or email, and uploads it gzipped. Only Station House admins can read it.
The platform's design-traces.ts runs the same secret patterns again on arrival.
"""
import base64
import gzip
import json
import re
from pathlib import Path

from .client import request

CONSENT_VERSION = "trace-consent-v1"
TEXT_LIMIT = 20_000
TOOL_INPUT_LIMIT = 2_000
TOOL_RESULT_LIMIT = 500
# Claude Code keeps whole file contents and command output here; the conversation doesn't need them.
DROP_KEYS = {"toolUseResult", "snapshot", "file", "originalFile", "structuredPatch"}

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bhf_[A-Za-z0-9]{20,}"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bcfat_[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
]


def scrub(text: str) -> tuple[str, int]:
    count = 0
    for pattern in SECRET_PATTERNS:
        text, n = pattern.subn("[REDACTED]", text)
        count += n
    return text, count


def claude_code_dir(cwd) -> Path:
    """Where Claude Code keeps a project's session logs: ~/.claude/projects/<path with / and . as ->."""
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(Path(cwd).resolve()))
    return Path.home() / ".claude" / "projects" / slug


def find_log(session_id: str, cwd) -> tuple[Path, str] | None:
    """(log file, agent) for the newest Claude Code or Codex session log that mentions the design session."""
    candidates = [(p, "claude-code") for p in claude_code_dir(cwd).glob("*.jsonl")]
    codex = Path.home() / ".codex" / "sessions"
    if codex.is_dir():
        candidates += [(p, "codex") for p in codex.rglob("rollout-*.jsonl")]
    for path, agent in sorted(candidates, key=lambda c: c[0].stat().st_mtime, reverse=True)[:200]:
        try:
            with path.open(encoding="utf-8", errors="replace") as f:
                if any(session_id in line for line in f):
                    return path, agent
        except OSError:
            continue
    return None


def _cut(value, counts: dict, in_tool_result=False):
    if isinstance(value, dict):
        out = {}
        tool_result = value.get("type") in ("tool_result", "function_call_output")
        for k, v in value.items():
            if k in DROP_KEYS:
                counts["droppedFields"] = counts.get("droppedFields", 0) + 1
                continue
            if value.get("type") in ("tool_use", "function_call") and k in ("input", "arguments"):
                text = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
                if len(text) > TOOL_INPUT_LIMIT:
                    counts["toolInputsTrimmed"] = counts.get("toolInputsTrimmed", 0) + 1
                out[k] = text[:TOOL_INPUT_LIMIT]
                continue
            out[k] = _cut(v, counts, in_tool_result or tool_result)
        return out
    if isinstance(value, list):
        return [_cut(v, counts, in_tool_result) for v in value]
    if isinstance(value, str):
        limit = TOOL_RESULT_LIMIT if in_tool_result else TEXT_LIMIT
        if len(value) > limit:
            counts["toolResultsTrimmed" if in_tool_result else "textsTrimmed"] = counts.get(
                "toolResultsTrimmed" if in_tool_result else "textsTrimmed", 0) + 1
            return value[:limit] + " [...]"
    return value


def redact(lines) -> tuple[str, dict]:
    """The transcript cut down and scrubbed, as JSONL, with counts of what was removed."""
    counts: dict = {}
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            counts["unreadableLines"] = counts.get("unreadableLines", 0) + 1
            continue
        out.append(json.dumps(_cut(entry, counts), ensure_ascii=False))
    text, secrets = scrub("\n".join(out) + "\n")
    counts["secrets"] = secrets
    counts["lines"] = len(out)
    return text, counts


def preview(text: str, lines: int = 12) -> str:
    """A readable glimpse: the first few user and assistant messages."""
    shown = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        msg = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        role = msg.get("role") or entry.get("type")
        content = msg.get("content")
        if isinstance(content, list):
            content = " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
        if role in ("user", "assistant") and isinstance(content, str) and content.strip():
            shown.append(f"{role}: {content.strip()[:200]}")
        if len(shown) >= lines:
            break
    return "\n".join(shown)


def prepare(session_id: str, cwd, path: str | None = None) -> dict:
    """{path, agent, text, counts} for the transcript to attach (redacted, not yet sent)."""
    if path:
        found = (Path(path).expanduser(), "codex" if "/.codex/" in str(path) else "claude-code" if "/.claude/" in str(path) else "other")
    else:
        found = find_log(session_id, cwd)
        if not found:
            raise FileNotFoundError(f"No Claude Code or Codex session log here mentions {session_id}. Pass --file.")
    log, agent = found
    text, counts = redact(log.read_text(encoding="utf-8", errors="replace").splitlines())
    return {"path": str(log), "agent": agent, "text": text, "counts": counts}


def upload(session_id: str, prepared: dict, consent: str) -> dict:
    data = gzip.compress(prepared["text"].encode("utf-8"))
    res = request("POST", f"/api/v1/design/sessions/{session_id}/traces", {
        "agent": prepared["agent"],
        "consentVersion": f"{CONSENT_VERSION}:{consent}",
        "redaction": prepared["counts"],
        "gzipBase64": base64.b64encode(data).decode(),
    })
    return {"traceId": (res or {}).get("traceId"), "bytes": len(data), "path": prepared["path"], "redaction": prepared["counts"]}


def attach(session_id: str, cwd, consent: str, path: str | None = None) -> dict:
    """Prepare and upload in one step, for callers that already asked the contributor."""
    return upload(session_id, prepare(session_id, cwd, path), consent)
