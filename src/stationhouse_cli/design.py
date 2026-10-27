"""`stationhouse design`: an experiment's design file (DESIGN.md, or a ConspiracyBench EXPERIMENT.md)
and the checks run on it before anything is run.

The checks are plain text rules, no model: each of the six stage sections is there and filled in,
Controls names a baseline, and the rest mention what a reviewer would ask for first (a data version,
a split, seeds or reruns, the result that would mean "no"). They catch a missing piece, not a bad
idea; `design review` and the contributor's own agent do the thinking.
"""
import re
from importlib import resources

# (key, heading as written, other headings accepted for it)
STAGES = [
    ("claim", "Claim", ("hypothesis", "research question", "question")),
    ("metric", "Unit and metric", ("unit", "metric", "metrics", "measure")),
    ("controls", "Controls", ("baselines", "baselines and controls", "control")),
    ("data", "Data", ("dataset", "data and cleaning", "inputs")),
    ("noise", "Noise", ("noise floor", "variance", "seeds")),
    ("readout", "Readout", ("pre-registered readout", "pass rule", "success criteria", "what counts as a result")),
]

HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
COMMENT = re.compile(r"<!--.*?-->", re.S)
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME)\b|^\s*\.\.\.\s*$", re.M)

BASELINE = re.compile(r"baseline|random|shuffl|constant|majority|chance|carry[- ]forward|copy|untrained|base model|"
                      r"null model|placebo|no[- ]op|ablat|prior|naive|always[- ]", re.I)
VERSION = re.compile(r"\b[0-9a-f]{7,64}\b|commit|sha|hash|version|v\d|revision|@\s*\w|\d{4}-\d{2}-\d{2}", re.I)
SPLIT = re.compile(r"split|held[- ]out|hold[- ]out|test set|train|leak|contaminat|fresh prompts|unseen", re.I)
FILTERS = re.compile(r"filter|dedup|duplicate|drop|remov|clean|exclud|kept", re.I)
REPEATS = re.compile(r"seed|rerun|re-run|repeat|bootstrap|confidence interval|\bci\b|std|standard (?:error|deviation)|"
                     r"variance|k\s*=\s*\d|\d+\s*(?:runs|samples|draws)|permutation|random[- ]neuron", re.I)
REFUTE = re.compile(r"refut|falsif|null|no effect|fail|reject|inconclusive|would mean|wrong if|not support|negative", re.I)
NUMBER = re.compile(r"\d")


def template() -> str:
    return (resources.files("stationhouse_cli") / "skills" / "experiment-design" / "TEMPLATE.md").read_text()


def guide() -> str:
    """The skill's body, frontmatter dropped: the same guidance a coding agent gets."""
    text = (resources.files("stationhouse_cli") / "skills" / "experiment-design" / "SKILL.md").read_text()
    return text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()


def _stage_for(title: str):
    t = re.sub(r"^[\d.)\s]+|[*_`:]", "", title).strip().lower()
    for key, name, others in STAGES:
        if any(t == n or t.startswith(n + " ") or t.startswith(n + "(") for n in (name.lower(), *others)):
            return key
    return None


def sections(text: str) -> dict:
    """{stage key: section text, comments removed}. A section runs to the next heading at its own
    level or above, so sub-headings inside it stay with it, and the first heading for a stage wins."""
    # Comments go first (keeping their line breaks), so a heading inside one doesn't count.
    lines = COMMENT.sub(lambda m: "\n" * m.group(0).count("\n"), text).splitlines()
    found = {}
    for i, line in enumerate(lines):
        m = HEADING.match(line)
        if not m:
            continue
        key = _stage_for(m.group(2))
        if not key or key in found:
            continue
        level, body = len(m.group(1)), []
        for nxt in lines[i + 1:]:
            h = HEADING.match(nxt)
            if h and len(h.group(1)) <= level:
                break
            body.append(nxt)
        found[key] = "\n".join(body).strip()
    return found


def check(text: str) -> list[dict]:
    """[{level: error|warning, stage, message}]. Any error means the design isn't ready to run."""
    secs = sections(text)
    out = []

    def add(level, stage, message):
        out.append({"level": level, "stage": stage, "message": message})

    names = {key: name for key, name, _ in STAGES}
    for key, name, _ in STAGES:
        body = secs.get(key)
        if body is None:
            add("error", key, f"no `{name}` section")
        elif not body:
            add("error", key, f"`{name}` is empty")
        elif PLACEHOLDER.search(body):
            add("error", key, f"`{name}` still has a TODO/TBD or a bare `...` line")

    def filled(key):
        return bool(secs.get(key)) and not PLACEHOLDER.search(secs[key])

    if filled("controls") and not BASELINE.search(secs["controls"]):
        add("error", "controls", "Controls doesn't name a baseline: add at least one trivial one (constant, majority, "
                                 "copy the input, carry forward, base model, random units) and say what it scores")
    if filled("metric") and not NUMBER.search(secs["metric"]):
        add("warning", "metric", "Unit and metric gives no sample size: say how many units")
    if filled("data"):
        d = secs["data"]
        if not VERSION.search(d):
            add("warning", "data", "Data doesn't pin a version (commit, hash, revision or date)")
        if not SPLIT.search(d):
            add("warning", "data", "Data doesn't say how the evaluation set is kept apart (split, held-out, leakage)")
        if not FILTERS.search(d):
            add("warning", "data", "Data doesn't list its filters or cleaning steps (say 'none' if there are none)")
    if filled("noise") and not REPEATS.search(secs["noise"]):
        add("warning", "noise", "Noise doesn't say how it's estimated (seeds, reruns, bootstrap, random-unit baseline)")
    if filled("readout"):
        r = secs["readout"]
        if not REFUTE.search(r):
            add("warning", "readout", "Readout doesn't say which result would refute the claim")
        if not NUMBER.search(r):
            add("warning", "readout", "Readout has no number: give the threshold or effect size that counts")
    order = {k: i for i, (k, _, _) in enumerate(STAGES)}
    out.sort(key=lambda f: (f["level"] != "error", order[f["stage"]]))
    for f in out:
        f["section"] = names[f["stage"]]
    return out


REVIEW_PROMPT = (
    "Review this experiment design as a skeptical reviewer, before it runs. Don't look up tasks. For each of "
    "Claim, Unit and metric, Controls, Data, Noise and Readout: what is missing or weak, and one concrete fix. "
    "Then answer: what is the dumbest method that would score well here, and is it controlled for? What else "
    "could explain a positive result? Could evaluation data leak into what is tuned or written? Is the "
    "expected effect larger than the noise? Be brief and specific; plain text, no tables.\n\n"
)


def review_question(text: str, limit: int) -> tuple[str, bool]:
    """The question sent to the assistant: the prompt plus the design, squeezed to fit `limit`
    characters. Returns (question, truncated)."""
    body = COMMENT.sub("", text)
    body = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", body)).strip()
    room = limit - len(REVIEW_PROMPT) - len("DESIGN:\n")
    truncated = len(body) > room
    if truncated:
        # The six stages come first; "How to run" and the rest are what gets cut.
        secs = sections(text)
        core = "\n\n".join(f"## {name}\n{secs.get(key, '(missing)')}" for key, name, _ in STAGES)
        body = re.sub(r"[ \t]+", " ", core)[:room].rstrip()
    return REVIEW_PROMPT + "DESIGN:\n" + body, truncated
