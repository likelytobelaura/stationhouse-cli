"""`stationhouse mcp`: the Experimentation Wizard as an MCP server (stdio), for Claude Code (through
the stationhouse plugin), Codex, Cursor and other agents.

Each round's questions go to the contributor as a form inside their agent (MCP elicitation). The
answers go from that form to Station House and come back to the agent only to read, so the model
never writes them. There is deliberately no tool that submits answers. Agents that can't show a form
get the questions and a link to answer them on the web instead.
"""
import os
from pathlib import Path

from . import wizard
from .auth import AuthError
from .client import ApiError

INSTRUCTIONS = """Station House's Experimentation Wizard. Before you push or open a PR with an experiment on a
Station House project (ConspiracyBench, the Marin Neuron Hunt, Consciousness Self-Attribution), its design
goes through three rounds of reviewer questions, and the CONTRIBUTOR answers each round in a form. Never
answer the questions yourself, never paraphrase them into your own answers, and don't push until
design_finalize has given you the Design-Session line for the PR description. Between rounds, revise the
design file with the contributor using their answers."""

STOP = ("STOP here. Show the contributor these questions word for word and ask them to answer at the link (or in "
        "`stationhouse design answer` in a separate terminal). Do not answer them yourself. End your turn; when they "
        "say they've answered, call design_status, revise the design with them, and submit the next round.")


def _session_for(cwd: str | None) -> tuple[dict, str, str | None]:
    """(project repo entry, repo id, branch) for the working directory, or raise ApiError."""
    root = wizard.repo_root(cwd or os.getcwd()) or Path(cwd or os.getcwd())
    project = wizard.project_repo(wizard.remote_repos(root))
    if not project:
        raise ApiError("This folder isn't a clone of a Station House project repo, so there's no project to review against. "
                       "Pass `repo` (e.g. hf:Station-house/ConspiracyBench).")
    return project, project["repo"], wizard.current_branch(root)


def _read(path: str, cwd: str | None) -> str:
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = Path(cwd or os.getcwd()) / p
    if not p.is_file():
        raise ApiError(f"{p} doesn't exist.")
    return p.read_text(encoding="utf-8")


def _answer_form(round_: dict):
    """A pydantic model with one text field per question: the form the contributor fills in."""
    from pydantic import Field, create_model
    fields = {}
    for q in round_["feedback"]["questions"]:
        about = f"About: “{q['quote']}”. " if q.get("quote") else ""
        fields[q["id"]] = (str, Field(title=q["question"], description=f"{about}Why it matters: {q['why']}"))
    return create_model(f"Round{round_['n']}Answers", **fields)


def _form_message(round_: dict) -> str:
    fb = round_["feedback"]
    lines = [f"Station House design review, round {round_['n']} of {wizard.ROUNDS}: {round_.get('focus', '')}.", fb.get("summary", "")]
    if fb.get("blocking"):
        lines.append("Blocking: " + " ".join(b["issue"] for b in fb["blocking"]))
    lines.append(f"Answer each question in your own words (at least {wizard.MIN_ANSWER} characters, or \"n/a: <why>\").")
    return "\n".join(lines)


