import argparse
import getpass
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from . import __version__, config, explore as explore_mod, llm, work
from .auth import AuthError, current_credentials, login, normalize_email
from .client import ApiError, request
from .store import clear_credentials, load_credentials

TASK_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"


def _emit(args, data, human):
    if args.json:
        print(json.dumps(data, indent=2))
    else:
        human(data)


def _ago(person):
    if person.get("stale"):
        return "stale"
    return "active"


def _people_line(p):
    prs = ", ".join(f"#{n}" for n in p.get("prNums") or [])
    return f"  {p['hfUsername']}  ({_ago(p)}{', ' + prs if prs else ''})"


def cmd_login(args):
    email = normalize_email(args.email or input("Station House email: "))
    password = getpass.getpass("Password: ")
    creds = login(email, password)
    print(f"Signed in as {creds['email']}.")


def cmd_logout(args):
    print("Signed out." if clear_credentials() else "You weren't signed in.")


def cmd_whoami(args):
    creds = current_credentials()
    me = request("GET", "/api/v1/me")
    _emit(args, {"email": creds["email"], **(me or {})}, lambda d: print(
        f"{d['email']}" + (f"  HF: {d['hfUsername']}" if d.get("hfUsername") else "  (no verified HF username)")
        + ("  [admin]" if d.get("isAdmin") else "")))


def cmd_unclaimed(args):
    out = request("GET", "/api/v1/tasks/unclaimed", query={"project": args.project})

    def human(d):
        tasks = d.get("tasks", [])
        if not tasks:
            print("Nothing unclaimed right now.")
        for t in tasks:
            print(f"{t['taskId']}  {t['title']}")
            if t.get("summary"):
                print(f"    {t['summary']}")
    _emit(args, out, human)


def cmd_who(args):
    out = request("GET", f"/api/v1/tasks/{args.task_id}/people")

    def human(d):
        print(f"{d.get('title', args.task_id)} ({args.task_id})")
        people = d.get("people", [])
        print("\n".join(_people_line(p) for p in people) if people else "  nobody yet")
    _emit(args, out, human)


def cmd_mine(args):
    out = request("GET", "/api/v1/me/claims")

    def human(d):
        claims = d.get("claims", [])
        if not claims:
            print("You haven't claimed anything.")
        for c in claims:
            print(f"{c['taskId']}  {c.get('status', 'ACTIVE').lower()}{'  (stale)' if c.get('stale') else ''}")
    _emit(args, out, human)


CARD_ADDED = {
    "added": "It's on your dashboard now.",
    "unchanged": "It was already on your dashboard.",
    "failed": "Warning: you're on it, but I couldn't put its card on your dashboard. Add it from the website.",
}
CARD_REMOVED = {
    "removed": "Taken off your dashboard.",
    "unchanged": "It wasn't on your dashboard.",
    "failed": "Warning: released, but I couldn't take its card off your dashboard. Remove it from the website.",
}


def cmd_claim(args):
    out = request("POST", "/api/v1/claims", {"taskId": args.task_id})

    def human(d):
        print(f"You're on {args.task_id}.")
        if d.get("dashboard") in CARD_ADDED:
            print(CARD_ADDED[d["dashboard"]])
        others = [p for p in d.get("people", []) if not p.get("you")]
        if others:
            print("Also working on it:")
            print("\n".join(_people_line(p) for p in others))
    _emit(args, out, human)


def cmd_release(args):
    out = request("POST", "/api/v1/claims/release", {"taskId": args.task_id})

    def human(d):
        print(f"Released {args.task_id}.")
        if d.get("dashboard") in CARD_REMOVED:
            print(CARD_REMOVED[d["dashboard"]])
    _emit(args, out, human)


def cmd_task_create(args):
    title = " ".join(args.title)
    out = request("POST", "/api/v1/tasks", {"parentTaskId": args.parent, "title": title, "summary": args.summary})

    def human(d):
        if d.get("created"):
            print(f"Proposed \"{title}\" as a subtask of {args.parent}.")
            print("An admin has to approve it. Once they do it's created, you're put on it, and it shows on your dashboard.")
        else:
            print(f"\"{title}\" is already proposed under {args.parent} and waiting for an admin. Nothing new was filed.")
    _emit(args, out, human)


def cmd_task_request(args):
    title = " ".join(args.title)
    body = {"title": title, "description": args.description, "projectId": args.project}
    if args.atomic_unit:
        body["atomicUnit"] = args.atomic_unit
    out = request("POST", "/api/v1/tasks/request", body)

    def human(d):
        if not d.get("created"):
            print(f"You've already requested \"{title}\". Nothing new was filed.")
            return
        print(f"Requested \"{title}\". The maintainers decide whether it becomes a task.")
        if not d.get("notified"):
            print("Warning: it's recorded, but the email to the maintainers didn't go out. Mention it to one of them.")
    _emit(args, out, human)


