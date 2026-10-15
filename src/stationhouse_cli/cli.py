import argparse
import contextlib
import difflib
import getpass
import io
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from . import __version__, chat, explore as explore_mod, inbox, projects, train, work
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


def cmd_projects(args):
    out = {"projects": projects.list_projects(), "current": projects.chosen()}

    def human(d):
        if not d["projects"]:
            print("You can't see any projects yet.")
        for p in d["projects"]:
            mark = "*" if p["id"] == d["current"] else " "
            print(f"{mark} {p['id']}  {p['title']}")
            if p.get("summary"):
                print(f"    {p['summary'][:160]}")
        if len(d["projects"]) > 1 and not d["current"]:
            print("\nPick one to work in: stationhouse project use <id>")
    _emit(args, out, human)


def cmd_project(args):
    if args.action == "use":
        if not args.project_id:
            raise ApiError("Which one? stationhouse project use <id>  (see `stationhouse projects`)")
        project = projects.use(args.project_id)
        _emit(args, project, lambda p: print(f"Working in {p['id']} ({p['title']}) from now on."))
        return
    if args.action == "clear":
        had = projects.forget()
        _emit(args, {"cleared": had}, lambda d: print("Forgot your project; commands will ask again." if had else "No project was picked."))
        return
    out = request("GET", projects.project_path(projects.resolve(args.project_id)))

    def human(d):
        p, o = d["project"], d.get("overview") or {}
        print(f"{p['title']} ({p['id']})")
        if o.get("researchQuestion"):
            print(f"  {o['researchQuestion']}")
        if o.get("currentMilestone"):
            print(f"  Now: {o['currentMilestone']}")
        for r in (o.get("resources") or [])[:5]:
            print(f"  {r['label']}: {r['href']}")
    _emit(args, out, human)


def cmd_unclaimed(args):
    out = request("GET", projects.project_path(projects.resolve(args.project), "/unclaimed"))

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
    _, out = projects.task_call("GET", args.task_id, "/people", args.project)

    def human(d):
        print(f"{d.get('title', args.task_id)} ({args.task_id})")
        people = d.get("people", [])
        print("\n".join(_people_line(p) for p in people) if people else "  nobody yet")
    _emit(args, out, human)


def _context_person(p):
    claim = p.get("claim")
    role = ("released" if claim["released"] else "on it") if claim else None
    did = [f"{n} {w}{'s' if n != 1 else ''}" for n, w in ((p.get("threads", 0), "PR/thread"), (p.get("comments", 0), "comment")) if n]
    return f"  {p['user']}  ({', '.join([r for r in [role] if r] + did) or 'involved'})"


def cmd_context(args):
    _, out = projects.task_call("GET", args.task_id, "/context", args.project)

    def human(d):
        t = d["task"]
        print(f"{t['title']} ({t.get('projectId', '?')}/{t['taskId']})")
        if d.get("parent"):
            print(f"  part of: {d['parent']['taskId']}  {d['parent']['title']}")
        for s in d.get("subtasks", []):
            print(f"  subtask: {s['taskId']}  {s['title']}")
        people = d.get("people", [])
        print("\nPeople:" if people else "\nNobody on it yet.")
        for p in people:
            print(_context_person(p))
        threads = d.get("threads", [])
        if threads:
            print("\nPRs and discussions (most recent activity first):")
        for th in threads:
            kind = "PR" if th.get("kind") == "PR" else "Discussion"
            by = f" by {th['author']}" if th.get("author") else ""
            print(f"  {kind} #{th['num']}  {th['title']}  [{th.get('status') or '?'}]{by}, {th['commentCount']} comments")
            if th.get("url"):
                print(f"    {th['url']}")
            for c in th.get("recentComments", []):
                text = " ".join(c["text"].split())
                print(f"    {c['author']}: {text[:100]}{'…' if len(text) > 100 else ''}")
        for m in d.get("mentions", []):
            print(f"\n  Mentioned by {m['author']} on {m['thread']}: {' '.join(m['text'].split())[:100]}")
    _emit(args, out, human)