def build_server():
    from mcp.server.mcpserver import Context, MCPServer
    from mcp.shared.exceptions import NoBackChannelError

    server = MCPServer(name="stationhouse", instructions=INSTRUCTIONS)

    @server.tool(description="Start a Station House design review (Experimentation Wizard) for the current repo and "
                             "branch. Call it once the contributor's design is written, before any push or PR.")
    def design_start(title: str, cwd: str | None = None, repo: str | None = None) -> dict:
        if repo:
            project = wizard.project_repo([repo]) or {"repo": repo, "projectId": None}
            branch = wizard.current_branch(cwd or os.getcwd())
        else:
            project, repo, branch = _session_for(cwd)
        session = wizard.start(project["repo"], branch, title, project.get("projectId"))
        return {"session": session, "next": "Call design_submit_round with the design file for round 1."}

    @server.tool(description="Submit the design file for the next review round. The contributor gets 3-5 questions "
                             "in a form and answers them; you get their answers back. Revise the design with them "
                             "before the next round. Three rounds, then design_finalize.")
    async def design_submit_round(draft_path: str, ctx: Context, cwd: str | None = None, session_id: str | None = None) -> dict:
        text = _read(draft_path, cwd)
        if not session_id:
            _, repo, branch = _session_for(cwd)
            session = wizard.find(repo, branch)
            if not session:
                raise ApiError("No design review for this branch yet. Call design_start first.")
            session_id = session["id"]
        root = wizard.repo_root(cwd or os.getcwd())
        files = []
        if root:
            from . import gate
            files = gate.changed_files(root)
        res = wizard.ask_round(session_id, text, wizard.results_summary(files, root) if root else "", wizard.format_check(text))
        round_, session = res["round"], res["session"]
        no_form = {"round": round_, "answerUrl": session["answerUrl"], "instructions": STOP,
                   "questions": wizard.describe_round(round_)}
        caps = ctx.client_capabilities
        if not (caps and caps.elicitation):
            return no_form

        message = _form_message(round_)
        for _attempt in range(3):
            try:
                result = await ctx.elicit(message, _answer_form(round_))
            except NoBackChannelError:
                # A connection that can't carry a server request (the 2026 stateless protocol): the link it is.
                return no_form
            if result.action != "accept" or result.data is None:
                return {"round": round_["n"], "status": result.action,
                        "instructions": "The contributor stopped this round. End your turn and ask them how they'd like to "
                                        f"continue. They can also answer at {session['answerUrl']}."}
            answers = result.data.model_dump()
            problems = wizard.answer_problems(round_["feedback"]["questions"], answers)
            if not problems:
                saved = wizard.answer(session_id, round_["n"], answers, "mcp-elicit")
                done = saved["session"]["roundsAnswered"] >= wizard.ROUNDS
                return {
                    "round": round_["n"],
                    "questions": round_["feedback"]["questions"],
                    "blocking": round_["feedback"]["blocking"],
                    "contributorAnswers": answers,
                    "roundsAnswered": saved["session"]["roundsAnswered"],
                    "next": ("All three rounds are answered. Update the design file with the contributor, then call "
                             "design_finalize with it." if done else
                             "Revise the design file WITH the contributor using their answers, then call "
                             "design_submit_round again for the next round."),
                }
            message = _form_message(round_) + "\n\nPlease expand: " + "; ".join(f"{k}: {v}" for k, v in problems.items())
        return {"round": round_["n"], "status": "incomplete", "answerUrl": session["answerUrl"],
                "instructions": "The answers were too short three times. End your turn; the contributor can answer at the link."}

    @server.tool(description="The current design review: rounds asked and answered, and the contributor's answers.")
    def design_status(cwd: str | None = None, session_id: str | None = None) -> dict:
        if not session_id:
            _, repo, branch = _session_for(cwd)
            session = wizard.find(repo, branch)
            if not session:
                return {"session": None, "next": "No review yet: call design_start."}
            session_id = session["id"]
        full = wizard.get(session_id)
        return {"session": full["session"], "rounds": full["rounds"]}

    @server.tool(description="After all three rounds are answered: record the design as it will be pushed and get "
                             "the Design-Session line for the PR description. Call again after any later edit.")
    async def design_finalize(draft_path: str, ctx: Context, cwd: str | None = None, session_id: str | None = None) -> dict:
        text = _read(draft_path, cwd)
        if not session_id:
            _, repo, branch = _session_for(cwd)
            session = wizard.find(repo, branch)
            if not session:
                raise ApiError("No design review for this branch. Call design_start first.")
            session_id = session["id"]
        res = wizard.finalize(session_id, text)
        out = {"marker": res["marker"], "session": res["session"],
               "next": f"Put this line in the PR description: {res['marker']}"}
        caps = ctx.client_capabilities
        if caps and caps.elicitation:
            from pydantic import Field, create_model
            consent = create_model("TraceConsent", attach=(bool, Field(
                default=False, title="Share this session's agent transcript with Station House?",
                description="Optional. Helps us study how experiments get designed. Keys, tokens, emails and file "
                            "contents are removed first, and only Station House admins can read it.")))
            try:
                result = await ctx.elicit("One optional question about research on the design process.", consent)
            except NoBackChannelError:
                return out
            if result.action == "accept" and result.data is not None and result.data.attach:
                from . import traces
                try:
                    out["trace"] = traces.attach(session_id, cwd=cwd or os.getcwd(), consent="elicitation")
                except (ApiError, OSError) as e:
                    out["trace"] = {"error": str(e)}
        return out

    return server


def main() -> int:
    try:
        build_server().run("stdio")
    except (AuthError, ApiError) as e:
        print(f"error: {e}", file=__import__("sys").stderr)
        return 1
    return 0
