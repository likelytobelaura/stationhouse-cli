import json

import pytest

from stationhouse_cli import cli

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
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, TREE)
    return api


# ---------------------------------------------------------------- task claim / release (and the dashboard card)

def test_task_claim_says_the_card_is_on_the_dashboard(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/claim")] = (200, {"people": [{"hfUsername": "me", "you": True}], "dashboard": "added"})
    code, out, _ = run(capsys, "task", "claim", "add-question")
    assert code == 0 and "You're on add-question." in out and "on your dashboard now" in out
    assert api.calls[0]["body"] is None


def test_task_claim_is_honest_when_the_card_didnt_land(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/claim")] = (200, {"people": [], "dashboard": "failed"})
    code, out, _ = run(capsys, "task", "claim", "add-question")
    assert code == 0 and "You're on add-question" in out and "couldn't put its card on your dashboard" in out


def test_top_level_claim_still_works_against_an_older_server(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/claim")] = (200, {"people": [{"hfUsername": "me", "you": True}]})
    code, out, _ = run(capsys, "claim", "add-question")
    assert code == 0 and "You're on add-question" in out and "dashboard" not in out


def test_task_release_reports_the_card_coming_off(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/release")] = (200, {"ok": True, "dashboard": "removed"})
    code, out, _ = run(capsys, "task", "release", "add-question")
    assert code == 0 and "Released add-question." in out and "Taken off your dashboard" in out


# ---------------------------------------------------------------- task create (subtask) / task request

def test_task_create_proposes_a_subtask_and_says_an_admin_decides(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/subtasks")] = (201, {"proposalId": "cli:add-question:malformed-data", "created": True, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "create", "add-question", "Malformed", "data", "-s", "Tests for malformed input")
    assert code == 0 and "Proposed \"Malformed data\" as a subtask of add-question" in out and "admin has to approve" in out
    assert api.calls[0]["body"] == {"title": "Malformed data", "summary": "Tests for malformed input"}


def test_task_create_twice_says_nothing_new_was_filed(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/subtasks")] = (200, {"proposalId": "x", "created": False, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "create", "add-question", "Malformed data", "-s", "s")
    assert code == 0 and "already proposed" in out and "Nothing new was filed" in out


def test_task_create_needs_a_summary_and_a_sane_parent_before_any_request(signed_in, api, capsys):
    with pytest.raises(SystemExit):
        cli.main(["task", "create", "add-question", "Title only"])
    capsys.readouterr()
    code, _, err = run(capsys, "task", "create", "../admin", "t", "-s", "s")
    assert code == 2 and "isn't a valid task id" in err and api.calls == []


def test_task_create_shows_the_servers_reason_for_a_refusal(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/x/subtasks")] = (409, {"error": "\"x\" is itself a subtask of \"y\"; only one level of subtasks is supported."})
    code, _, err = run(capsys, "task", "create", "x", "t", "-s", "s")
    assert code == 1 and "only one level" in err


def test_task_request_files_a_top_level_request(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/requests")] = (201, {"requestId": "r", "created": True, "notified": True, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "request", "Multilingual", "set", "-d", "Run it in five languages", "--atomic-unit", "one language")
    assert code == 0 and "Requested \"Multilingual set\"" in out and "Warning" not in out
    assert api.calls[0]["body"] == {"title": "Multilingual set", "description": "Run it in five languages", "atomicUnit": "one language"}


def test_task_request_warns_when_the_maintainers_were_not_emailed(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/requests")] = (201, {"requestId": "r", "created": True, "notified": False, "status": "PENDING"})
    code, out, _ = run(capsys, "task", "request", "Thing", "-d", "d")
    assert code == 0 and "recorded" in out and "email to the maintainers didn't go out" in out


def test_task_request_twice(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/projects/conspiracybench/requests")] = (200, {"requestId": "r", "created": False, "notified": False, "status": "PENDING"})
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
    assert tree.calls[0]["path"] == "/api/v1/projects/conspiracybench/tasks"


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


def test_explore_search_lists_matches_and_uses_no_model(tree, capsys):
    # --no-llm is still accepted (old scripts pass it) and changes nothing: explore never uses a model.
    for argv in (("explore", "malformed", "output", "from", "models"), ("explore", "--no-llm", "malformed", "output")):
        _, out, _ = run(capsys, *argv)
        assert out.startswith("scorer  Scorer hardening")
    assert [c["path"] for c in tree.calls] == ["/api/v1/projects/conspiracybench/tasks"] * 2  # nothing but the platform


def test_explore_json_is_facts_only(tree, capsys):
    _, out, _ = run(capsys, "explore", "--json", "who", "is", "on", "scorer")
    d = json.loads(out)
    assert d["intent"] == "who" and d["tasks"][0]["taskId"] == "scorer"
    assert not {"answer", "model", "modelNote"} & set(d)


def test_explore_with_nothing_matching_says_so(tree, capsys):
    _, out, _ = run(capsys, "explore", "quantum", "chromodynamics")
    assert "Nothing in the task list matches that." in out


def test_explore_without_a_question_points_people_to_the_assistant(tree, capsys):
    for argv in (("explore",), ("explore", "--json")):
        code, _, err = run(capsys, *argv)
        assert code == 1 and "needs a question" in err and "chat with the assistant" in err
    assert tree.calls == []


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
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, {"project": "p", "you": "me", "tasks": [TREE["tasks"][3]]})
    code, out, _ = run(capsys, "find-work")
    assert code == 0 and "Nothing open to pick up right now." in out and "task request" in out


def test_find_work_json_carries_the_reasons(tree, capsys):
    _, out, _ = run(capsys, "find-work", "--json")
    d = json.loads(out)
    assert d["picks"][0]["taskId"] == "add-question-edge" and "nobody is on it yet" in d["picks"][0]["reasons"]


# ---------------------------------------------------------------- tasks people don't pick up (make-account)

def test_find_work_and_unclaimed_explore_skip_tasks_the_server_says_arent_workable(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, {**TREE, "tasks": TREE["tasks"] + [MAKE_ACCOUNT]})
    _, out, _ = run(capsys, "find-work", "--limit", "10")
    assert "make-account" not in out and "add-question-edge" in out
    _, out, _ = run(capsys, "explore", "what is unclaimed")
    assert "make-account" not in out and "add-question-edge" in out


def test_an_older_server_without_the_workable_flag_still_works(tree, capsys):
    assert "workable" not in TREE["tasks"][0]
    _, out, _ = run(capsys, "find-work")
    assert "Do this one: add-question-edge" in out


def test_login_with_no_terminal_says_so_instead_of_a_traceback(monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt="": (_ for _ in ()).throw(EOFError))
    code, _, err = run(capsys, "login")
    assert code == 1 and "needs a terminal" in err and "Traceback" not in err


# ---------------------------------------------------------------- agents-md (for coding agents other than Claude Code)

def test_agents_md_writes_the_usage_rules_without_the_skill_frontmatter(tmp_path, capsys):
    code, out, _ = run(capsys, "agents-md", "--dir", str(tmp_path))
    text = (tmp_path / "AGENTS.md").read_text()
    assert code == 0 and "Wrote" in out
    assert text.startswith("<!-- stationhouse:begin") and "<!-- stationhouse:end -->" in text
    assert text.rstrip().endswith("<!-- stationhouse-design:end -->")  # the experiment-design rules follow
    assert "stationhouse find-work" in text and "Claim only after the contributor agrees" in text.replace("**", "")
    assert "name: stationhouse" not in text  # the Claude Code frontmatter is dropped


def test_agents_md_keeps_what_was_already_there_and_updates_in_place(tmp_path, capsys):
    (tmp_path / "AGENTS.md").write_text("# My project\n\nUse tabs.\n")
    run(capsys, "agents-md", "--dir", str(tmp_path))
    first = (tmp_path / "AGENTS.md").read_text()
    assert first.startswith("# My project\n\nUse tabs.\n\n<!-- stationhouse:begin")
    # Re-running replaces our section only: stale text inside is refreshed, other edits survive, nothing doubles.
    (tmp_path / "AGENTS.md").write_text(first.replace("stationhouse find-work", "OLD TEXT", 1) + "\n## After\n\nKeep me.\n")
    code, out, _ = run(capsys, "agents-md", "--dir", str(tmp_path))
    again = (tmp_path / "AGENTS.md").read_text()
    assert code == 0 and "Updated" in out
    assert again.count("stationhouse:begin") == 1 and "OLD TEXT" not in again
    assert again.startswith("# My project\n\nUse tabs.\n") and again.rstrip().endswith("Keep me.")
    run(capsys, "agents-md", "--dir", str(tmp_path))
    assert (tmp_path / "AGENTS.md").read_text() == again  # idempotent


def test_agents_md_print_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code, out, _ = run(capsys, "agents-md", "--print")
    assert code == 0 and out.startswith("<!-- stationhouse:begin") and not (tmp_path / "AGENTS.md").exists()


def test_the_agent_rules_cover_prs_projects_and_context(tmp_path, capsys):
    run(capsys, "agents-md", "--dir", str(tmp_path))
    text = (tmp_path / "AGENTS.md").read_text()
    for needed in ("stationhouse prs", "stationhouse projects", "--project <id>", "stationhouse context", "A PR is not a task",
                   "you are the assistant"):
        assert needed in text, needed
    assert 'stationhouse chat "' not in text  # coding agents interpret the facts themselves


def test_agents_md_needs_no_sign_in(tmp_path, capsys):
    assert run(capsys, "agents-md", "--dir", str(tmp_path))[0] == 0


# ---------------------------------------------------------------- live test, 2026-10-14: dashboards aren't claims

def _node(task_id, **kw):
    base = {"taskId": task_id, "title": task_id.replace("-", " ").title(), "parentTaskId": None, "childTaskIds": [],
            "summary": "", "scope": [], "completionCriteria": "", "available": True, "closed": False, "workable": True,
            "people": [], "mine": False, "claimedByYou": False, "recurring": False, "full": False, "pendingSubtasks": []}
    return {**base, **kw}


ME_ON_DASHBOARD = {"hfUsername": "me", "stale": False, "claimed": False, "you": True}
CROWD = [{"hfUsername": f"m{i}", "stale": False, "claimed": False} for i in range(59)]
DASHBOARD_TREE = {"project": "conspiracybench", "you": "me", "tasks": [
    _node("judge-injection-guard", mine=True, people=[ME_ON_DASHBOARD, {"hfUsername": "ada", "stale": False, "claimed": True}]),
    _node("daily-pr-review", mine=True, recurring=True, people=[ME_ON_DASHBOARD, *CROWD]),
    _node("csa-task-i-claimed", mine=True, claimedByYou=True, people=[{**ME_ON_DASHBOARD, "claimed": True}]),
]}


def test_find_work_offers_dashboard_tasks_and_daily_ones(signed_in, api, capsys):
    # The account had nearly every task on its dashboard, so find-work offered nothing; and the daily
    # "review a PR" task, on everyone's dashboard, was never offered to anyone.
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, DASHBOARD_TREE)
    _, out, _ = run(capsys, "find-work", "--json")
    picks = json.loads(out)["picks"]
    assert [p["taskId"] for p in picks] == ["daily-pr-review", "judge-injection-guard"]
    assert "daily task" in " ".join(picks[0]["reasons"]) and "59" not in " ".join(picks[0]["reasons"])
    assert "1 person is already on it" in " ".join(picks[1]["reasons"])  # ada's claim; your dashboard doesn't count


def test_explore_tells_claims_from_dashboards(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, DASHBOARD_TREE)
    _, out, _ = run(capsys, "explore", "what am I working on")
    assert "You're on 1 task." in out and "csa-task-i-claimed" in out and "judge-injection-guard" not in out
    _, out, _ = run(capsys, "explore", "what is unclaimed")
    assert "daily-pr-review" in out and "60 more have it on their dashboard" in out  # 59 others plus you