def cmd_prs(args):
    out = request("GET", projects.project_path(projects.resolve(args.project), "/prs"), query={"limit": args.limit})

    def human(d):
        prs = d.get("prs", [])
        if not prs:
            print("No open PRs are waiting for review right now.")
        for p in prs:
            seen = f"commented on by {', '.join(p['reviewers'][:3])}" if p.get("reviewers") else "no review yet"
            task = f"  [{p['taskId']}]" if p.get("taskId") else ""
            print(f"#{p['num']}  {p['title']}{task}")
            print(f"    by {p.get('author') or 'unknown'}, {seen}")
            if p.get("url"):
                print(f"    {p['url']}")
    _emit(args, out, human)


def cmd_mine(args):
    out = request("GET", "/api/v1/me/claims")

    def human(d):
        claims = d.get("claims", [])
        if not claims:
            print("You haven't claimed anything.")
        for c in claims:
            where = f"{c['projectId']}/" if c.get("projectId") else ""
            title = f"  {c['title']}" if c.get("title") else ""
            print(f"{where}{c['taskId']}{title}  ({c.get('status', 'ACTIVE').lower()}{', stale' if c.get('stale') else ''})")
    _emit(args, out, human)


def cmd_inbox(args):
    # Everything waiting on you unless a project is named or picked: a reply doesn't care which project you're in.
    project = projects.chosen(args.project)
    if args.action == "done":
        if not args.target:
            raise ApiError("Which one? stationhouse inbox done <#number>  (see `stationhouse inbox`)")
        item = inbox.done(project, args.target)
        _emit(args, {"done": item["id"]}, lambda d: print(
            f"Marked #{item['num']} done. It comes back if someone comments again."))
        return
    _emit(args, inbox.fetch(project), inbox.render)


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
    _, out = projects.task_call("POST", args.task_id, "/claim", args.project)

    def human(d):
        print(f"You're on {d.get('taskId', args.task_id)}.")
        if d.get("dashboard") in CARD_ADDED:
            print(CARD_ADDED[d["dashboard"]])
        others = [p for p in d.get("people", []) if not p.get("you")]
        if others:
            print("Also working on it:")
            print("\n".join(_people_line(p) for p in others))
    _emit(args, out, human)


def cmd_release(args):
    _, out = projects.task_call("POST", args.task_id, "/release", args.project)

    def human(d):
        print(f"Released {args.task_id}.")
        if d.get("dashboard") in CARD_REMOVED:
            print(CARD_REMOVED[d["dashboard"]])
    _emit(args, out, human)


def cmd_task_create(args):
    title = " ".join(args.title)
    _, out = projects.task_call("POST", args.parent, "/subtasks", args.project, {"title": title, "summary": args.summary})

    def human(d):
        if d.get("created"):
            print(f"Proposed \"{title}\" as a subtask of {args.parent}.")
            print("An admin has to approve it. Once they do it's created, you're put on it, and it shows on your dashboard.")
        else:
            print(f"\"{title}\" is already proposed under {args.parent} and waiting for an admin. Nothing new was filed.")
    _emit(args, out, human)


def cmd_task_request(args):
    title = " ".join(args.title)
    body = {"title": title, "description": args.description}
    if args.atomic_unit:
        body["atomicUnit"] = args.atomic_unit
    project = projects.resolve(args.project)
    out = request("POST", projects.project_path(project, "/requests"), body)

    def human(d):
        if not d.get("created"):
            print(f"You've already requested \"{title}\". Nothing new was filed.")
            return
        print(f"Requested \"{title}\" in {project}. The maintainers decide whether it becomes a task.")
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
    return request("GET", projects.project_path(projects.resolve(args.project), "/tasks"))


def _answer(question, tree):
    """The deterministic lookup: which tasks a question is about, and who is on them. No model."""
    result = explore_mod.explore(question, tree)
    result["question"] = question
    return result, {t["taskId"]: t for t in tree["tasks"]}


def _print_answer(result, by_id):
    if result["note"]:
        print(result["note"])
    for t in result["tasks"]:
        print(explore_mod.render_task(t, by_id))


