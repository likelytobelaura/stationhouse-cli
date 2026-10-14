"""Chat mode against the fake API: what the member types, what goes to /api/v1/agent/chat, and that a
suggested claim only happens on their yes, made by the CLI itself."""
import builtins

import pytest

from stationhouse_cli import cli

TREE = {"project": "conspiracybench", "you": "me", "tasks": [
    {"taskId": "add-question", "title": "Add a question", "parentTaskId": None, "childTaskIds": [], "summary": "Write one.",
     "scope": [], "completionCriteria": "c", "available": True, "closed": False, "workable": True, "people": [],
     "mine": False, "pendingSubtasks": []}]}
REPLY = {"reply": "Try `add-question`: nobody is on it.", "taskIds": ["add-question"],
         "suggestedActions": [{"type": "claim", "taskId": "add-question", "title": "Add a question", "reason": "Nobody is on it."}]}


def typing(monkeypatch, *lines):
    """Feeds the prompts these lines in order, then Ctrl-D; returns the prompts that were shown."""
    queue, prompts = list(lines), []

    def fake_input(prompt=""):
        prompts.append(prompt)
        if not queue:
            raise EOFError
        return queue.pop(0)
    monkeypatch.setattr(builtins, "input", fake_input)
    return prompts


def chat_calls(api):
    return [c for c in api.calls if c["path"] == "/api/v1/agent/chat"]


def test_bare_command_opens_the_chat_and_claims_only_on_yes(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, REPLY)
    api.routes[("POST", "/api/v1/projects/conspiracybench/tasks/add-question/claim")] = (200, {"people": [{"hfUsername": "me", "you": True}], "dashboard": "added"})
    prompts = typing(monkeypatch, "what could I work on?", "y", "exit")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Try `add-question`" in out and "You're on add-question." in out
    assert any(p.startswith("Claim add-question") for p in prompts)
    body = chat_calls(api)[0]["body"]
    assert body == {"messages": [{"role": "user", "text": "what could I work on?"}], "project": "conspiracybench"}
    # The CLI makes the claim itself, with no body: who comes from the token, which task from the path.
    assert [c["body"] for c in api.calls if c["path"] == "/api/v1/projects/conspiracybench/tasks/add-question/claim"] == [None]


def test_saying_no_changes_nothing(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, REPLY)
    typing(monkeypatch, "what could I work on?", "n")
    cli.main([])
    assert "left as it is" in capsys.readouterr().out
    assert not [c for c in api.calls if c["path"] == "/api/v1/projects/conspiracybench/tasks/add-question/claim"]


def test_the_conversation_carries_over_and_clear_forgets_it(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, {**REPLY, "suggestedActions": []})
    typing(monkeypatch, "first", "second", "/clear", "third")
    cli.main([])
    sent = [c["body"]["messages"] for c in chat_calls(api)]
    assert [m["role"] for m in sent[1]] == ["user", "assistant", "user"]
    assert sent[1][1]["text"] == REPLY["reply"] and sent[1][2]["text"] == "second"
    assert sent[2] == [{"role": "user", "text": "third"}]


def test_slash_runs_a_normal_command(signed_in, api, capsys, monkeypatch):
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks/add-question/people")] = (200, {"title": "Add a question", "people": []})
    typing(monkeypatch, "/who add-question", "/who", "/nope")
    cli.main([])
    out = capsys.readouterr()
    assert "Add a question (add-question)" in out.out
    assert not chat_calls(api)  # commands never go to the assistant


def test_falls_back_to_the_task_list_when_the_assistant_is_off(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (503, {"error": "The assistant isn't switched on yet.", "code": "agentOff"})
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, TREE)
    typing(monkeypatch, "who is on add question?")
    cli.main([])
    out = capsys.readouterr().out
    assert "isn't switched on yet" in out and "add-question" in out


def test_falls_back_when_the_model_provider_fails(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (502, {"error": "The assistant couldn't answer right now."})
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks")] = (200, TREE)
    typing(monkeypatch, "who is on add question?")
    cli.main([])
    out = capsys.readouterr().out
    assert "couldn't answer right now" in out and "Answering from the task list" in out and "add-question" in out


def test_one_shot_question(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, {**REPLY, "suggestedActions": []})
    code = cli.main(["chat", "what", "next?"])
    out = capsys.readouterr().out
    assert code == 0 and "Try `add-question`" in out
    assert chat_calls(api)[0]["body"]["messages"] == [{"role": "user", "text": "what next?"}]


def test_one_shot_from_an_agent_prints_the_suggestion_and_changes_nothing(signed_in, api, capsys, monkeypatch):
    # A coding agent runs it with no terminal: no y/N prompt to hang on, just a line to act on.
    api.routes[("POST", "/api/v1/agent/chat")] = (200, REPLY)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    asked = typing(monkeypatch)
    code = cli.main(["chat", "what could I work on?"])
    out = capsys.readouterr().out
    assert code == 0 and asked == []
    assert "Suggested: claim add-question" in out and "stationhouse claim add-question" in out
    assert not [c for c in api.calls if c["path"] == "/api/v1/projects/conspiracybench/tasks/add-question/claim"]


def test_typing_a_question_asks_the_assistant(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, {**REPLY, "suggestedActions": []})
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    for argv, text in (
        (["what", "could", "I", "work", "on?"], "what could I work on?"),
        (["who", "is", "working", "on", "the", "scorer?"], "who is working on the scorer?"),  # starts with a command word
        (["ask", "any PRs to review?"], "any PRs to review?"),
    ):
        assert cli.main(argv) == 0
        assert chat_calls(api)[-1]["body"]["messages"] == [{"role": "user", "text": text}]
    assert "Try `add-question`" in capsys.readouterr().out


def test_commands_still_run_as_commands(signed_in, api, capsys):
    api.routes[("GET", "/api/v1/projects/conspiracybench/tasks/add-question/people")] = (200, {"title": "Add a question", "people": []})
    assert cli.main(["who", "add-question"]) == 0
    assert "Add a question (add-question)" in capsys.readouterr().out
    assert not chat_calls(api)


def test_a_typo_is_an_error_not_a_question(signed_in, api, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["claimm", "add-question"])
    assert e.value.code == 2 and "did you mean `claim`" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        cli.main(["task", "create", "add-question", "Title", "only"])  # a real command's own error, not a question
    assert api.calls == []


def test_help_is_help(signed_in, api, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(["help"])
    assert e.value.code == 0 and "chat with the assistant" in " ".join(capsys.readouterr().out.split())
    assert api.calls == []


def test_signed_out_is_told_to_log_in(api, capsys, monkeypatch):
    typing(monkeypatch, "hello")
    cli.main([])
    out = capsys.readouterr().out
    assert "type /login first" in out and "Type /login, then ask again" in out
    assert not api.calls