def cmd_task_proposals(args):
    out = request("GET", "/api/v1/proposals")

    def human(d):
        rows = d.get("proposals", [])
        if not rows:
            print("No proposals waiting.")
        for p in rows:
            who = p.get("hfUsername") or "unknown"
            via = f"PR #{p['prNum']}" if p.get("prNum") else "CLI"
            print(f"{p['proposalId']}\n    under {p['parentTaskId']}: {p['title']}  (by {who}, {via})")
            if p.get("summary"):
                print(f"    {p['summary']}")
    _emit(args, out, human)


def cmd_task_approve(args):
    out = request("POST", "/api/v1/proposals/approve", {"proposalId": args.proposal_id})

    def human(d):
        print(f"Created {d['taskId']}.")
        if d.get("claimed"):
            print("The proposer is on it" + (f"; {CARD_ADDED[d['dashboard']].lower()}" if d.get("dashboard") in CARD_ADDED else "."))
        else:
            print("Nobody was credited (the proposal had no Hugging Face username).")
    _emit(args, out, human)


def cmd_task_reject(args):
    out = request("POST", "/api/v1/proposals/reject", {"proposalId": args.proposal_id})
    _emit(args, out, lambda d: print("Rejected."))


def _tree(args):
    return request("GET", "/api/v1/tasks", query={"project": args.project})


def _answer(question, tree, args):
    """One question: the deterministic answer, plus the local model's words for open-ended questions."""
    result = explore_mod.explore(question, tree)
    by_id = {t["taskId"]: t for t in tree["tasks"]}
    result["question"] = question
    result["answer"], result["model"], result["modelNote"] = None, None, None
    if result["intent"] == "search" and result["tasks"] and not args.no_llm:
        text, why = llm.ask_local(question, result["tasks"], set(by_id), url=config.ollama_url(), model=config.model())
        result["answer"], result["modelNote"] = text, why
        result["model"] = config.model() if text else None
    return result, by_id


def _print_answer(result, by_id):
    if result["answer"]:
        print(f"{result['answer']}\n    (local model {result['model']}; the tasks it used are below)\n")
    elif result["modelNote"] and "no local model" not in result["modelNote"]:
        print(f"({result['modelNote']})\n")
    if result["note"]:
        print(result["note"])
    for t in result["tasks"]:
        print(explore_mod.render_task(t, by_id))


def cmd_explore(args):
    tree = _tree(args)
    question = " ".join(args.question).strip()
    if question:
        result, by_id = _answer(question, tree, args)
        _emit(args, result, lambda d: _print_answer(result, by_id))
        return
    if args.json:
        raise ApiError("--json needs a question: stationhouse explore --json \"who is on scoring?\"")
    print("Explore mode. Ask about the tasks: \"who is working on the scorer?\", \"what is unclaimed?\", "
          "\"what is ada on?\". `refresh` reloads, `exit` quits.")
    while True:
        try:
            line = input("explore> ").strip()
        except EOFError:
            print()
            return
        if line in ("exit", "quit", "q"):
            return
        if line == "refresh":
            tree = _tree(args)
            print(f"Reloaded {len(tree['tasks'])} tasks.")
        elif line:
            result, by_id = _answer(line, tree, args)
            _print_answer(result, by_id)


def cmd_find_work(args):
    tree = _tree(args)
    out = work.suggest(tree, " ".join(args.about), args.limit)
    by_id = {t["taskId"]: t for t in tree["tasks"]}

    def human(d):
        picks = d["picks"]
        if not picks:
            print("Nothing open to pick up right now." if d["open"] == 0 else "Nothing open matches that.")
            print("If there's work you'd like to do that isn't here: stationhouse task request \"title\" -d \"what and why\"")
            return
        if not d["matched"]:
            print("Nothing matched that, so these are the open tasks that most need someone:\n")
        first = picks[0]["task"]
        print(f"Do this one: {first['taskId']}  {first['title']}")
        for r in picks[0]["reasons"]:
            print(f"  - {r}")
        print(explore_mod.render_task(first, by_id).split("\n", 1)[1])
        print(f"\nTo take it:  stationhouse task claim {first['taskId']}")
        if len(picks) > 1:
            print("\nOr:")
            for p in picks[1:]:
                print(f"  {p['task']['taskId']}  {p['task']['title']}  ({p['reasons'][0]})")
    _emit(args, {"picks": [{"taskId": p["task"]["taskId"], "title": p["task"]["title"], "score": p["score"],
                            "reasons": p["reasons"], "people": p["task"]["people"]} for p in out["picks"]],
                 "matched": out["matched"], "open": out["open"]}, lambda _d: human(out))


