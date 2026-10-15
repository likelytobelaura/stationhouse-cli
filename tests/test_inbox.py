from stationhouse_cli import chat, cli, inbox

REPO = "Station-house/ConspiracyBench"


def item(num, kind="pr-feedback", repo=REPO, **extra):
    return {"id": f"{repo}#{num}", "kind": kind, "repo": repo, "num": num, "threadKind": "PR", "title": f"PR {num}",
            "url": f"https://huggingface.co/spaces/{repo}/discussions/{num}", "status": "open", "taskId": "add-question",
            "from": ["bob"], "count": 1, "waitingSince": "2026-10-14T10:00:00.000Z", "projectId": "conspiracybench",
            "latest": {"author": "bob", "at": "2026-10-14T10:00:00.000Z", "text": "Question 4 has the wrong label."}, **extra}


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_inbox_lists_what_is_waiting_with_links(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": True, "followUps": [item(348), item(203, kind="mention")]})
    code, out, _ = run(capsys, "inbox")
    assert code == 0
    assert "2 follow-ups waiting on you" in out
    assert "#348  PR 348  [add-question]" in out and "Feedback on your PR: bob, 1 comment" in out
    assert "bob: Question 4 has the wrong label." in out and "discussions/348" in out
    assert "Mentioned you" in out
    # Works in the project picked (conftest picks conspiracybench).
    assert api.calls[0]["path"] == "/api/v1/me/follow-ups?project=conspiracybench"


def test_inbox_looks_everywhere_when_no_project_is_picked(signed_in, api, capsys, monkeypatch):
    monkeypatch.delenv("STATIONHOUSE_PROJECT")
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": True, "followUps": []})
    code, out, _ = run(capsys, "todo")
    assert code == 0 and "Nothing is waiting on you." in out
    assert api.calls[0]["path"] == "/api/v1/me/follow-ups"


def test_inbox_asks_an_unverified_member_to_verify(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": False, "followUps": []})
    _, out, _ = run(capsys, "inbox")
    assert "Verify your Hugging Face account" in out


def test_inbox_done_by_number_marks_it_up_to_its_latest_comment(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": True, "followUps": [item(348)]})
    api.routes[("POST", "/api/v1/me/follow-ups/done")] = (200, {"ok": True})
    code, out, _ = run(capsys, "inbox", "done", "#348")
    assert code == 0 and "Marked #348 done" in out
    assert api.calls[1]["body"] == {"id": f"{REPO}#348", "upTo": "2026-10-14T10:00:00.000Z"}


def test_inbox_done_needs_the_full_id_when_a_number_is_in_two_repos(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": True, "followUps": [item(12), item(12, repo="Station-house/Other")]})
    code, _, err = run(capsys, "inbox", "done", "12")
    assert code == 1 and "more than one repo" in err
    code, _, err = run(capsys, "inbox", "done", "99")
    assert code == 1 and "Nothing in your inbox is 99" in err


def test_inbox_against_an_older_server(signed_in, api, capsys):
    code, _, err = run(capsys, "inbox")
    assert code == 1 and "doesn't have the inbox yet" in err


def test_a_question_starting_with_inbox_still_goes_to_the_assistant(signed_in, api, capsys):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, {"reply": "Do these follow-ups first: #348.", "suggestedActions": [], "taskIds": []})
    code, out, _ = run(capsys, "inbox", "what", "needs", "me?")
    assert code == 0 and "Do these follow-ups first" in out


def test_chat_greeting_nudges_when_follow_ups_are_waiting(signed_in, api, monkeypatch):
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": True, "followUps": [item(348), item(349)]})
    assert "2 follow-ups are waiting on you: /inbox" in cli._inbox_line("conspiracybench")
    api.routes[("GET", "/api/v1/me/follow-ups")] = (200, {"verified": True, "followUps": []})
    assert cli._inbox_line(None) == ""
    del api.routes[("GET", "/api/v1/me/follow-ups")]
    assert cli._inbox_line(None) == ""  # an older server: no nudge, no error
    assert "/inbox" in chat.HELP


def test_waiting_reads_like_a_person_would_say_it():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
    assert inbox.waiting("2026-10-15T11:30:00Z", now) == "under an hour"
    assert inbox.waiting("2026-10-15T09:00:00.000Z", now) == "3 hours"
    assert inbox.waiting("2026-10-10T12:00:00Z", now) == "5 days"
