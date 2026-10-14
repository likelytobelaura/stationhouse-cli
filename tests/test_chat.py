"""Chat mode against the fake API: what the member types, what goes to /api/v1/agent/chat, and that a
suggested claim only happens on their yes, made by the CLI itself."""
import builtins

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
    api.routes[("POST", "/api/v1/claims")] = (200, {"people": [{"hfUsername": "me", "you": True}], "dashboard": "added"})
    prompts = typing(monkeypatch, "what could I work on?", "y", "exit")
    assert cli.main([]) == 0
    out = capsys.readouterr().out
    assert "Try `add-question`" in out and "You're on add-question." in out
    assert any(p.startswith("Claim add-question") for p in prompts)
    body = chat_calls(api)[0]["body"]
    assert body == {"messages": [{"role": "user", "text": "what could I work on?"}], "project": "conspiracybench"}
    assert [c["body"] for c in api.calls if c["path"] == "/api/v1/claims"] == [{"taskId": "add-question"}]


def test_saying_no_changes_nothing(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, REPLY)
    typing(monkeypatch, "what could I work on?", "n")
    cli.main([])
    assert "left as it is" in capsys.readouterr().out
    assert not [c for c in api.calls if c["path"] == "/api/v1/claims"]


def test_the_conversation_carries_over_and_clear_forgets_it(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (200, {**REPLY, "suggestedActions": []})
    typing(monkeypatch, "first", "second", "/clear", "third")
    cli.main([])
    sent = [c["body"]["messages"] for c in chat_calls(api)]
    assert [m["role"] for m in sent[1]] == ["user", "assistant", "user"]
    assert sent[1][1]["text"] == REPLY["reply"] and sent[1][2]["text"] == "second"
    assert sent[2] == [{"role": "user", "text": "third"}]


def test_slash_runs_a_normal_command(signed_in, api, capsys, monkeypatch):
    api.routes[("GET", "/api/v1/tasks/add-question/people")] = (200, {"title": "Add a question", "people": []})
    typing(monkeypatch, "/who add-question", "/who", "/nope")
    cli.main([])
    out = capsys.readouterr()
    assert "Add a question (add-question)" in out.out
    assert not chat_calls(api)  # commands never go to the assistant


def test_falls_back_to_the_task_list_when_the_assistant_is_off(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (503, {"error": "The assistant isn't switched on yet.", "code": "agentOff"})
    api.routes[("GET", "/api/v1/tasks")] = (200, TREE)
    typing(monkeypatch, "who is on add question?")
    cli.main([])
    out = capsys.readouterr().out
    assert "isn't switched on yet" in out and "add-question" in out


def test_falls_back_when_the_model_provider_fails(signed_in, api, capsys, monkeypatch):
    api.routes[("POST", "/api/v1/agent/chat")] = (502, {"error": "The assistant couldn't answer right now."})
    api.routes[("GET", "/api/v1/tasks")] = (200, TREE)
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


def test_signed_out_is_told_to_log_in(api, capsys, monkeypatch):
    typing(monkeypatch, "hello")
    cli.main([])
    out = capsys.readouterr().out
    assert "type /login first" in out and "Type /login, then ask again" in out
    assert not api.calls
