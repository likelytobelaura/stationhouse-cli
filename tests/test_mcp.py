import json

import anyio
import pytest

pytest.importorskip("mcp")

from mcp import Client, types  # noqa: E402

from stationhouse_cli import mcp_server  # noqa: E402

SID = "ds_" + "B" * 22
QUESTIONS = [{"id": f"q{i}", "stage": "claim", "quote": "", "question": f"Question {i}?", "why": "w"} for i in (1, 2, 3)]
ROUND = {"n": 1, "focus": "The claim and the controls", "answers": None,
         "feedback": {"summary": "s", "questions": QUESTIONS, "blocking": [], "resolved": []}}
SESSION = {"id": SID, "repo": "hf:Station-house/ConspiracyBench", "roundsAnswered": 0, "answerUrl": f"https://x/wizard/{SID}"}
GOOD = "A real answer that is comfortably over forty characters long."


@pytest.fixture
def design_file(tmp_path):
    p = tmp_path / "EXPERIMENT.md"
    p.write_text("# Claim\nx\n")
    return p


@pytest.fixture
def wizard_api(api, signed_in):
    api.routes[("POST", f"/api/v1/design/sessions/{SID}/rounds")] = (200, {"session": SESSION, "round": ROUND})
    api.routes[("POST", f"/api/v1/design/sessions/{SID}/answers")] = (200, {"session": {**SESSION, "roundsAnswered": 1}, "round": ROUND})
    return api


def call(tool, args, elicit=None, mode="legacy"):
    # "legacy" = the initialize handshake Claude Code's stdio servers use; elicitation rides on it.
    async def run():
        kwargs = {"elicitation_callback": elicit} if elicit else {}
        async with Client(mcp_server.build_server(), mode=mode, **kwargs) as client:
            res = await client.call_tool(tool, args)
            return res, json.loads(res.content[0].text) if res.content and not res.is_error else None
    return anyio.run(run)


def test_the_contributor_answers_in_a_form_and_the_agent_gets_their_answers(wizard_api, design_file):
    seen = []

    async def elicit(_ctx, params):
        seen.append(params)
        return types.ElicitResult(action="accept", content={q["id"]: GOOD for q in QUESTIONS})

    res, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID}, elicit)
    assert not res.is_error, res
    schema = seen[0].requested_schema
    assert [schema["properties"][q]["title"] for q in ("q1", "q2", "q3")] == ["Question 1?", "Question 2?", "Question 3?"]
    assert out["contributorAnswers"] == {q["id"]: GOOD for q in QUESTIONS}
    sent = wizard_api.calls[-1]
    assert sent["path"].endswith("/answers") and sent["body"]["via"] == "mcp-elicit"


def test_short_answers_are_asked_again_before_anything_is_sent(wizard_api, design_file):
    replies = [{"q1": "short", "q2": GOOD, "q3": GOOD}, {q["id"]: GOOD for q in QUESTIONS}]

    async def elicit(_ctx, params):
        return types.ElicitResult(action="accept", content=replies.pop(0))

    res, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID}, elicit)
    assert out["contributorAnswers"]["q1"] == GOOD
    assert sum(c["path"].endswith("/answers") for c in wizard_api.calls) == 1


def test_answers_graded_vague_are_asked_again_with_what_is_missing(wizard_api, design_file):
    path = f"/api/v1/design/sessions/{SID}/answers"
    ok = wizard_api.routes[("POST", path)]
    wizard_api.routes[("POST", path)] = (400, {"error": "Some answers don't commit to anything yet.", "code": "vague",
                                               "problems": [{"id": "q2", "problem": "Say which threshold."}]})
    messages = []

    async def elicit(_ctx, params):
        messages.append(params.message)
        if len(messages) == 2:
            wizard_api.routes[("POST", path)] = ok
        return types.ElicitResult(action="accept", content={q["id"]: GOOD for q in QUESTIONS})

    _, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID}, elicit)
    assert len(messages) == 2 and "q2: Say which threshold." in messages[1]
    assert out["roundsAnswered"] == 1


def test_an_unchanged_design_asks_the_agent_to_check_with_the_contributor(wizard_api, design_file):
    wizard_api.routes[("POST", f"/api/v1/design/sessions/{SID}/rounds")] = (409, {"error": "The design hasn't changed since round 1.", "code": "unchanged"})
    _, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID})
    assert out["status"] == "unchanged" and "no_change_reason" in out["instructions"]


def test_finalize_returns_the_closing_review_for_the_contributor(wizard_api, design_file):
    verdict = {"summary": "Solid.", "stages": [], "blockers": [], "preregistration": {}}
    wizard_api.routes[("POST", f"/api/v1/design/sessions/{SID}/finalize")] = (200, {"marker": f"Design-Session: {SID}", "session": SESSION, "verdict": verdict})
    _, out = call("design_finalize", {"draft_path": str(design_file), "session_id": SID})
    assert out["closingReview"] == verdict and "closing review" in out["next"]


def test_a_declined_form_stops_the_agent_without_answers(wizard_api, design_file):
    async def elicit(_ctx, params):
        return types.ElicitResult(action="decline")

    _, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID}, elicit)
    assert out["status"] == "decline" and "End your turn" in out["instructions"]
    assert not any(c["path"].endswith("/answers") for c in wizard_api.calls)


def test_without_forms_the_agent_is_told_to_stop_and_send_the_link(wizard_api, design_file):
    _, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID})
    assert out["answerUrl"] == SESSION["answerUrl"]
    assert out["instructions"].startswith("STOP") and "Question 1?" in out["questions"]
    assert not any(c["path"].endswith("/answers") for c in wizard_api.calls)


def test_a_connection_without_a_back_channel_gets_the_link(wizard_api, design_file):
    async def elicit(_ctx, params):  # declared, but the 2026 stateless protocol can't deliver it
        return types.ElicitResult(action="accept", content={q["id"]: GOOD for q in QUESTIONS})

    _, out = call("design_submit_round", {"draft_path": str(design_file), "session_id": SID}, elicit, mode="auto")
    assert out["instructions"].startswith("STOP") and out["answerUrl"] == SESSION["answerUrl"]
    assert not any(c["path"].endswith("/answers") for c in wizard_api.calls)


def test_there_is_no_tool_for_submitting_answers():
    async def run():
        async with Client(mcp_server.build_server()) as client:
            return [t.name for t in (await client.list_tools()).tools]
    assert sorted(anyio.run(run)) == ["design_finalize", "design_start", "design_status", "design_submit_round"]