def cmd_assign(args):
    out = request("POST", "/api/v1/claims/assign", {"taskId": args.task_id, "hfUsername": args.handle.lstrip("@")})
    _emit(args, out, lambda d: print(f"Assigned {args.handle.lstrip('@')} to {args.task_id}."))


def cmd_skills_install(args):
    dest = Path(args.dir).expanduser() if args.dir else Path.home() / ".claude" / "skills"
    src = resources.files("stationhouse_cli") / "skills" / "stationhouse"
    target = dest / "stationhouse"
    target.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src / "SKILL.md") as f:
        shutil.copyfile(f, target / "SKILL.md")
    print(f"Installed the stationhouse skill to {target}")


def build_parser():
    p = argparse.ArgumentParser(prog="stationhouse", description="Station House from the terminal.")
    p.add_argument("--version", action="version", version=f"stationhouse-cli {__version__}")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help, task=False, under=None, aliases=()):
        sp = (under or sub).add_parser(name, help=help, aliases=list(aliases))
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
        if task:
            sp.add_argument("task_id", metavar="task-id")
        sp.set_defaults(fn=fn)
        return sp

    def project(sp):
        sp.add_argument("--project", default=config.DEFAULT_PROJECT)
        return sp

    add("login", cmd_login, "sign in with your Station House email").add_argument("--email")
    add("logout", cmd_logout, "forget the stored session")
    add("whoami", cmd_whoami, "who you are signed in as")
    project(add("unclaimed", cmd_unclaimed, "open tasks nobody is on"))
    add("who", cmd_who, "who is working on a task", task=True)
    add("mine", cmd_mine, "tasks you have claimed")
    add("claim", cmd_claim, "claim a task (same as `task claim`)", task=True)
    add("release", cmd_release, "release a task you claimed (same as `task release`)", task=True)
    a = add("assign", cmd_assign, "admins only: put someone on a task", task=True)
    a.add_argument("handle", help="their Hugging Face username")

    t = sub.add_parser("task", help="claim, create or request tasks")
    tsub = t.add_subparsers(dest="task_cmd", required=True)
    add("claim", cmd_claim, "claim an existing task or subtask; it shows on your dashboard", task=True, under=tsub)
    add("release", cmd_release, "release a task you claimed; it comes off your dashboard", task=True, under=tsub)
    c = add("create", cmd_task_create, "propose a SUBTASK of an existing task (an admin approves it)", under=tsub)
    c.add_argument("parent", metavar="parent-task-id", help="the existing task it belongs under")
    c.add_argument("title", nargs="+")
    c.add_argument("-s", "--summary", required=True, help="what the subtask is and when it's done")
    r = add("request", cmd_task_request, "ask for a new top-level task that isn't a subtask of an existing one", under=tsub)
    r.add_argument("title", nargs="+")
    r.add_argument("-d", "--description", required=True, help="what the task is and why it's worth doing")
    r.add_argument("--atomic-unit", help="the smallest unit of contribution someone could make")
    r.add_argument("--project", default=config.DEFAULT_PROJECT)
    add("proposals", cmd_task_proposals, "admins only: subtask proposals waiting for review", under=tsub)
    for name, fn, help in (("approve", cmd_task_approve, "admins only: create the proposed subtask"),
                           ("reject", cmd_task_reject, "admins only: decline a proposed subtask")):
        add(name, fn, help, under=tsub).add_argument("proposal_id", metavar="proposal-id")

    e = project(add("explore", cmd_explore, "ask questions about the tasks and who is on them (no question: explore mode)"))
    e.add_argument("question", nargs="*", help="e.g. who is working on the scorer?")
    e.add_argument("--no-llm", action="store_true", help="never use a local model, even if one is running")
    w = project(add("find-work", cmd_find_work, "pick an open task for you to do", aliases=["work"]))
    w.add_argument("about", nargs="*", help="what you're into, e.g. adversarial prompts")
    w.add_argument("--limit", type=int, default=3, help="how many tasks to suggest (default 3)")
    add("skills-install", cmd_skills_install, "install the agent skill").add_argument("--dir", help="skills directory (default ~/.claude/skills)")
    return p


def main(argv=None):
    import re
    args = build_parser().parse_args(argv)
    for attr in ("task_id", "parent"):
        if hasattr(args, attr) and not re.match(TASK_ID, getattr(args, attr)):
            print(f"error: {getattr(args, attr)!r} isn't a valid task id", file=sys.stderr)
            return 2
    try:
        args.fn(args)
    except (AuthError, ApiError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except EOFError:
        # No terminal to read from (a piped or background shell): say so, don't dump a traceback.
        print("error: this needs a terminal to type into. Run it in a normal terminal window.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
