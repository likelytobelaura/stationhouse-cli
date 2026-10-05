import json

import pytest

from stationhouse_cli import cli, llm

TREE = {
    "project": "conspiracybench", "you": "me",
    "tasks": [
        {"taskId": "add-question", "title": "Add a question", "parentTaskId": None, "childTaskIds": ["add-question-edge"],
         "summary": "Write a new benchmark question.", "scope": [], "completionCriteria": "A question is merged.",
         "available": True, "closed": False, "mine": False, "pendingSubtasks": [],
         "people": [{"hfUsername": "ada", "prNums": [12], "stale": False}]},
        {"taskId": "add-question-edge", "title": "Edge case questions", "parentTaskId": "add-question", "childTaskIds": [],
         "summary": "Questions that probe awkward inputs.", "scope": [], "completionCriteria": "Ten edge cases merged.",
         "available": True, "closed": False, "mine": False, "pendingSubtasks": [], "people": []},
        {"taskId": "scorer", "title": "Scorer hardening", "parentTaskId": None, "childTaskIds": [],
         "summary": "Make the scorer robust against malformed model output.", "scope": ["malformed data"],
         "completionCriteria": "Scorer tests pass.", "available": True, "closed": False, "mine": True,
         "pendingSubtasks": [{"title": "Fuzzing", "hfUsername": "bob"}],
         "people": [{"hfUsername": "me", "stale": False, "you": True}, {"hfUsername": "bob", "stale": True}]},
        {"taskId": "old-task", "title": "Old task", "parentTaskId": None, "childTaskIds": [], "summary": "", "scope": [],
         "completionCriteria": "", "available": True, "closed": True, "mine": False, "pendingSubtasks": [], "people": []},
    ],
}


MAKE_ACCOUNT = {"taskId": "make-account", "title": "Make an account", "parentTaskId": None, "childTaskIds": [], "summary": "",
                "scope": [], "completionCriteria": "", "available": True, "closed": False, "workable": False, "mine": False,
                "pendingSubtasks": [], "people": []}


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def tree(signed_in, api):
    api.routes[("GET", "/api/v1/tasks")] = (200, TREE)
    return api


# ---------------------------------------------------------------- task claim / release (and the dashboard card)

