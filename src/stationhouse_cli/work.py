"""`find-work`: look through the open tasks and say which one to do next, and why.

Pure and deterministic: the pick comes from who is already on each task and, if you say what you're
into, how closely a task matches. No model decides it, so the reason given is the real reason.
"""
from .explore import rank


def _live_others(task: dict) -> tuple[int, int]:
    others = [p for p in task["people"] if not p.get("you")]
    stale = sum(1 for p in others if p.get("stale"))
    return len(others) - stale, stale


def suggest(tree: dict, about: str = "", limit: int = 3) -> dict:
    """{"picks": [{task, score, reasons}], "matched": bool}. Open tasks you aren't already on, best first.

    Nobody on it beats only-stale beats shared; a subtask (a bounded piece) beats a parent that still
    has open subtasks (do those instead); with `about`, tasks that match it come first and tasks that
    don't match at all are left out, unless nothing matches, in which case you get the open ones with matched=False.
    """
    tasks = tree["tasks"]
    by_id = {t["taskId"]: t for t in tasks}
    candidates = [t for t in tasks if t["available"] and not t["closed"] and not t["mine"]]
    relevance: dict[str, float] = {}
    matched = True
    if about.strip():
        ranked = rank(about, candidates)
        if ranked:
            best = ranked[0][0]
            relevance = {t["taskId"]: s / best for s, t in ranked}
            candidates = [t for t in candidates if t["taskId"] in relevance]
        else:
            matched = False
    picks = []
    for t in candidates:
        score, reasons = 0.0, []
        live_n, stale_n = _live_others(t)
        if live_n == 0 and stale_n == 0:
            score += 3.0
            reasons.append("nobody is on it yet")
        elif live_n == 0:
            score += 2.0
            reasons.append(f"only stale claims ({stale_n}), so it needs someone new")
        else:
            score -= 0.75 * min(live_n, 4)
            reasons.append(f"{live_n} {'person is' if live_n == 1 else 'people are'} already on it, so you'd be working alongside them")
        if t.get("parentTaskId") in by_id:
            score += 1.0
            reasons.append(f"a subtask of {by_id[t['parentTaskId']]['title']}, so a bounded piece of work")
        open_kids = [c for c in t.get("childTaskIds", []) if c in by_id and by_id[c]["available"] and not by_id[c]["closed"]]
        if open_kids:
            score -= 1.5
            reasons.append(f"it has {len(open_kids)} open subtask{'s' if len(open_kids) != 1 else ''}, which are the smaller way in")
        if t["taskId"] in relevance:
            score += 4.0 * relevance[t["taskId"]]
            reasons.append(f"matches \"{about.strip()}\"")
        picks.append({"task": t, "score": round(score, 2), "reasons": reasons})
    picks.sort(key=lambda p: (-p["score"], p["task"]["taskId"]))
    return {"picks": picks[:limit], "matched": matched, "open": len(picks)}