def cmd_explore(args):
    question = " ".join(args.question).strip()
    if not question:
        raise ApiError("explore needs a question, e.g. stationhouse explore \"who is on scoring?\". "
                       "To ask in your own words, just run `stationhouse` and chat with the assistant.")
    tree = _tree(args)
    result, by_id = _answer(question, tree)
    _emit(args, result, lambda d: _print_answer(result, by_id))


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


def _offline_answer(project_fn):
    """The chat's fallback when the assistant can't answer: `explore` over the current project's tasks."""
    def answer(question):
        try:
            tree = request("GET", projects.project_path(projects.resolve(project_fn()), "/tasks"))
            # Deterministic only: a quick, honest answer from the list, never a slow local model.
            result, by_id = _answer(question, tree)
            _print_answer(result, by_id)
        except (AuthError, ApiError) as e:
            print(f"error: {e}")
    return answer


def _run_in_chat(argv):
    """A /command typed in the chat: the normal CLI, minus the exit when argparse rejects it. Parsed
    strictly: in the chat, plain lines are the questions and a mistyped /command is an error."""
    try:
        main(argv, questions=False)
    except SystemExit:
        pass


def cmd_chat(args):
    if args.json:
        raise ApiError("Chat is interactive; for machine-readable output use the other commands with --json.")
    question = " ".join(args.message).strip()
    # Re-read each turn, so `/project use <id>` in the chat takes effect at once.
    project_fn = lambda: projects.chosen(args.project)  # noqa: E731
    session = chat.Chat(project_fn, _run_in_chat, _offline_answer(project_fn),
                        interactive=not question or sys.stdin.isatty())
    if question:
        session.turn(question)
        return
    signed_in = load_credentials() is not None
    where = project_fn()
    chat.run(session, "Station House. Ask about the projects and tasks, or what to work on next. "
                      "/help lists commands, /exit leaves."
                      + (f"\nWorking in {where} (/project use <id> to switch)." if where else "\nAll your projects (/project use <id> to focus on one).")
                      + ("" if signed_in else "\nYou aren't signed in yet: type /login first.")
                      + (_inbox_line(where) if signed_in else ""))


def _inbox_line(project):
    """The chat greeting's nudge when something is waiting on you; nothing if not, or if it can't tell."""
    try:
        n = len(inbox.fetch(project).get("followUps") or [])
    except (AuthError, ApiError):
        return ""
    if not n:
        return ""
    return f"\n{n} follow-up{'s are' if n != 1 else ' is'} waiting on you: /inbox to see {'them' if n != 1 else 'it'}."


def cmd_assign(args):
    _, out = projects.task_call("POST", args.task_id, "/assign", args.project, {"hfUsername": args.handle.lstrip("@")})
    _emit(args, out, lambda d: print(f"Assigned {args.handle.lstrip('@')} to {args.task_id}."))


def cmd_train(args):
    if not sys.stdout.isatty():
        print("The train only runs in a terminal.")
        return
    try:
        train.play()
    except KeyboardInterrupt:
        print()


def cmd_skills_install(args):
    dest = Path(args.dir).expanduser() if args.dir else Path.home() / ".claude" / "skills"
    src = resources.files("stationhouse_cli") / "skills" / "stationhouse"
    target = dest / "stationhouse"
    target.mkdir(parents=True, exist_ok=True)
    with resources.as_file(src / "SKILL.md") as f:
        shutil.copyfile(f, target / "SKILL.md")
    print(f"Installed the stationhouse skill to {target}")


AGENTS_BEGIN = "<!-- stationhouse:begin (written by `stationhouse agents-md`; edits inside are replaced) -->"
AGENTS_END = "<!-- stationhouse:end -->"


def agents_section() -> str:
    """The usage rules for any coding agent, from the same source as the Claude Code skill (frontmatter dropped)."""
    text = (resources.files("stationhouse_cli") / "skills" / "stationhouse" / "SKILL.md").read_text()
    body = text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()
    return f"{AGENTS_BEGIN}\n{body}\n{AGENTS_END}\n"


def with_section(existing: str, section: str) -> str:
    """`existing` with our section added, or replaced in place if it is already there. Nothing outside it changes."""
    start = existing.find(AGENTS_BEGIN)
    end = existing.find(AGENTS_END, start) if start != -1 else -1
    if start != -1 and end != -1:
        return existing[:start] + section + existing[end + len(AGENTS_END):].lstrip("\n")
    return (existing.rstrip() + "\n\n" if existing.strip() else "") + section


