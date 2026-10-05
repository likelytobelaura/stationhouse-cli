import argparse
import getpass
import json
import shutil
import sys
from importlib import resources
from pathlib import Path

from . import __version__, config
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


def cmd_claim(args):
    out = request("POST", "/api/v1/claims", {"taskId": args.task_id})

    def human(d):
        print(f"You're on {args.task_id}.")
        others = [p for p in d.get("people", []) if not p.get("you")]
        if others:
            print("Also working on it:")
            print("\n".join(_people_line(p) for p in others))
    _emit(args, out, human)


def cmd_release(args):
    out = request("POST", "/api/v1/claims/release", {"taskId": args.task_id})
    _emit(args, out, lambda d: print(f"Released {args.task_id}."))


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

    def add(name, fn, help, task=False):
        sp = sub.add_parser(name, help=help)
        sp.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="machine-readable output")
        if task:
            sp.add_argument("task_id", metavar="task-id")
        sp.set_defaults(fn=fn)
        return sp

    add("login", cmd_login, "sign in with your Station House email").add_argument("--email")
    add("logout", cmd_logout, "forget the stored session")
    add("whoami", cmd_whoami, "who you are signed in as")
    add("unclaimed", cmd_unclaimed, "open tasks nobody is on").add_argument("--project", default=config.DEFAULT_PROJECT)
    add("who", cmd_who, "who is working on a task", task=True)
    add("mine", cmd_mine, "tasks you have claimed")
    add("claim", cmd_claim, "claim a task (many people can share one)", task=True)
    add("release", cmd_release, "release a task you claimed", task=True)
    a = add("assign", cmd_assign, "admins only: put someone on a task", task=True)
    a.add_argument("handle", help="their Hugging Face username")
    add("skills-install", cmd_skills_install, "install the agent skill").add_argument("--dir", help="skills directory (default ~/.claude/skills)")
    return p


def main(argv=None):
    import re
    args = build_parser().parse_args(argv)
    if hasattr(args, "task_id") and not re.match(TASK_ID, args.task_id):
        print(f"error: {args.task_id!r} isn't a valid task id", file=sys.stderr)
        return 2
    try:
        args.fn(args)
    except (AuthError, ApiError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
