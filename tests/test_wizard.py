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
    assert "no git branch" in msg
    # No branch: only a session started from this folder counts, never the newest one on the platform.
    assert not any(c["path"].startswith("/api/v1/design/sessions") for c in hunt_api.calls)
    logged = [c for c in hunt_api.calls if c["path"] == "/api/v1/design/gate-events"][-1]["body"]
    assert logged["repo"] == "hf:Station-house/ConspiracyBench"


def test_hf_upload_finds_the_design_in_the_local_folder(tmp_path, hunt_api):
    out = tmp_path / "exp"
    (out / "results").mkdir(parents=True)
    (out / "EXPERIMENT.md").write_text(DESIGN)
    (out / "results" / "summary.json").write_text("{}")
    wizard.remember("hf:Station-house/ConspiracyBench", None, SID)
    hunt_api.routes[("GET", f"/api/v1/design/sessions/{SID}")] = (200, {"session": session(repo="hf:Station-house/ConspiracyBench"), "rounds": []})
    cmd = f"hf upload Station-house/ConspiracyBench {out} completed_experiments/exp --repo-type space"
    code, msg = gate.hook(json.dumps({"tool_input": {"command": cmd}, "cwd": str(tmp_path)}))
    assert code == 0 and SID in msg, msg


@pytest.mark.parametrize("command", [
    "git push", "/usr/bin/git push origin x", "git -c core.x=y push", "git --no-pager push", 'git -C "a b" push',
    "hf upload Station-house/ConspiracyBench . --repo-type space", "hf upload-large-folder Station-house/ConspiracyBench .",
    "gh pr create --fill", "gh api repos/o/r/pulls -f title=x", "python -c 'api.upload_folder(folder_path=\".\")'",
    "cd sub && git push origin feat",
])
def test_push_forms_are_recognised(command):
    assert any(any(p.search(seg) for p in gate.PUSHES) for seg, _ in gate._segments(command)), command


@pytest.mark.parametrize("command", ["git status", "git pushd", "echo hf uploaded", "gh pr view 3"])
def test_other_commands_are_not_pushes(command):
    assert not any(any(p.search(seg) for p in gate.PUSHES) for seg, _ in gate._segments(command)), command


def test_push_of_another_branch_checks_that_branch(repo, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": []})
    git(repo, "checkout", "-q", "main")
    code, msg = gate.hook(json.dumps({"tool_input": {"command": "git push origin req/L40"}, "cwd": str(repo)}))
    assert code == 2, "pushing req/L40 from main still checks req/L40's request"
    q = [c for c in hunt_api.calls if c["path"].startswith("/api/v1/design/sessions")][-1]
    assert "branch=req%2FL40" in q["path"]


def test_pre_push_checks_the_refs_git_sends(repo, hunt_api):
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": []})
    sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "req/L40"], capture_output=True, text=True).stdout.strip()
    git(repo, "checkout", "-q", "main")
    code, msg = gate.pre_push("https://github.com/Station-House/marin-neuron-hunt.git",
                              f"refs/heads/req/L40 {sha} refs/heads/req/L40 {gate.ZERO_SHA}\n", cwd=repo)
    assert code == 1 and "--no-verify" in msg
    assert gate.pre_push(None, f"(delete) {gate.ZERO_SHA} refs/heads/x {sha}\n", cwd=repo)[0] == 0


def test_agents_cannot_answer_the_review():
    for command in ["stationhouse design answer", "sth design answer --session x",
                    "curl -X POST https://app.station-house.net/api/v1/design/sessions/ds_x/answers -d @a.json"]:
        code, msg = gate.hook(json.dumps({"tool_input": {"command": command}}))
        assert code == 2 and "contributor" in msg, command


def test_an_experiment_push_without_a_design_document_is_blocked(repo, hunt_api):
    git(repo, "checkout", "-qb", "results-only", "main")
    (repo / "out").mkdir()
    (repo / "out" / "results.jsonl").write_text("{}")
    (repo / "out" / "summary.json").write_text("{}")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "results")
    hunt_api.routes[("GET", "/api/v1/design/sessions")] = (200, {"sessions": [session(branch="results-only")]})
    out = gate.check(repo)
    assert out["outcome"] == "blocked" and "no design document" in out["message"]


def test_undecided_classification_counts_as_an_experiment(signed_in, api):
    api.routes[("POST", "/api/v1/design/classify")] = (503, {"error": "down"})
    assert wizard.classify(["scripts/run.py"])["kind"] == "experiment"
    assert wizard.classify(["x/outputs/a.parquet", "x/outputs/b.jsonl"])["method"] == "rules"


def test_gate_events_are_queued_when_the_platform_is_down_and_sent_later(signed_in, api):
    api.routes[("POST", "/api/v1/design/gate-events")] = (503, {"error": "down"})
    gate._log("github:a/b", "x", "experiment", "unchecked")
    assert gate._queue_path().exists()
    api.routes[("POST", "/api/v1/design/gate-events")] = (201, {"ok": True})
    gate._log("github:a/b", "x", "other", "not-experiment")
    sent = [c["body"]["outcome"] for c in api.calls if c["path"] == "/api/v1/design/gate-events" and c["method"] == "POST"]
    assert sent[-2:] == ["unchecked", "not-experiment"]
    assert not gate._queue_path().exists()


def test_design_stretch_keeps_only_the_review():
    lines = ["unrelated work", "more unrelated", "call design_start", f"session {SID} started", "round 1", f"finalize {SID}", "later work"]
    assert traces.design_stretch(lines, SID) == lines[2:6]


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
    assert counts["secrets"] == 1 and counts["droppedFields"] == 1 and counts["toolResultsDropped"] == 1
    assert counts["toolInputsTrimmed"] == 1 and counts["unreadableLines"] == 1
    assert "z" * 50 not in text, "tool output is dropped, not trimmed"
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
