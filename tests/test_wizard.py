import json
import subprocess
from pathlib import Path

import pytest

from stationhouse_cli import cli, gate, traces, wizard

FIXTURES = json.loads((Path(__file__).parent / "design_hash_fixtures.json").read_text(encoding="utf-8"))
SID = "ds_" + "A" * 22
DESIGN = "# Claim\nPressure raises endorsement.\n\n## Controls\nBase model.\n"
REPOS = [{"repo": "hf:Station-house/ConspiracyBench", "projectId": "conspiracybench"},
         {"repo": "github:Station-House/marin-neuron-hunt", "projectId": "marin-neuron-atlas"}]


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path):
    """A Neuron Hunt clone with origin/main one commit behind a branch that adds a request."""
    root = tmp_path / "hunt"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.email", "t@example.com")
    git(root, "config", "user.name", "t")
    git(root, "remote", "add", "origin", "https://github.com/Station-House/marin-neuron-hunt.git")
    (root / "README.md").write_text("hunt\n")
    git(root, "add", ".")
    git(root, "commit", "-qm", "init")
    git(root, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(root, "checkout", "-qb", "req/L40")
    (root / "requests" / "L40-N1-001").mkdir(parents=True)
    (root / "requests" / "L40-N1-001" / "DESIGN.md").write_text(DESIGN)
    git(root, "add", ".")
    git(root, "commit", "-qm", "request")
    return root


def session(**over):
    base = {"id": SID, "repo": "github:Station-House/marin-neuron-hunt", "branch": "req/L40", "title": "t", "status": "complete",
            "roundsAnswered": 3, "currentRound": 3, "finalSha256": wizard.design_hash(DESIGN), "answerUrl": "https://x/wizard/" + SID}
    return {**base, **over}


@pytest.fixture
def hunt_api(api, signed_in):
    api.routes[("GET", "/api/v1/design/repos")] = (200, {"repos": REPOS})
    api.routes[("POST", "/api/v1/design/gate-events")] = (201, {"ok": True})
    return api


# --- pure rules -----------------------------------------------------------------------------------

@pytest.mark.parametrize("f", FIXTURES, ids=[f["name"] for f in FIXTURES])
def test_design_hash_matches_the_shared_fixtures(f):
    assert wizard.design_hash(f["text"]) == f["sha256"]


def test_repo_ids_from_remotes():
    assert wizard.repo_from_url("https://huggingface.co/spaces/Station-house/ConspiracyBench") == "hf:Station-house/ConspiracyBench"
    assert wizard.repo_from_url("git@hf.co:spaces/Station-house/ConspiracyBench.git") == "hf:Station-house/ConspiracyBench"
    assert wizard.repo_from_url("git@github.com:Station-House/marin-neuron-hunt.git") == "github:Station-House/marin-neuron-hunt"
    assert wizard.repo_from_url("https://huggingface.co/datasets/x/y") is None


@pytest.mark.parametrize("files,kind", [
    (["requests/L40-N25546-002.yaml"], "experiment"),
    (["experiment_requests/test-prompt/evidence/EXPERIMENT.md"], "experiment"),
    (["completed_experiments/x/results/a.json", "completed_experiments/x/summary.json"], "experiment"),
    (["questions/cb-0400.json"], "other"),
    (["explanations/L40-N1.md", "README.md"], "other"),
    (["scripts/run.py"], None),
    (["completed_experiments/x/results/a.json"], None),
])
def test_classify_files(files, kind):
    assert wizard.classify_files(files)[0] == kind


def test_design_doc_is_the_request_design_or_the_nearest_experiment_md(tmp_path):
    (tmp_path / "requests" / "r1").mkdir(parents=True)
    (tmp_path / "requests" / "r1" / "DESIGN.md").write_text("x")
    assert wizard.find_design_doc(["requests/r1/DESIGN.md"], tmp_path) == tmp_path / "requests/r1/DESIGN.md"
    exp = tmp_path / "completed_experiments" / "e1"
    (exp / "results").mkdir(parents=True)
    (exp / "EXPERIMENT.md").write_text("x")
    (exp / "results" / "a.csv").write_text("x")
    assert wizard.find_design_doc(["completed_experiments/e1/results/a.csv"], tmp_path) == exp / "EXPERIMENT.md"
    assert wizard.find_design_doc(["scripts/a.py"], tmp_path) is None


def test_pr_body_design_section():
    body = "Intro\n\n## Design\nClaim: x\nControls: y\n\n## Results\nnone"
    assert wizard.pr_body_design(body) == "Claim: x\nControls: y"
    assert wizard.pr_body_design("no design here") is None


def test_answer_problems_mirror_the_platform_rule():
    qs = [{"id": "q1"}, {"id": "q2"}, {"id": "q3"}]
    probs = wizard.answer_problems(qs, {"q1": "x" * 40, "q2": "n/a:", "q3": "short"})
    assert set(probs) == {"q2", "q3"}


# --- the push gate --------------------------------------------------------------------------------

def test_gate_blocks_an_experiment_with_no_review(repo, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": []})
    out = gate.check(repo)
    assert out["outcome"] == "blocked"
    assert "Experimentation Wizard" in out["message"] and "never answer" in out["message"]
    logged = [c for c in hunt_api.calls if c["path"] == "/api/v1/design/gate-events"]
    assert logged[-1]["body"]["outcome"] == "blocked"
    assert logged[-1]["body"]["repo"] == "github:Station-House/marin-neuron-hunt"


def test_gate_passes_a_finalized_matching_design(repo, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": [session()]})
    out = gate.check(repo)
    assert out["outcome"] == "passed"
    assert out["marker"] == f"Design-Session: {SID}"


@pytest.mark.parametrize("over,why", [
    ({"roundsAnswered": 2}, "2 of 3 rounds"),
    ({"finalSha256": None}, "isn't finalized"),
    ({"finalSha256": "0" * 64}, "changed after the design was finalized"),
])
def test_gate_blocks_unfinished_or_changed_reviews(repo, hunt_api, over, why):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": [session(**over)]})
    out = gate.check(repo)
    assert out["outcome"] == "blocked" and why in out["message"]


def test_gate_lets_non_experiments_through_and_logs_them(repo, hunt_api):
    git(repo, "checkout", "-qb", "docs", "main")
    (repo / "README.md").write_text("better\n")
    git(repo, "commit", "-qam", "docs")
    out = gate.check(repo)
    assert out["outcome"] == "not-experiment"
    assert hunt_api.calls[-1]["body"]["outcome"] == "not-experiment"


def test_gate_ignores_repos_that_are_not_projects(tmp_path, hunt_api):
    git(tmp_path, "init", "-q")
    git(tmp_path, "remote", "add", "origin", "https://github.com/someone/else.git")
    assert gate.check(tmp_path)["outcome"] == "not-project"


def test_repo_list_comes_from_the_platform_and_survives_an_outage(signed_in, api, monkeypatch):
    api.routes[("GET", "/api/v1/design/repos")] = (200, {"repos": REPOS})
    assert wizard.project_repo(["github:station-house/MARIN-neuron-hunt"])["projectId"] == "marin-neuron-atlas"
    # A day later the platform is down: the last list still counts.
    monkeypatch.setattr(wizard, "REPOS_CACHE_SECONDS", -1)
    api.routes[("GET", "/api/v1/design/repos")] = (503, {"error": "down"})
    assert wizard.project_repo(["hf:Station-house/ConspiracyBench"])["repo"] == "hf:Station-house/ConspiracyBench"


def test_no_repo_list_at_all_means_no_gate(repo, signed_in, api):
    api.routes[("GET", "/api/v1/design/repos")] = (503, {"error": "down"})
    assert wizard.known_repos() == []
    assert gate.check(repo)["outcome"] == "not-project"


def test_gate_fails_open_when_the_platform_is_down(repo, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (503, {"error": "down"})
    assert gate.check(repo)["outcome"] == "unchecked"


def test_hook_blocks_a_push_and_ignores_other_commands(repo, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": []})
    code, _ = gate.hook(json.dumps({"tool_input": {"command": "ls -la"}, "cwd": str(repo)}))
    assert code == 0
    code, msg = gate.hook(json.dumps({"tool_input": {"command": "git push -u origin req/L40"}, "cwd": str(repo)}))
    assert code == 2 and msg.startswith("Station House:")
    code, _ = gate.hook(json.dumps({"tool_input": {"command": f"cd {repo} && gh pr create --fill"}, "cwd": str(repo)}))
    assert code == 2


def test_hook_reads_an_hf_upload(tmp_path, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": []})
    out = tmp_path / "exp"
    (out / "results").mkdir(parents=True)
    (out / "EXPERIMENT.md").write_text(DESIGN)
    (out / "results" / "summary.json").write_text("{}")
    cmd = f"hf upload Station-house/ConspiracyBench {out} completed_experiments/exp --repo-type space --create-pr"
    code, msg = gate.hook(json.dumps({"tool_input": {"command": cmd}, "cwd": str(tmp_path)}))
    assert code == 2, msg
    q = next(c for c in hunt_api.calls if c["path"].startswith("/api/v1/design/sessions"))
    assert "hf%3AStation-house%2FConspiracyBench" in q["path"]


def test_hooks_install_writes_a_pre_push_hook(repo, capsys):
    assert cli.main(["hooks", "install", "--dir", str(repo)]) == 0
    hook = repo / ".git" / "hooks" / "pre-push"
    assert "design gate --pre-push" in hook.read_text()
    (repo / ".git" / "hooks" / "pre-push").write_text("#!/bin/sh\necho mine\n")
    assert cli.main(["hooks", "install", "--dir", str(repo)]) == 1  # someone else's hook: left alone


# --- commands -------------------------------------------------------------------------------------

def test_round_prints_questions_and_tells_agents_to_stop(repo, hunt_api, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": [session(roundsAnswered=0, finalSha256=None, status="open")]})
    rnd = {"n": 1, "focus": "The claim and the controls", "answers": None,
           "feedback": {"summary": "Clear claim.", "blocking": [{"stage": "controls", "issue": "No baseline."}],
                        "questions": [{"id": "q1", "stage": "claim", "quote": "Pressure raises", "question": "What refutes it?", "why": "w"}]}}
    hunt_api.routes[("POST", f"/api/v1/design/sessions/{SID}/rounds")] = (200, {"session": session(), "round": rnd})
    assert cli.main(["design", "round", "requests/L40-N1-001/DESIGN.md"]) == 3
    out = capsys.readouterr().out
    assert "What refutes it?" in out and "No baseline." in out and "STOP" in out
    sent = hunt_api.calls[-1]["body"]
    assert sent["draft"] == DESIGN and any("Unit and metric" in f for f in sent["formatCheck"])


def test_answer_refuses_without_a_terminal(repo, hunt_api, capsys, monkeypatch):
    monkeypatch.chdir(repo)
    assert cli.main(["design", "answer", "--session", SID]) == 1
    assert "separate terminal" in capsys.readouterr().err


# --- transcripts ----------------------------------------------------------------------------------

def test_redact_keeps_the_conversation_and_drops_the_rest():
    lines = [
        json.dumps({"type": "user", "message": {"role": "user", "content": "design it; key sk-ant-api03-abcdefghijklmnopqrstuv"}}),
        json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Starting design_start for " + SID},
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/x", "pad": "y" * 5000}}]}}),
        json.dumps({"type": "user", "message": {"content": [{"type": "tool_result", "content": "z" * 3000}]},
                    "toolUseResult": {"file": {"content": "SECRET FILE"}}}),
        "not json",
    ]
    text, counts = traces.redact(lines)
    assert "sk-ant" not in text and "SECRET FILE" not in text
    assert counts["secrets"] == 1 and counts["droppedFields"] == 1 and counts["toolResultsTrimmed"] == 1
    assert counts["toolInputsTrimmed"] == 1 and counts["unreadableLines"] == 1
    assert "user: design it" in traces.preview(text)


def test_find_log_picks_the_session_that_mentions_the_review(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    work = tmp_path / "work"
    work.mkdir()
    logs = traces.claude_code_dir(work)
    logs.mkdir(parents=True)
    (logs / "a.jsonl").write_text('{"x": "nothing"}\n')
    (logs / "b.jsonl").write_text(json.dumps({"x": SID}) + "\n")
    assert traces.find_log(SID, work) == (logs / "b.jsonl", "claude-code")
