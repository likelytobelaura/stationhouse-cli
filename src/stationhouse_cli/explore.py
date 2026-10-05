"""Ask questions of the task tree, and find the work to do next.

Everything here is pure: it reads the tree that `GET /api/v1/tasks` returns (tasks with their
subtasks, summaries and the people on them) and answers from it. Who is working on what is always
read off the tree, never recalled by a model; a local model (llm.py) is only used to talk about what
a task *is*, and its answer is checked against the tree before it is shown.
"""
import math
import re

STOPWORDS = set("""a an and are as at be but by can do does for from has have how i if in is it its me my of on or
so that the their there these this to us was we what when where which who whom why will with would you your
about any all anyone anybody else other others some someone somebody task tasks work working worked doing
unclaimed claimed nobody open available need needs help grabs subtask subtasks sub children pieces breakdown parts part under
claim claiming taking person people""".split())


def stem(word: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[: -len(suffix)]
    return word


def tokens(text: str) -> list[str]:
    return [stem(w) for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if w not in STOPWORDS and len(w) > 1]


def _fields(task: dict) -> list[tuple[float, list[str]]]:
    """(weight, tokens) for what a task is about: its name counts most, then what it says it is."""
    return [
        (3.0, tokens(task["title"])),
        (3.0, tokens(task["taskId"].replace("-", " "))),
        (1.0, tokens(task.get("summary", ""))),
        (0.5, tokens(" ".join(task.get("scope") or []))),
        (0.5, tokens(task.get("completionCriteria", ""))),
    ]


def rank(question: str, tasks: list[dict]) -> list[tuple[float, dict]]:
    """Tasks that share words with the question, best first (score > 0 only). Rarer words count more."""
    q = set(tokens(question))
    if not q:
        return []
    docs = [(t, _fields(t)) for t in tasks]
    df = {w: sum(1 for _, fs in docs if any(w in toks for _, toks in fs)) for w in q}
    out = []
    for task, fields in docs:
        score = 0.0
        for w in q:
            if df[w] == 0:
                continue
            idf = math.log(1 + len(docs) / df[w])
            score += idf * max((wt for wt, toks in fields if w in toks), default=0.0)
        if score > 0:
            out.append((score, task))
    return sorted(out, key=lambda p: (-p[0], p[1]["taskId"]))


# ---------------------------------------------------------------- explore

MINE = re.compile(r"\b(i|my|mine|me|i'm|im)\b")
UNCLAIMED = re.compile(r"\b(unclaimed|nobody|no ?one|needs? (?:a )?(?:hand|help|someone|people|volunteers?)|open|available|up for grabs)\b")
WHO = re.compile(r"\b(who|who's|whos|anyone|anybody|someone|somebody|working on|taking|claimed)\b")
SUBTASKS = re.compile(r"\b(subtasks?|sub-tasks?|children|pieces|breakdown|broken down|parts?|under)\b")


def handles_in(tree_tasks: list[dict]) -> dict[str, str]:
    return {p["hfUsername"].lower(): p["hfUsername"] for t in tree_tasks for p in t["people"]}


def mentioned_handles(question: str, tasks: list[dict]) -> list[str]:
    """Handles named in the question: anyone on the tree by name, or anything written @like-this."""
    known = handles_in(tasks)
    q = question.lower()
    found = [orig for low, orig in known.items() if re.search(rf"(?<![\w.-]){re.escape(low)}(?![\w.-])", q)]
    for at in re.findall(r"@([A-Za-z0-9][A-Za-z0-9_.-]*)", question):
        if at.lower() not in {h.lower() for h in found}:
            found.append(known.get(at.lower(), at))
    return found


def intent(question: str, tasks: list[dict]) -> tuple[str, list[str]]:
    """(kind, handles): what is being asked, decided by patterns, not by a model."""
    q = question.lower()
    handles = mentioned_handles(question, tasks)
    if handles:
        return "person", handles
    if MINE.search(q) and re.search(r"working|claimed|doing|have|on\b|signed|assigned", q):
        return "mine", []
    if UNCLAIMED.search(q):
        return "unclaimed", []
    if WHO.search(q):
        return "who", []
    if SUBTASKS.search(q):
        return "subtasks", []
    return "search", []


def live(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t["available"] and not t["closed"] and t.get("workable", True)]


def render_people(task: dict) -> str:
    if not task["people"]:
        return "nobody yet"
    return ", ".join(
        f"{p['hfUsername']}{' (you)' if p.get('you') else ''} ({'stale' if p.get('stale') else 'active'}"
        + (", " + ", ".join(f"#{n}" for n in p["prNums"]) if p.get("prNums") else "") + ")"
        for p in task["people"])


def render_task(task: dict, by_id: dict[str, dict]) -> str:
    kind = ""
    if task.get("parentTaskId") and task["parentTaskId"] in by_id:
        kind = f"  [subtask of {by_id[task['parentTaskId']]['title']}]"
    elif task.get("childTaskIds"):
        kind = f"  [{len(task['childTaskIds'])} subtask{'s' if len(task['childTaskIds']) != 1 else ''}]"
    state = "  (closed)" if task["closed"] else "" if task["available"] else "  (not open yet)"
    lines = [f"{task['taskId']}  {task['title']}{kind}{state}"]
    if task.get("summary"):
        lines.append(f"    {task['summary']}")
    lines.append(f"    On it: {render_people(task)}")
    if task.get("pendingSubtasks"):
        lines.append("    Proposed subtasks awaiting approval: " + "; ".join(
            f"{p['title']}" + (f" ({p['hfUsername']})" if p.get("hfUsername") else "") for p in task["pendingSubtasks"]))
    return "\n".join(lines)


def explore(question: str, tree: dict) -> dict:
    """The deterministic answer: {intent, handles, tasks, note}. `tasks` are tree nodes, best first."""
    tasks = tree["tasks"]
    by_id = {t["taskId"]: t for t in tasks}
    kind, handles = intent(question, tasks)
    note = ""
    if kind == "person":
        want = {h.lower() for h in handles}
        hits = [t for t in tasks if any(p["hfUsername"].lower() in want for p in t["people"])]
        note = (f"{', '.join(handles)} {'is' if len(handles) == 1 else 'are'} on {len(hits)} task{'s' if len(hits) != 1 else ''}."
                if hits else f"I don't see {', '.join(handles)} on any task.")
        chosen = hits
    elif kind == "mine":
        chosen = [t for t in tasks if t["mine"]]
        note = f"You're on {len(chosen)} task{'s' if len(chosen) != 1 else ''}." if chosen else (
            "You aren't on any task." + ("" if tree.get("you") else " (No verified Hugging Face username on your account, so I can't tell.)"))
    elif kind == "unclaimed":
        chosen = [t for t in live(tasks) if not t["people"]]
        topical = [t for _, t in rank(question, chosen)]  # "what scoring work is unclaimed": only those about scoring
        if topical:
            chosen = topical
        note = f"{len(chosen)} open task{'s' if len(chosen) != 1 else ''} nobody is on." if chosen else "Every open task has someone on it."
    else:
        ranked = rank(question, tasks)
        if kind in ("who", "subtasks"):
            top = ranked[0][0] if ranked else 0
            chosen = [t for s, t in ranked if s >= 0.6 * top][:3]
            if kind == "subtasks":
                # The parent with its children, or the child with its siblings.
                family: list[dict] = []
                for t in chosen[:1]:
                    root = by_id.get(t.get("parentTaskId") or "", t)
                    family = [root] + [by_id[c] for c in root["childTaskIds"] if c in by_id]
                chosen = family
            note = "" if chosen else "I couldn't tell which task you mean; try its name or id."
        else:
            chosen = [t for _, t in ranked[:5]]
            note = "" if chosen else "Nothing in the task list matches that."
    return {"intent": kind, "handles": handles, "tasks": chosen, "note": note}
