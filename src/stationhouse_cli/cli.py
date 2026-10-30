import argparse
import contextlib
import difflib
import getpass
import io
import json
import shutil
import sys
from datetime import datetime
from importlib import resources
from pathlib import Path

from . import __version__, chat, design, explore as explore_mod, gate, inbox, projects, traces, train, wizard, work
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


# Neuron claims exist only in the Marin Neuron Atlas: one holder per neuron, 7 days from the last
# activity (an open PR stops the clock), at most 5 each, GitHub verified on the profile.
NEURONS = "/api/v1/projects/marin-neuron-atlas/neurons"
HUNT_REPO = "Station-House/marin-neuron-hunt"


def _day(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%b %-d")


def _neuron_line(c):
    if c.get("state") == "in-review":
        when = f"PR #{c.get('prNum')} open"
    elif c.get("until"):
        when = f"until {_day(c['until'])}"
    else:
        when = c.get("state", "")
    challenge = f", challenging @{c['explainedBy']}" if c.get("challenge") else ""
    return f"{c['neuronId']}  ({when}{challenge})"


def cmd_neuron_claim(args):
    note = " ".join(getattr(args, "note", None) or [])
    out = request("PUT", f"{NEURONS}/{args.neuron_id}/claim", {"note": note} if note else {})

    def human(d):
        c = d["claim"]
        if c.get("state") == "in-review":
            print(f"You hold {c['neuronId']}; your PR #{c.get('prNum')} for it is open, so it won't lapse.")
            return
        verb = "Renewed: you hold" if d.get("renewed") else "You hold"
        print(f"{verb} {c['neuronId']} until {_day(c['until'])}.")
        if c.get("challenge"):
            print(f"It's explained by @{c['explainedBy']}; your PR should add your version under `alternatives:`.")
        print(f"Open a PR for it in {HUNT_REPO} (list it under '## Neuron(s)') to stop the clock.")
    _emit(args, out, human)


def cmd_neuron_release(args):
    out = request("DELETE", f"{NEURONS}/{args.neuron_id}/claim")
    _emit(args, out, lambda d: print(f"Released {d['claim']['neuronId']}."))


def cmd_neuron_who(args):
    out = request("GET", f"{NEURONS}/{args.neuron_id}/claim")

    def human(d):
        c = d.get("claim")
        if not c or c.get("state") == "free":
            print(f"Nobody holds {args.neuron_id.upper()}. Claim it: stationhouse neuron claim {args.neuron_id}")
            return
        if c.get("holder"):
            print(f"@{c['holder']['github']} holds {_neuron_line(c)}")
            if c.get("note"):
                print(f"  \"{c['note']}\"")
        else:
            print(f"{c['neuronId']} is explained by @{c.get('explainedBy')} (PR #{c.get('explainedPrNum')}). "
                  f"Claiming it starts a challenge.")
    _emit(args, out, human)


def cmd_neuron_mine(args):
    out = request("GET", f"{NEURONS}/claims", query={"mine": "1"})

    def human(d):
        me = d.get("me", {})
        if not me.get("github"):
            print("Connect your GitHub account on your Station House profile to claim neurons.")
        held = [c for c in d.get("claims", []) if c.get("holder")]
        if not held:
            print("You don't hold any neurons.")
            return
        print(f"You hold {me.get('live', len(held))} of {me.get('cap', 5)}:")
        print("\n".join(f"  {_neuron_line(c)}" for c in held))
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


def _design_file(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.suffix.lower() == ".md" else p / "DESIGN.md"


def cmd_design_new(args):
    path = _design_file(args.path)
    if path.exists() and not args.force:
        raise ApiError(f"{path} already exists. Work in it, or pass --force to start it over.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(design.template(), encoding="utf-8")
    _emit(args, {"path": str(path)}, lambda d: print(
        f"Wrote {path}. Fill in each section before you run anything, then: stationhouse design check {path}"))


def _read_design(path: str) -> tuple[Path, str]:
    p = _design_file(path)
    if not p.is_file():
        raise ApiError(f"{p} doesn't exist. Start one with: stationhouse design new {path}")
    return p, p.read_text(encoding="utf-8")


def _print_findings(path, findings):
    errors = [f for f in findings if f["level"] == "error"]
    if not findings:
        print(f"{path}: all six stages are filled in and nothing obvious is missing.")
        print("That's the floor, not a review: next, stationhouse design review " + str(path))
        return
    for f in findings:
        print(f"{f['level']}: {f['section']}: {f['message']}")
    print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s)."
          + (" Fix the errors before running anything." if errors else " Keep a warning only if the file says why."))


def cmd_design_check(args):
    path, text = _read_design(args.path)
    findings = design.check(text)
    errors = sum(f["level"] == "error" for f in findings)
    _emit(args, {"path": str(path), "ready": not errors, "findings": findings},
          lambda d: _print_findings(path, findings))
    return 1 if errors else 0


def cmd_design_guide(args):
    _emit(args, {"guide": design.guide(), "template": design.template()}, lambda d: print(d["guide"]))


def cmd_design_review(args):
    path, text = _read_design(args.path)
    findings = design.check(text)
    question, truncated = design.review_question(text, chat.MAX_QUESTION)
    try:
        res = request("POST", "/api/v1/agent/chat",
                      {"messages": [{"role": "user", "text": question}], "project": projects.chosen(args.project)})
        reply = (res or {}).get("reply") or "(no reply)"
    except ApiError as e:
        if e.status not in chat.OFFLINE_STATUSES and e.status is not None:
            raise
        reply = None
        why = str(e)

    def human(_d):
        if findings:
            _print_findings(path, findings)
            print()
        if truncated:
            print("(The design is longer than the assistant takes in one question, so it saw the six stages only, cut to fit.)\n")
        if reply is None:
            print(f"The assistant can't review it right now ({why}). The checks above still apply; a coding agent "
                  "can review it too (it gets the method from `stationhouse design guide`).")
        else:
            print(reply)
    _emit(args, {"path": str(path), "findings": findings, "truncated": truncated, "review": reply}, human)


def _here():
    """(repo id, project id, branch) for the current directory's Station House project repo."""
    root = wizard.repo_root(".") or Path(".").resolve()
    project = wizard.project_repo(wizard.remote_repos(root))
    if not project:
        raise ApiError("This folder isn't a clone of a Station House project repo. Pass --repo hf:owner/name or github:owner/name.")
    return project["repo"], project.get("projectId"), wizard.current_branch(root)


def _repo_args(args):
    if getattr(args, "repo", None):
        project = wizard.project_repo([args.repo]) or {"repo": args.repo, "projectId": None}
        return project["repo"], project.get("projectId"), wizard.current_branch(".")
    return _here()


def _session_id(args) -> str:
    if getattr(args, "session", None):
        return args.session
    repo, _, branch = _repo_args(args)
    session = wizard.find(repo, branch)
    if not session:
        raise ApiError("No design review for this branch yet. Start one: stationhouse design start \"<title>\"")
    return session["id"]


def cmd_design_start(args):
    repo, project_id, branch = _repo_args(args)
    session = wizard.start(repo, branch, " ".join(args.title), project_id)
    _emit(args, {"session": session}, lambda d: print(
        f"Started design review {session['id']} for {repo}{' (' + branch + ')' if branch else ''}.\n"
        f"Next: stationhouse design round <design file>"))


def cmd_design_round(args):
    path, text = _read_design(args.path)
    sid = _session_id(args)
    root = wizard.repo_root(".")
    files = gate.changed_files(root) if root else []
    res = wizard.ask_round(sid, text, wizard.results_summary(files, root) if root else "", wizard.format_check(text),
                           getattr(args, "no_change_reason", None))
    round_, session = res["round"], res["session"]

    def human(_d):
        print(wizard.describe_round(round_))
        print(f"\nAnswer these yourself at {session['answerUrl']}\nor in a separate terminal: stationhouse design answer")
        print("\n(Coding agents: STOP here. Show the contributor these questions word for word; don't answer them.)")
    _emit(args, {"session": session, "round": round_}, human)
    return 3  # waiting for a human


def cmd_design_answer(args):
    import sys
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ApiError("`design answer` takes the contributor's own answers, typed in a terminal. Run it in a separate "
                       "terminal window, or answer at the link from `design round`.")
    sid = _session_id(args)
    full = wizard.get(sid)
    waiting = next((r for r in reversed(full["rounds"]) if not r.get("answers")), None)
    if not waiting:
        print("No round is waiting for answers. Submit the next round with: stationhouse design round <file>")
        return 0
    print(wizard.describe_round(waiting))
    print(f"\nType each answer; finish it with an empty line. At least {wizard.MIN_ANSWER} characters, or \"n/a: <why>\".")
    answers = {}
    questions = waiting["feedback"]["questions"]
    pending = questions
    while True:
        while pending:
            for q in pending:
                print(f"\n[{q['id']}] {q['question']}")
                lines = []
                while True:
                    line = input("> ")
                    if not line.strip():
                        break
                    lines.append(line)
                answers[q["id"]] = "\n".join(lines)
            problems = wizard.answer_problems(questions, answers)
            for qid, problem in problems.items():
                print(f"{qid}: {problem}")
            pending = [q for q in questions if q["id"] in problems]
        try:
            res = wizard.answer(sid, waiting["n"], answers, "cli-tty")
            break
        except ApiError as e:
            if e.status != 400 or not e.body.get("problems"):
                raise
            # Graded vague or evasive: say what's missing and ask those again.
            print(f"\n{e}")
            problems = {p["id"]: p["problem"] for p in e.body["problems"]}
            for qid, problem in problems.items():
                print(f"{qid}: {problem}")
            pending = [q for q in questions if q["id"] in problems]
    done = res["session"]["roundsAnswered"] >= wizard.ROUNDS
    print(f"\nSaved round {waiting['n']}. " + ("All three rounds are answered: finalize with stationhouse design finalize <file>."
                                              if done else "Revise the design, then: stationhouse design round <file>"))
    return 0


def cmd_design_status(args):
    sid = _session_id(args)
    full = wizard.get(sid)
    s = full["session"]

    def human(_d):
        print(f"{s['id']}  {s['title']}  ({s['repo']})")
        print(f"{s['roundsAnswered']}/{wizard.ROUNDS} rounds answered" + ("; finalized" if s.get("finalSha256") else ""))
        for r in full["rounds"]:
            print(f"  round {r['n']}: {r['focus']}: " + (f"answered via {r['answeredVia']}" if r.get("answers") else "waiting"))
        print(f"Web: {s['answerUrl']}")
    _emit(args, full, human)


def cmd_design_finalize(args):
    path, text = _read_design(args.path)
    res = wizard.finalize(_session_id(args), text)

    def human(d):
        v = d.get("verdict")
        if v:
            print("Closing review:\n" + v.get("summary", ""))
            for s in v.get("stages", []):
                print(f"  {s['stage']}: {s['status']}" + (f"  (\u201c{s['quote']}\u201d)" if s.get("quote") else ""))
            for b in v.get("blockers", []):
                print(f"  {'resolved' if b['resolved'] else 'OPEN'} blocker: {b['issue']}")
            p = v.get("preregistration", {})
            for k in ("claim", "unit", "metric", "pass", "refute", "prediction"):
                print(f"  {k}: {p.get(k) or 'not stated'}")
            print()
        print(f"Finalized. Put this line in the PR description:\n{d['marker']}")
    _emit(args, res, human)


def cmd_design_gate(args):
    import sys
    if args.hook:
        code, message = gate.hook(sys.stdin.read())
        if message:
            print(message, file=sys.stderr)
        return code
    if args.pre_push is not None:
        return gate.main_pre_push(args.pre_push or None)
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else ""
    result = gate.check(".", repo=args.repo, base=args.base, body=body)
    _emit(args, result, lambda d: print(d.get("message", "")))
    return gate.BLOCK if result["outcome"] == "blocked" else 0


def cmd_design_attach_trace(args):
    import sys
    sid = _session_id(args)
    prepared = traces.prepare(sid, Path(".").resolve(), args.file)
    c = prepared["counts"]
    print(f"Transcript: {prepared['path']} ({prepared['agent']})")
    print(f"After redaction: {c.get('lines', 0)} entries; {c.get('secrets', 0)} keys/tokens/emails removed; "
          f"{c.get('droppedFields', 0)} file-content fields dropped; {c.get('toolResultsTrimmed', 0)} tool outputs trimmed.")
    print("\nStart of what would be sent:\n" + (traces.preview(prepared["text"]) or "(no messages)"))
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise ApiError("Attaching a transcript needs your yes in a terminal. Run this in a terminal window.")
    if input("\nSend it to Station House (admins only can read it)? [y/N] ").strip().lower() not in ("y", "yes"):
        print("Not sent.")
        return 0
    res = traces.upload(sid, prepared, "cli-tty")
    print(f"Attached ({res['bytes']} bytes gzipped).")
    return 0


def cmd_hooks_install(args):
    import os
    import stat
    root = wizard.repo_root(args.dir)
    if not root:
        raise ApiError(f"{args.dir} isn't inside a git repo.")
    hooks_dir = Path(wizard.git(root, "rev-parse", "--git-path", "hooks") or ".git/hooks")
    hooks_dir = hooks_dir if hooks_dir.is_absolute() else root / hooks_dir
    target = hooks_dir / "pre-push"
    if target.exists() and "stationhouse design gate" not in target.read_text(errors="replace") and not args.force:
        raise ApiError(f"{target} already exists and isn't ours. Merge it by hand, or pass --force to replace it.")
    exe = shutil.which("stationhouse")
    command = f'"{exe}"' if exe else f'"{sys.executable}" -m stationhouse_cli.cli'
    hooks_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(gate.PRE_PUSH_HOOK.format(command=command))
    os.chmod(target, target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    _emit(args, {"path": str(target)}, lambda d: print(
        f"Installed the design gate as {target}. Experiment pushes to Station House projects now need a reviewed design."))


def cmd_mcp(args):
    from . import mcp_server
    try:
        import mcp  # noqa: F401
    except ImportError:
        raise ApiError("The MCP server needs the `mcp` package: pip install 'stationhouse-cli[mcp]'") from None
    return mcp_server.main()


SKILLS = ("stationhouse", "experiment-design")


def cmd_skills_install(args):
    dest = Path(args.dir).expanduser() if args.dir else Path.home() / ".claude" / "skills"
    for name in SKILLS:
        src = resources.files("stationhouse_cli") / "skills" / name
        target = dest / name
        target.mkdir(parents=True, exist_ok=True)
        for f in src.iterdir():
            if f.name.endswith(".md"):
                with resources.as_file(f) as real:
                    shutil.copyfile(real, target / f.name)
        print(f"Installed the {name} skill to {target}")


AGENTS_BEGIN = "<!-- stationhouse:begin (written by `stationhouse agents-md`; edits inside are replaced) -->"
AGENTS_END = "<!-- stationhouse:end -->"
# One marked section per skill, so each is refreshed in place on its own.
MARKERS = {
    "stationhouse": (AGENTS_BEGIN, AGENTS_END),
    "experiment-design": ("<!-- stationhouse-design:begin (written by `stationhouse agents-md`; edits inside are replaced) -->",
                          "<!-- stationhouse-design:end -->"),
}


def agents_section(skill: str = "stationhouse") -> str:
    """The usage rules for any coding agent, from the same source as the Claude Code skill (frontmatter dropped)."""
    text = (resources.files("stationhouse_cli") / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    body = text.split("---", 2)[2].strip() if text.startswith("---") else text.strip()
    begin, end = MARKERS[skill]
    return f"{begin}\n{body}\n{end}\n"


def with_section(existing: str, section: str, begin: str = AGENTS_BEGIN, end_marker: str = AGENTS_END) -> str:
    """`existing` with our section added, or replaced in place if it is already there. Nothing outside it changes."""
    start = existing.find(begin)
    end = existing.find(end_marker, start) if start != -1 else -1
    if start != -1 and end != -1:
        rest = existing[end + len(end_marker):].lstrip("\n")
        return existing[:start] + section + ("\n" + rest if rest else "")
    return (existing.rstrip() + "\n\n" if existing.strip() else "") + section


def cmd_agents_md(args):
    if args.print:
        print("\n".join(agents_section(name) for name in SKILLS), end="")
        return
    path = Path(args.dir).expanduser() / "AGENTS.md"
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    text = existing
    for name in SKILLS:
        text = with_section(text, agents_section(name), *MARKERS[name])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"{'Updated' if existing else 'Wrote'} the stationhouse sections of {path}")


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

    n = sub.add_parser("neuron", help="Marin Neuron Atlas: claim a neuron so nobody else takes it")
    nsub = n.add_subparsers(dest="neuron_cmd", required=True)
    for name, fn, help in (
        ("claim", cmd_neuron_claim, "hold a neuron for 7 days (again on one you hold: renew); an explained one is a challenge"),
        ("renew", cmd_neuron_claim, "restart the 7 days on a neuron you hold"),
        ("release", cmd_neuron_release, "give a neuron up"),
        ("who", cmd_neuron_who, "who holds a neuron, if anyone"),
    ):
        sp = add(name, fn, help, under=nsub)
        sp.add_argument("neuron_id", metavar="neuron-id", help="e.g. L40-N25546")
        if name == "claim":
            sp.add_argument("note", nargs="*", help="optional: what you'll test (up to 140 characters)")
    add("mine", cmd_neuron_mine, "the neurons you hold", under=nsub)

    d = sub.add_parser("design", help="design an experiment before you run it: template, checks, review")
    dsub = d.add_subparsers(dest="design_cmd", required=True)
    dn = add("new", cmd_design_new, "write a DESIGN.md with the six stages to fill in", under=dsub)
    dn.add_argument("path", nargs="?", default=".", help="a folder (gets DESIGN.md) or a .md file, e.g. EXPERIMENT.md")
    dn.add_argument("--force", action="store_true", help="overwrite an existing file")
    add("check", cmd_design_check, "check a design file: every stage filled in, a baseline named, ... (no model)",
        under=dsub).add_argument("path", nargs="?", default=".", help="the design file, or its folder")
    add("guide", cmd_design_guide, "how to design one, stage by stage", under=dsub)
    project(add("review", cmd_design_review, "ask the Station House assistant to critique a design (signed in)",
                under=dsub)).add_argument("path", nargs="?", default=".", help="the design file, or its folder")

    def wiz(name, fn, help, path=False):
        sp = add(name, fn, help, under=dsub)
        sp.add_argument("--session", help="design session id (default: this repo and branch's)")
        sp.add_argument("--repo", help="hf:owner/name or github:owner/name (default: from the git remote)")
        if path:
            sp.add_argument("path", nargs="?", default=".", help="the design file, or its folder")
        return sp

    st = wiz("start", cmd_design_start, "Experimentation Wizard: start the three-round design review before an experiment PR")
    st.add_argument("title", nargs="+")
    wiz("round", cmd_design_round, "submit the design for the next review round (the contributor answers it)",
        path=True).add_argument("--no-change-reason", help="why the design stays as it was after the last round's answers")
    wiz("answer", cmd_design_answer, "answer the waiting round yourself, in a terminal")
    wiz("status", cmd_design_status, "the review's rounds and answers")
    wiz("finalize", cmd_design_finalize, "after round 3: record the design and get the Design-Session line for the PR", path=True)
    at = wiz("attach-trace", cmd_design_attach_trace, "optional: share this session's agent transcript (redacted; asks first)")
    at.add_argument("--file", help="the transcript (default: the newest Claude Code/Codex log that mentions the session)")
    g = add("gate", cmd_design_gate, "is this push an experiment, and was its design reviewed? (exit 2 = blocked)", under=dsub)
    g.add_argument("--repo", help="hf:owner/name or github:owner/name (default: from the git remote)")
    g.add_argument("--base", help="compare against this ref (default: origin's default branch)")
    g.add_argument("--body-file", help="the PR description, if the design is in its ## Design section")
    g.add_argument("--hook", action="store_true", help="run as a Claude Code PreToolUse hook (tool call on stdin)")
    g.add_argument("--pre-push", nargs="?", const="", default=None, metavar="REMOTE_URL", help="run as a git pre-push hook")

    hk = sub.add_parser("hooks", help="git hooks for Station House project repos")
    hsub = hk.add_subparsers(dest="hooks_cmd", required=True)
    hi = add("install", cmd_hooks_install, "add the design gate as this repo's git pre-push hook", under=hsub)
    hi.add_argument("--dir", default=".", help="the repo (default: here)")
    hi.add_argument("--force", action="store_true", help="replace an existing pre-push hook")
    add("mcp", cmd_mcp, "run the Experimentation Wizard MCP server (stdio) for coding agents")

    e = project(add("explore", cmd_explore, "for agents/scripts: which tasks a question is about and who is on them (no model)"))
    e.add_argument("question", nargs="*", help="e.g. who is working on the scorer?")
    # explore no longer uses a local model at all; the flag is still accepted so old scripts don't break.
    e.add_argument("--no-llm", action="store_true", help=argparse.SUPPRESS)
    w = project(add("find-work", cmd_find_work, "pick an open task for you to do", aliases=["work"]))
    w.add_argument("about", nargs="*", help="what you're into, e.g. adversarial prompts")
    w.add_argument("--limit", type=int, default=3, help="how many tasks to suggest (default 3)")
    add("train", cmd_train, "watch the welcome train again")
    add("skills-install", cmd_skills_install, "install the agent skills (Claude Code)").add_argument("--dir", help="skills directory (default ~/.claude/skills)")
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
    # The train never plays for hooks and the MCP server: their stdout/stderr belong to the agent.
    if args.fn not in (cmd_train, cmd_mcp, cmd_design_gate):
        train.welcome(bool(getattr(args, "json", False)))
    try:
        code = args.fn(args)
    except (AuthError, ApiError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except EOFError:
        # No terminal to read from (a piped or background shell): say so, don't dump a traceback.
        print("error: this needs a terminal to type into. Run it in a normal terminal window.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return code or 0


if __name__ == "__main__":
    sys.exit(main())