def cmd_agents_md(args):
    if args.print:
        print(agents_section(), end="")
        return
    path = Path(args.dir).expanduser() / "AGENTS.md"
    existing = path.read_text() if path.exists() else ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(with_section(existing, agents_section()))
    print(f"{'Updated' if existing else 'Wrote'} the stationhouse section of {path}")


def build_parser():
    p = argparse.ArgumentParser(
        prog="stationhouse",
        description="Station House from the terminal. Asking something? Run `stationhouse` to chat with the "
                    "assistant, or type the question: stationhouse what could I work on? The commands below "
                    "answer with plain facts (add --json), for scripts and coding agents to reason over.",
    )
    p.add_argument("--version", action="version", version=f"stationhouse-cli {__version__}")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    # No command at all opens the chat.
    p.set_defaults(fn=cmd_chat, message=[], project=None)
    sub = p.add_subparsers(dest="cmd")

    def project(sp):
        sp.add_argument("--project", help="project id (default: the one from `project use`, or the only one)")
        return sp

    def add(name, fn, help, task=False, under=None, aliases=()):
        sp = (under or sub).add_parser(name, help=help, aliases=list(aliases))
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
        if task:
            sp.add_argument("task_id", metavar="task-id")
            project(sp)
        sp.set_defaults(fn=fn)
        return sp

    ch = project(add("chat", cmd_chat, "talk with the Station House assistant (also `stationhouse` on its own, "
                                       "or just type your question: stationhouse what could I work on?)", aliases=["ask"]))
    ch.add_argument("message", nargs="*", help="ask one question and exit, e.g. what could I work on?")
    add("login", cmd_login, "sign in with your Station House email").add_argument("--email")
    add("logout", cmd_logout, "forget the stored session")
    add("whoami", cmd_whoami, "who you are signed in as")
    add("projects", cmd_projects, "the projects you can see (* = the one you're working in)")
    pj = add("project", cmd_project, "show the project you're working in, or `project use <id>` / `project clear`")
    pj.add_argument("action", nargs="?", choices=["show", "use", "clear"], default="show")
    pj.add_argument("project_id", nargs="?", metavar="project-id")
    project(add("unclaimed", cmd_unclaimed, "open tasks nobody is on"))
    add("who", cmd_who, "who is working on a task", task=True)
    add("context", cmd_context, "a task's neighbourhood: people, PRs and their latest comments", task=True)
    add("mine", cmd_mine, "tasks you have claimed")
    ib = project(add("inbox", cmd_inbox, "PRs and discussions waiting on you: feedback, replies, mentions",
                     aliases=["follow-ups", "todo"]))
    ib.add_argument("action", nargs="?", choices=["list", "done"], default="list")
    ib.add_argument("target", nargs="?", metavar="number", help="with `done`: the PR/discussion number (or its full id)")
    pr = project(add("prs", cmd_prs, "open PRs waiting for a review (not your own), unreviewed first"))
    pr.add_argument("--limit", type=int, default=10)
    add("claim", cmd_claim, "claim a task (same as `task claim`)", task=True)
    add("release", cmd_release, "release a task you claimed (same as `task release`)", task=True)
    a = add("assign", cmd_assign, "admins only: put someone on a task", task=True)
    a.add_argument("handle", help="their Hugging Face username")

    t = sub.add_parser("task", help="claim, create or request tasks")
    tsub = t.add_subparsers(dest="task_cmd", required=True)
    add("claim", cmd_claim, "claim an existing task or subtask; it shows on your dashboard", task=True, under=tsub)
    add("release", cmd_release, "release a task you claimed; it comes off your dashboard", task=True, under=tsub)
    c = project(add("create", cmd_task_create, "propose a SUBTASK of an existing task (an admin approves it)", under=tsub))
    c.add_argument("parent", metavar="parent-task-id", help="the existing task it belongs under")
    c.add_argument("title", nargs="+")
    c.add_argument("-s", "--summary", required=True, help="what the subtask is and when it's done")
    r = add("request", cmd_task_request, "ask for a new top-level task that isn't a subtask of an existing one", under=tsub)
    r.add_argument("title", nargs="+")
    r.add_argument("-d", "--description", required=True, help="what the task is and why it's worth doing")
    r.add_argument("--atomic-unit", help="the smallest unit of contribution someone could make")
    project(r)
    add("proposals", cmd_task_proposals, "admins only: subtask proposals waiting for review", under=tsub)
    for name, fn, help in (("approve", cmd_task_approve, "admins only: create the proposed subtask"),
                           ("reject", cmd_task_reject, "admins only: decline a proposed subtask")):
        add(name, fn, help, under=tsub).add_argument("proposal_id", metavar="proposal-id")

    e = project(add("explore", cmd_explore, "for agents/scripts: which tasks a question is about and who is on them (no model)"))
    e.add_argument("question", nargs="*", help="e.g. who is working on the scorer?")
    # explore no longer uses a local model at all; the flag is still accepted so old scripts don't break.
    e.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)
    w = project(add("find-work", cmd_find_work, "pick an open task for you to do", aliases=["work"]))
    w.add_argument("about", nargs="*", help="what you're into, e.g. adversarial prompts")
    w.add_argument("--limit", type=int, default=3, help="how many tasks to suggest (default 3)")
    add("train", cmd_train, "watch the welcome train again")
    add("skills-install", cmd_skills_install, "install the agent skill (Claude Code)").add_argument("--dir", help="skills directory (default ~/.claude/skills)")
    am = add("agents-md", cmd_agents_md, "write the usage rules into AGENTS.md for other coding agents (Codex, Cursor, ...)")
    am.add_argument("--dir", default=".", help="project directory (default: here)")
    am.add_argument("--print", action="store_true", help="print the section instead of writing a file")
    return p


