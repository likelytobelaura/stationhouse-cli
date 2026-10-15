"""The contributor's inbox: PRs and discussions where someone is waiting on them (GET
/api/v1/me/follow-ups). Feedback on their own open PRs first, then @mentions, then replies. Items clear
themselves on the platform when the thread is merged or closed, or once they answer (a comment, or a
commit to their own PR); `inbox done` hides one until someone comments again.
"""
from datetime import datetime, timezone

from .client import ApiError, request

TODO = {
    "pr-feedback": "Feedback on your PR",
    "mention": "Mentioned you",
    "reply": "Replied to you",
}


def fetch(project: str | None) -> dict:
    """{verified, followUps: [...]} for one project, or all of them when project is None."""
    try:
        return request("GET", "/api/v1/me/follow-ups", query={"project": project} if project else None) or {}
    except ApiError as e:
        if e.status == 404 and not e.body.get("error"):
            raise ApiError("This Station House server doesn't have the inbox yet.", 404) from None
        raise


def waiting(iso: str, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    try:
        hours = int((now - datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() // 3600)
    except ValueError:
        return "a while"
    if hours < 1:
        return "under an hour"
    if hours < 48:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours // 24} days"


def render(data: dict, out=print) -> None:
    if not data.get("verified", True):
        out("Verify your Hugging Face account on your Station House profile, then the inbox can find "
            "the PRs and discussions waiting on you.")
        return
    items = data.get("followUps") or []
    if not items:
        out("Nothing is waiting on you.")
        return
    out(f"{len(items)} follow-up{'s' if len(items) != 1 else ''} waiting on you, most urgent first:")
    for f in items:
        where = f"  ({f['projectId']})" if f.get("projectId") else ""
        task = f"  [{f['taskId']}]" if f.get("taskId") else ""
        out(f"\n#{f['num']}  {f['title']}{task}{where}")
        who = ", ".join(f.get("from", [])[:3]) + (f" +{len(f['from']) - 3}" if len(f.get("from", [])) > 3 else "")
        out(f"    {TODO.get(f.get('kind'), 'Waiting on you')}: {who}, {f['count']} comment{'s' if f['count'] != 1 else ''}, "
            f"waiting {waiting(f['waitingSince'])}")
        latest = " ".join((f.get("latest") or {}).get("text", "").split())
        if latest:
            out(f"    {f['latest']['author']}: {latest[:120]}{'…' if len(latest) > 120 else ''}")
        if f.get("url"):
            out(f"    {f['url']}")
    out("\nAnswer on the PR or discussion and it clears itself. Already handled? stationhouse inbox done <#number>")


def find(items: list[dict], target: str) -> dict:
    """The item `target` names: its full id ("owner/repo#12") or just its number ("12", "#12")."""
    exact = [f for f in items if f["id"] == target]
    if exact:
        return exact[0]
    num = target.lstrip("#")
    matches = [f for f in items if num.isdigit() and f["num"] == int(num)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        ids = ", ".join(f["id"] for f in matches)
        raise ApiError(f"#{num} is in more than one repo ({ids}). Say which: stationhouse inbox done <id>")
    raise ApiError(f"Nothing in your inbox is {target}. See `stationhouse inbox`.")


def done(project: str | None, target: str) -> dict:
    """Marks the item done up to its latest comment, so a newer comment brings it back."""
    item = find(fetch(project).get("followUps") or [], target)
    request("POST", "/api/v1/me/follow-ups/done", {"id": item["id"], "upTo": item["latest"]["at"]})
    return item