def test_task_claim_says_the_card_is_on_the_dashboard(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims")] = (200, {"people": [{"hfUsername": "me", "you": True}], "dashboard": "added"})
    code, out, _ = run(capsys, "task", "claim", "add-question")
    assert code == 0 and "You're on add-question." in out and "on your dashboard now" in out
    assert api.calls[0]["body"] == {"taskId": "add-question"}


def test_task_claim_is_honest_when_the_card_didnt_land(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims")] = (200, {"people": [], "dashboard": "failed"})
    code, out, _ = run(capsys, "task", "claim", "add-question")
    assert code == 0 and "You're on add-question" in out and "couldn't put its card on your dashboard" in out


def test_top_level_claim_still_works_against_an_older_server(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims")] = (200, {"people": [{"hfUsername": "me", "you": True}]})
    code, out, _ = run(capsys, "claim", "add-question")
    assert code == 0 and "You're on add-question" in out and "dashboard" not in out


def test_task_release_reports_the_card_coming_off(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims/release")] = (200, {"ok": True, "dashboard": "removed"})
    code, out, _ = run(capsys, "task", "release", "add-question")
    assert code == 0 and "Released add-question." in out and "Taken off your dashboard" in out


# ---------------------------------------------------------------- task create (subtask) / task request

def test_task_create_proposes_a_subtask_and_says_an_admin_decides(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/tasks")] = (201, {"proposalId": "cli:add-question:malformed-data", "created": True, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "create", "add-question", "Malformed", "data", "-s", "Tests for malformed input")
    assert code == 0 and "Proposed \"Malformed data\" as a subtask of add-question" in out and "admin has to approve" in out
    assert api.calls[0]["body"] == {"parentTaskId": "add-question", "title": "Malformed data", "summary": "Tests for malformed input"}


def test_task_create_twice_says_nothing_new_was_filed(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/tasks")] = (200, {"proposalId": "x", "created": False, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "create", "add-question", "Malformed data", "-s", "s")
    assert code == 0 and "already proposed" in out and "Nothing new was filed" in out


def test_task_create_needs_a_summary_and_a_sane_parent_before_any_request(signed_in, api, capsys):
    with pytest.raises(SystemExit):
        cli.main(["task", "create", "add-question", "Title only"])
    capsys.readouterr()
    code, _, err = run(capsys, "task", "create", "../admin", "t", "-s", "s")
    assert code == 2 and "isn't a valid task id" in err and api.calls == []


def test_task_create_shows_the_servers_reason_for_a_refusal(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/tasks")] = (409, {"error": "\"x\" is itself a subtask of \"y\"; only one level of subtasks is supported."})
    code, _, err = run(capsys, "task", "create", "x", "t", "-s", "s")
    assert code == 1 and "only one level" in err


def test_task_request_files_a_top_level_request(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/tasks/request")] = (201, {"requestId": "r", "created": True, "notified": True, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "request", "Multilingual", "set", "-d", "Run it in five languages", "--atomic-unit", "one language")
    assert code == 0 and "Requested \"Multilingual set\"" in out and "Warning" not in out
    assert api.calls[0]["body"] == {"title": "Multilingual set", "description": "Run it in five languages",
                                    "projectId": "conspiracybench", "atomicUnit": "one language"}


def test_task_request_warns_when_the_maintainers_were_not_emailed(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/tasks/request")] = (201, {"requestId": "r", "created": True, "notified": False, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "request", "Thing", "-d", "d")
    assert code == 0 and "recorded" in out and "email to the maintainers didn't go out" in out


def test_task_request_twice(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/tasks/request")] = (200, {"requestId": "r", "created": False, "notified": False, "status": "PENDING"})
    _, out, _ = run(capsys, "task", "request", "Thing", "-d", "d")
    assert "already requested" in out and "Warning" not in out


def test_admin_proposal_commands(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/proposals")] = (200, {"proposals": [
        {"proposalId": "cli:add-question:fuzzing", "parentTaskId": "add-question", "title": "Fuzzing", "summary": "Fuzz it.",
         "hfUsername": "bob", "prNum": None, "source": "CLI"}]})
    api.routes[("POST", "/api/v1/proposals/approve")] = (200, {"taskId": "add-question-fuzzing", "claimed": True, "dashboard": "added"})
    api.routes[("POST", "/api/v1/proposals/reject")] = (200, {"ok": True})
    _, out, _ = run(capsys, "task", "proposals")
    assert "cli:add-question:fuzzing" in out and "under add-question: Fuzzing  (by bob, CLI)" in out
    _, out, _ = run(capsys, "task", "approve", "cli:add-question:fuzzing")
    assert "Created add-question-fuzzing." in out and "proposer is on it" in out
    assert api.calls[-1]["body"] == {"proposalId": "cli:add-question:fuzzing"}
    assert "Rejected" in run(capsys, "task", "reject", "cli:add-question:fuzzing")[1]


def test_admin_commands_show_the_refusal_for_a_member(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/proposals/approve")] = (403, {"error": "Admins only."})
    code, _, err = run(capsys, "task", "approve", "x")
    assert code == 1 and "Admins only." in err


# ---------------------------------------------------------------- explore

def test_explore_who_lists_the_people_on_the_task_it_matched(tree, capsys):
    code, out, _ = run(capsys, "explore", "who", "else", "is", "working", "on", "scorer", "hardening?")
    assert code == 0 and out.startswith("scorer  Scorer hardening")
    assert "me (you) (active)" in out and "bob (stale)" in out
    assert "Proposed subtasks awaiting approval: Fuzzing (bob)" in out
    assert tree.calls[0]["path"] == "/api/v1/tasks?project=conspiracybench"


def test_explore_what_is_a_person_on(tree, capsys):
    _, out, _ = run(capsys, "explore", "what", "is", "Ada", "working", "on?")
    assert "ada is on 1 task." in out and "add-question  Add a question" in out and "scorer" not in out
    _, out, _ = run(capsys, "explore", "what", "is", "@zed", "doing")
    assert "I don't see zed on any task." in out


def test_explore_what_am_i_on(tree, capsys):
    _, out, _ = run(capsys, "explore", "what", "am", "I", "working", "on")
    assert "You're on 1 task." in out and "scorer" in out and "add-question  Add" not in out


def test_explore_unclaimed_skips_closed_and_can_be_narrowed_by_topic(tree, capsys):
    _, out, _ = run(capsys, "explore", "what", "is", "unclaimed?")
    assert "1 open task nobody is on." in out and "add-question-edge" in out and "old-task" not in out
    _, out, _ = run(capsys, "explore", "what", "edge", "case", "work", "is", "unclaimed")
    assert "add-question-edge" in out


def test_explore_subtasks_shows_the_family_from_either_end(tree, capsys):
    for q in ("what are the subtasks of add a question", "what is the edge case questions part of"):
        _, out, _ = run(capsys, "explore", *q.split())
        assert "add-question  Add a question" in out and "add-question-edge  Edge case questions  [subtask of Add a question]" in out


def test_explore_search_without_a_model_lists_matches_and_never_calls_one(tree, monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_local", lambda *a, **k: (_ for _ in ()).throw(AssertionError("model called")))
    _, out, _ = run(capsys, "explore", "--no-llm", "malformed", "output", "from", "models")
    assert out.startswith("scorer  Scorer hardening")


def test_explore_search_uses_the_local_model_when_it_answers(tree, monkeypatch, capsys):
    seen = {}

    def fake(question, tasks, known, **kw):
        seen.update(question=question, ids=[t["taskId"] for t in tasks], known=known, url=kw["url"])
        return "Use `scorer` for that.", None
    monkeypatch.setattr(llm, "ask_local", fake)
    _, out, _ = run(capsys, "explore", "which", "task", "covers", "malformed", "output?")
    assert out.startswith("Use `scorer` for that.") and "local model qwen3.5:4b" in out and "scorer  Scorer hardening" in out
    assert seen["url"].startswith("http://localhost") and seen["ids"][0] == "scorer"


def test_explore_search_falls_back_quietly_when_there_is_no_model(tree, monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_local", lambda *a, **k: (None, "no local model reachable at http://localhost:11434"))
    code, out, _ = run(capsys, "explore", "malformed", "output")
    assert code == 0 and out.startswith("scorer  Scorer hardening") and "reachable" not in out


def test_explore_says_when_it_dropped_an_ungrounded_model_answer(tree, monkeypatch, capsys):
    monkeypatch.setattr(llm, "ask_local", lambda *a, **k: (None, "the local model's answer cited a task that isn't in the list, so I dropped it"))
    _, out, _ = run(capsys, "explore", "malformed", "output")
    assert "I dropped it" in out and "scorer  Scorer hardening" in out


def test_explore_json(tree, capsys):
    _, out, _ = run(capsys, "explore", "--no-llm", "--json", "who", "is", "on", "scorer")
    d = json.loads(out)
    assert d["intent"] == "who" and d["tasks"][0]["taskId"] == "scorer" and d["answer"] is None


def test_explore_with_nothing_matching_says_so(tree, capsys):
    _, out, _ = run(capsys, "explore", "--no-llm", "quantum", "chromodynamics")
    assert "Nothing in the task list matches that." in out


def test_explore_mode_answers_several_questions_and_exits(tree, monkeypatch, capsys):
    lines = iter(["who is on scorer hardening", "refresh", "what is unclaimed", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(lines))
    code, out, _ = run(capsys, "explore", "--no-llm")
    assert code == 0 and "Explore mode" in out and "Reloaded 4 tasks." in out
    assert "bob (stale)" in out and "add-question-edge" in out
    assert [c["path"].split("?")[0] for c in tree.calls] == ["/api/v1/tasks", "/api/v1/tasks"]  # once, plus the refresh


def test_explore_mode_ends_cleanly_at_end_of_input(tree, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    assert run(capsys, "explore")[0] == 0


def test_explore_json_without_a_question_is_an_error(tree, capsys):
    code, _, err = run(capsys, "explore", "--json")
    assert code == 1 and "needs a question" in err


# ---------------------------------------------------------------- find-work

def test_find_work_picks_the_task_nobody_is_on_and_tells_you_how_to_take_it(tree, capsys):
    code, out, _ = run(capsys, "find-work")
    assert code == 0 and out.startswith("Do this one: add-question-edge  Edge case questions")
    assert "nobody is on it yet" in out and "a subtask of Add a question" in out
    assert "stationhouse task claim add-question-edge" in out
    assert "old-task" not in out and "scorer" not in out.split("Or:")[0]  # mine and closed are never suggested


def test_find_work_about_narrows_to_what_you_are_into(tree, capsys):
    _, out, _ = run(capsys, "work", "benchmark", "question", "--limit", "1")
    assert "Do this one: add-question" in out and 'matches "benchmark question"' in out and "Or:" not in out


def test_find_work_says_when_nothing_matches_and_still_offers_open_work(tree, capsys):
    _, out, _ = run(capsys, "find-work", "quantum", "chromodynamics")
    assert "Nothing matched that" in out and "Do this one:" in out


def test_find_work_with_nothing_open_points_at_task_request(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/tasks")] = (200, {"project": "p", "you": "me", "tasks": [TREE["tasks"][3]]})
    code, out, _ = run(capsys, "find-work")
    assert code == 0 and "Nothing open to pick up right now." in out and "task request" in out


def test_find_work_json_carries_the_reasons(tree, capsys):
    _, out, _ = run(capsys, "find-work", "--json")
    d = json.loads(out)
    assert d["picks"][0]["taskId"] == "add-question-edge" and "nobody is on it yet" in d["picks"][0]["reasons"]


# ---------------------------------------------------------------- the local model client

class FakeOllama:
    def __init__(self, reply):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer
        self.calls = []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.calls.append({"path": self.path, "body": json.loads(self.rfile.read(n))})
                data = json.dumps(reply).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()


def test_ask_local_sends_the_tasks_to_the_given_model_and_returns_a_grounded_answer():
    fake = FakeOllama({"message": {"content": "That's `scorer`."}})
    text, why = llm.ask_local("what covers scoring?", TREE["tasks"], {t["taskId"] for t in TREE["tasks"]}, url=fake.url, model="m")
    fake.server.shutdown()
    assert (text, why) == ("That's `scorer`.", None)
    body = fake.calls[0]["body"]
    assert fake.calls[0]["path"] == "/api/chat" and body["model"] == "m" and body["think"] is False
    assert body["options"]["temperature"] == 0
    prompt = body["messages"][1]["content"]
    assert "`scorer`: Scorer hardening" in prompt and "what covers scoring?" in prompt
    assert "ada" not in prompt and "bob" not in prompt  # who is on what stays out of the model's hands


def test_ask_local_drops_an_answer_that_cites_an_invented_or_no_task():
    ids = {"scorer"}
    for content in ("Try `scorer-v2`.", "Try `scorer` and `ghost`.", "No ids cited at all."):
        fake = FakeOllama({"message": {"content": content}})
        text, why = llm.ask_local("q", TREE["tasks"], ids, url=fake.url, model="m")
        fake.server.shutdown()
        assert text is None and "dropped" in why


def test_ask_local_without_ollama_returns_why_instead_of_raising():
    text, why = llm.ask_local("q", TREE["tasks"], {"scorer"}, url="http://127.0.0.1:9", model="m", timeout=2)
    assert text is None and "no local model reachable" in why


# ---------------------------------------------------------------- tasks people don't pick up (make-account)

def test_find_work_and_unclaimed_explore_skip_tasks_the_server_says_arent_workable(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/tasks")] = (200, {**TREE, "tasks": TREE["tasks"] + [MAKE_ACCOUNT]})
    _, out, _ = run(capsys, "find-work", "--limit", "10")
    assert "make-account" not in out and "add-question-edge" in out
    _, out, _ = run(capsys, "explore", "--no-llm", "what is unclaimed")
    assert "make-account" not in out and "add-question-edge" in out


def test_an_older_server_without_the_workable_flag_still_works(tree, capsys):
    assert "workable" not in TREE["tasks"][0]
    _, out, _ = run(capsys, "find-work")
    assert "Do this one: add-question-edge" in out


def test_login_with_no_terminal_says_so_instead_of_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    code, _, err = run(capsys, "login")
    assert code == 1 and "needs a terminal" in err and "Traceback" not in err