def _command_names(parser) -> set[str]:
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))  # noqa: SLF001
    return set(sub.choices)


# Commands a question can start with ("who is on the scorer?", "claim the scorer task for me"): when
# what follows doesn't parse as the command, it's a question. Commands with free text or required
# options (task, explore, assign, ...) always get their own error instead.
QUESTION_STARTERS = {"who", "context", "claim", "release", "mine", "projects", "project", "unclaimed", "prs", "whoami", "inbox", "todo"}


def _parse(parser, argv, questions=True):
    """Commands run as commands; anything else a person types is a question for the assistant.
    `stationhouse who add-question` is the `who` command; `stationhouse who is working on the scorer?`
    and `stationhouse what should I do next` are questions. A near-miss like `claimm x` is an error,
    not a question, so a typo never quietly goes to the assistant. questions=False (a /command typed
    in the chat) parses strictly."""
    if not questions or not argv or argv[0].startswith("-"):
        return parser.parse_args(argv)
    if argv[0] == "help":
        parser.print_help()
        raise SystemExit(0)
    commands = _command_names(parser)
    if argv[0] not in commands:
        close = difflib.get_close_matches(argv[0], commands, n=1, cutoff=0.8)
        if close and len(argv) <= 3:
            parser.error(f"unknown command {argv[0]!r}; did you mean `{close[0]}`?")
        return parser.parse_args(["ask", *argv])
    looks_like_a_sentence = argv[0] in QUESTION_STARTERS and len(argv) >= 3 and not any(a.startswith("-") for a in argv)
    if not looks_like_a_sentence:
        return parser.parse_args(argv)
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            return parser.parse_args(argv)
    except SystemExit as e:
        if e.code == 0:
            raise
        return parser.parse_args(["ask", *argv])


def main(argv=None, questions=True):
    import re
    parser = build_parser()
    args = _parse(parser, sys.argv[1:] if argv is None else list(argv), questions)
    for attr in ("task_id", "parent"):
        if hasattr(args, attr) and not re.match(TASK_ID, getattr(args, attr)):
            print(f"error: {getattr(args, attr)!r} isn't a valid task id", file=sys.stderr)
            return 2
    if args.fn is not cmd_train:
        train.welcome(bool(getattr(args, "json", False)))
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
