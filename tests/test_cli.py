import json

from stationhouse_cli import cli
from stationhouse_cli.store import load_credentials

TASKS = {"tasks": [{"taskId": "add-question", "title": "Add a question", "summary": "Write one."}]}
PEOPLE = {"taskId": "add-question", "title": "Add a question",
          "people": [{"hfUsername": "ada", "prNums": [12], "stale": False},
                     {"hfUsername": "bob", "stale": True}]}


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_requires_login_before_any_call(capsys):
    code, _, err = run(capsys, "unclaimed")
    assert code == 1 and "stationhouse login" in err


def test_unclaimed_human_and_json(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/tasks/unclaimed")] = (200, TASKS)
    code, out, _ = run(capsys, "unclaimed")
    assert code == 0 and "add-question  Add a question" in out and "Write one." in out
    # --json works before and after the subcommand
    for argv in (("--json", "unclaimed"), ("unclaimed", "--json")):
        _, out, _ = run(capsys, *argv)
        assert json.loads(out) == TASKS
    assert api.calls[0]["path"] == "/api/v1/tasks/unclaimed?project=conspiracybench"


def test_requests_carry_both_tokens(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/me/claims")] = (200, {"claims": []})
    run(capsys, "mine")
    h = {k.lower(): v for k, v in api.calls[0]["headers"].items()}
    assert h["authorization"] == f"Bearer {signed_in['access_token']}"
    assert h["x-station-house-id-token"] == "id.tok.en"


def test_who_lists_people_with_staleness(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/tasks/add-question/people")] = (200, PEOPLE)
    _, out, _ = run(capsys, "who", "add-question")
    assert "ada  (active, #12)" in out and "bob  (stale)" in out


CONTEXT = {
    "task": {"taskId": "add-question", "projectId": "conspiracybench", "title": "Add a question", "completionCriteria": "c"},
    "parent": {"taskId": "dataset", "title": "Build the dataset"},
    "subtasks": [{"taskId": "add-question-es", "title": "Spanish questions"}],
    "people": [
        {"user": "ada", "claim": {"status": "ACTIVE", "source": "SELF", "released": False}, "threads": 0, "comments": 0},
        {"user": "melover24", "claim": None, "threads": 1, "comments": 3},
        {"user": "bob", "claim": {"status": "CLOSED", "source": "PR", "released": True}, "threads": 0, "comments": 1},
    ],
    "threads": [{
        "repo": "Station-house/ConspiracyBench", "num": 62, "kind": "PR", "title": "Add Stage 2 pilot", "status": "merged",
        "url": "https://huggingface.co/spaces/Station-house/ConspiracyBench/discussions/62", "author": "melover24",
        "lastActivityAt": "2026-10-03T22:23:17.000Z", "commentCount": 7,
        "recentComments": [{"author": "stationhouse", "at": "2026-10-03T22:23:17.000Z", "text": "looking good.\nmerging now."}],
    }],
    "mentions": [],
}


def test_context_shows_the_task_neighbourhood(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/tasks/add-question/context")] = (200, CONTEXT)
    code, out, _ = run(capsys, "context", "add-question")
    assert code == 0
    assert "part of: dataset" in out and "subtask: add-question-es" in out
    assert "ada  (on it)" in out and "melover24  (1 PR/thread, 3 comments)" in out and "bob  (released, 1 comment)" in out
    assert "PR #62  Add Stage 2 pilot  [merged] by melover24, 7 comments" in out
    assert "stationhouse: looking good. merging now." in out  # newlines folded
    _, out, _ = run(capsys, "context", "add-question", "--json")
    assert json.loads(out) == CONTEXT


def test_context_for_a_task_nothing_has_touched(signed_in, api, capsys):
    empty = {**CONTEXT, "parent": None, "subtasks": [], "people": [], "threads": []}
    api.routes[("GET", "/api/v1/tasks/add-question/context")] = (200, empty)
    _, out, _ = run(capsys, "context", "add-question")
    assert "Nobody on it yet." in out and "PRs and discussions" not in out


def test_claim_sends_only_the_task_and_lists_others(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims")] = (200, {"people": [{"hfUsername": "me", "you": True}, {"hfUsername": "ada"}]})
    code, out, _ = run(capsys, "claim", "add-question")
    assert code == 0 and "You're on add-question" in out and "ada" in out and "me " not in out
    assert api.calls[0]["body"] == {"taskId": "add-question"}  # identity comes from the token, never the body


def test_release_and_assign(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims/release")] = (200, {"ok": True})
    api.routes[("POST", "/api/v1/claims/assign")] = (200, {"ok": True})
    assert run(capsys, "release", "add-question")[0] == 0
    code, out, _ = run(capsys, "assign", "add-question", "@ada")
    assert code == 0 and "Assigned ada" in out
    assert api.calls[-1]["body"] == {"taskId": "add-question", "hfUsername": "ada"}


def test_assign_refused_for_non_admin_shows_server_message(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims/assign")] = (403, {"error": "Admins only."})
    code, _, err = run(capsys, "assign", "add-question", "ada")
    assert code == 1 and "Admins only." in err


def test_claim_without_verified_handle_shows_server_message(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/claims")] = (409, {"error": "Verify your Hugging Face username first."})
    code, _, err = run(capsys, "claim", "add-question")
    assert code == 1 and "Verify your Hugging Face username" in err


def test_missing_route_says_not_supported_yet(signed_in, api, capsys):
    code, _, err = run(capsys, "unclaimed")
    assert code == 1 and "doesn't support that yet" in err


def test_unreachable_server_is_a_clear_error(signed_in, monkeypatch, capsys):
    monkeypatch.setenv("STATIONHOUSE_API_URL", "http://127.0.0.1:9")
    code, _, err = run(capsys, "unclaimed")
    assert code == 1 and "Couldn't reach" in err


def test_bad_task_id_rejected_before_any_request(signed_in, api, capsys):
    code, _, err = run(capsys, "claim", "../admin")
    assert code == 2 and "isn't a valid task id" in err and api.calls == []


def test_login_stores_credentials_and_never_prints_the_password(monkeypatch, capsys):
    from stationhouse_cli import auth, cli as climod
    monkeypatch.setattr(climod, "login", lambda e, p: auth.save_credentials(
        {"email": e, "access_token": "a", "id_token": "i", "refresh_token": "r", "expires_at": 1}) or
        {"email": e})
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "Sup3r-Secret!")
    code, out, err = run(capsys, "login", "--email", "Laura@Example.com")
    assert code == 0 and "laura@example.com" in out
    assert "Sup3r-Secret!" not in out + err
    assert load_credentials()["email"] == "laura@example.com"


def test_login_rejects_non_email_without_prompting(monkeypatch, capsys):
    monkeypatch.setattr("getpass.getpass", lambda prompt="": (_ for _ in ()).throw(AssertionError("prompted")))
    code, _, err = run(capsys, "login", "--email", "nope")
    assert code == 1 and "email address" in err


def test_logout(signed_in, capsys):
    assert "Signed out" in run(capsys, "logout")[1]
    assert "weren't signed in" in run(capsys, "logout")[1]


def test_skills_install_copies_the_skill(tmp_path, capsys):
    code, out, _ = run(capsys, "skills-install", "--dir", str(tmp_path))
    assert code == 0
    text = (tmp_path / "stationhouse" / "SKILL.md").read_text()
    assert text.startswith("---\nname: stationhouse")
