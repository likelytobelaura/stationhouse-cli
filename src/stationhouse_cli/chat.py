"""Chat mode: `stationhouse` with no arguments. Type to talk with the Station House assistant (the
platform's POST /api/v1/agent/chat); start a line with `/` to run any normal command (`/who
add-question`, `/claim add-question`, `/login`).

The assistant only reads. When it suggests claiming or releasing a task you're asked here, and on a
yes this CLI makes the claim itself, as you, the same as typing `/claim`. When the assistant is off,
over its budget or unreachable, questions get the offline `explore` answer instead.
"""
import shlex

from .auth import AuthError
from .client import ApiError, request

HELP = """Ask anything about the projects and tasks, e.g. "what could I work on?", "what's happening on add-question?".
Commands start with /:
  /who <task>  /context <task>  /claim <task>  /release <task>  /mine  /unclaimed  /find-work
  /login  /logout  /whoami  /clear (forget this conversation)  /help  /exit
Any other `stationhouse` command works the same way after a /."""

# What the server accepts (src/lib/agent/chat.ts): 40 messages, 4000 characters a question, 8000 a reply.
MAX_MESSAGES = 39
MAX_QUESTION = 4000
MAX_REPLY = 8000
# The assistant is off (503), over budget (429), failing upstream (502, e.g. Bedrock refusing) or this
# server doesn't have it (404): answer offline.
OFFLINE_STATUSES = (404, 429, 502, 503)


class Chat:
    def __init__(self, project, run_command, fallback, ask=None, out=print):
        """run_command(argv) runs a normal CLI command; fallback(question) prints the offline answer."""
        self.project, self.run_command, self.fallback = project, run_command, fallback
        # input is looked up per call, not bound here, so readline and tests see the real one.
        self.ask = ask or (lambda prompt="": input(prompt))
        self.out = out
        self.history = []

    def turn(self, line):
        """Handles one line. False means the session is over."""
        line = line.strip()
        if not line:
            return True
        if line in ("exit", "quit", "/exit", "/quit", "/q"):
            return False
        if line.startswith("/"):
            return self._command(line[1:])
        self._question(line)
        return True

    def _command(self, text):
        try:
            argv = shlex.split(text)
        except ValueError as e:
            self.out(f"Couldn't read that command: {e}")
            return True
        if not argv or argv[0] == "help":
            self.out(HELP)
        elif argv[0] == "clear":
            self.history = []
            self.out("Started a new conversation.")
        elif argv[0] == "chat":
            self.out("You're already chatting.")
        else:
            self.run_command(argv)
        return True

    def _question(self, question):
        if len(question) > MAX_QUESTION:
            self.out(f"That's too long for one question (at most {MAX_QUESTION} characters).")
            return
        messages = (self.history + [{"role": "user", "text": question}])[-MAX_MESSAGES:]
        try:
            res = request("POST", "/api/v1/agent/chat", {"messages": messages, "project": self.project})
        except AuthError:
            self.out("You aren't signed in. Type /login, then ask again.")
            return
        except ApiError as e:
            if e.status == 401:
                self.out("Your session expired. Type /login, then ask again.")
            elif e.status in OFFLINE_STATUSES or e.status is None:
                self.out(f"({e} Answering from the task list instead.)\n")
                self.fallback(question)
            else:
                self.out(f"error: {e}")
            return
        res = res or {}
        reply = res.get("reply") or "(no reply)"
        self.history += [{"role": "user", "text": question}, {"role": "assistant", "text": reply[:MAX_REPLY]}]
        self.out(reply)
        for action in res.get("suggestedActions") or []:
            self._offer(action)

    def _offer(self, action):
        verb = {"claim": "Claim", "release": "Release"}.get(action.get("type"))
        task_id = action.get("taskId")
        if not verb or not task_id:
            return
        reason = f" ({action['reason']})" if action.get("reason") else ""
        try:
            answer = self.ask(f"{verb} {task_id}, \"{action.get('title', task_id)}\"{reason}? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() in ("y", "yes"):
            self.run_command([action["type"], task_id])
        else:
            self.out("OK, left as it is.")


def run(chat, greeting):
    """The interactive loop. Ctrl-D or /exit leaves; Ctrl-C abandons the current line."""
    try:
        import readline  # noqa: F401 -- arrow keys edit the line and recall earlier questions
    except ImportError:
        pass
    chat.out(greeting)
    while True:
        try:
            line = chat.ask("you> ")
        except EOFError:
            chat.out("")
            return
        except KeyboardInterrupt:
            chat.out("")
            continue
        try:
            if not chat.turn(line):
                return
        except KeyboardInterrupt:
            chat.out("\n(stopped)")
