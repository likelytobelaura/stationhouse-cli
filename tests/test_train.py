"""The welcome train: once, for a person at a terminal, and never in a script's or agent's way."""
import os
import sys

from stationhouse_cli import cli, train
from stationhouse_cli.store import load_settings


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def as_a_terminal(monkeypatch):
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(train.time, "sleep", lambda s: None)
    monkeypatch.delenv("STATIONHOUSE_NO_TRAIN", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.setattr(train.shutil, "get_terminal_size", lambda fallback=None: os.terminal_size((100, 30)))


def test_frames_cross_the_whole_terminal_without_wrapping():
    frames = train.frames(60)
    assert all(len(line) <= 59 for frame in frames for line in frame)
    assert not any(line.strip() for line in frames[0])  # starts off-screen to the right
    assert not any(line.strip() for line in frames[-1])  # and ends off-screen to the left
    assert any("STATION" in line for frame in frames for line in frame)
    assert len({tuple(line.strip() for line in f[:3]) for f in frames[5:20]}) > 1  # the smoke puffs as it goes


def test_the_first_run_in_a_terminal_plays_it_once(monkeypatch, capsys):
    as_a_terminal(monkeypatch)
    code, out, _ = run(capsys, "agents-md", "--print")
    assert code == 0 and "STATION" in out and "Welcome to Station House." in out
    assert "\033[?25h" in out  # the cursor is given back
    assert out.rstrip().endswith("<!-- stationhouse-design:end -->")  # and the command still ran
    assert load_settings()["welcomed"] is True
    _, again, _ = run(capsys, "agents-md", "--print")
    assert "STATION" not in again and "Welcome" not in again


def test_never_for_scripts_agents_json_or_when_turned_off(monkeypatch, capsys):
    for setup in (
        lambda: monkeypatch.setattr(sys.stdout, "isatty", lambda: False),  # piped / an agent's shell
        lambda: monkeypatch.setattr(sys.stdin, "isatty", lambda: False),
        lambda: monkeypatch.setenv("STATIONHOUSE_NO_TRAIN", "1"),
        lambda: monkeypatch.setenv("TERM", "dumb"),
    ):
        as_a_terminal(monkeypatch)
        setup()
        _, out, _ = run(capsys, "agents-md", "--print")
        assert "Welcome" not in out
    as_a_terminal(monkeypatch)
    assert train.should_welcome(json_output=True) is False
    assert "welcomed" not in load_settings()


def test_a_narrow_terminal_gets_no_train_but_still_the_command(monkeypatch, capsys):
    as_a_terminal(monkeypatch)
    monkeypatch.setattr(train.shutil, "get_terminal_size", lambda fallback=None: os.terminal_size((30, 20)))
    code, out, _ = run(capsys, "agents-md", "--print")
    assert code == 0 and "STATION" not in out and "stationhouse:end" in out


def test_ctrl_c_skips_the_train_not_the_command(monkeypatch, capsys):
    as_a_terminal(monkeypatch)

    def interrupt(seconds):
        raise KeyboardInterrupt
    monkeypatch.setattr(train.time, "sleep", interrupt)
    code, out, _ = run(capsys, "agents-md", "--print")
    assert code == 0 and "\033[?25h" in out and "stationhouse:end" in out


def test_train_command_replays_it(monkeypatch, capsys):
    as_a_terminal(monkeypatch)
    for _ in range(2):
        _, out, _ = run(capsys, "train")
        assert "STATION" in out
    monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
    assert "only runs in a terminal" in run(capsys, "train")[1]
