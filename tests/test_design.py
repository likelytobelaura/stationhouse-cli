import json

from stationhouse_cli import cli, design

GOOD = """# Does the LoRA cut divergence?

## Claim

If we fine-tune Qwen3-4B on data/final_v4, its divergence rate on ConspiracyBench drops by at least 5 points
compared with the base model.

## Unit and metric

One corpus item (315), k=5 samples each. Divergence rate = diverge verdicts / all verdicts, by pplx-decider.

## Controls

The base model (no fine-tune) and a LoRA trained on the same number of shuffled generic chat rows, so only the
content differs. The always-hold policy scores 0% divergence; say what it scores on accuracy too.

## Data

Corpus at commit 58de878c2. The SFT set is filtered (>=40-char replies, no slurs): 43.6k -> 24.4k rows.
No SFT row mentions a corpus claim_id; held-out = the whole corpus, nothing from it is trained on.

## Noise

Three seeds for the LoRA, and a bootstrap over claims for a 95% interval.

## Readout

Report the drop with its interval. A drop under 5 points, or an interval that covers 0, refutes the claim.

## How to run

modal run eval.py
"""


def run(capsys, *argv):
    code = cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def test_template_has_every_stage_and_fails_the_check_until_filled():
    t = design.template()
    for _, name, _ in design.STAGES:
        assert f"## {name}" in t
    errors = [f for f in design.check(t) if f["level"] == "error"]
    assert {f["stage"] for f in errors} == {k for k, _, _ in design.STAGES}  # all empty: only the prompts in comments


def test_a_complete_design_passes():
    assert design.check(GOOD) == []


def test_missing_section_and_placeholders_are_errors():
    text = GOOD.replace("## Noise", "## Something else").replace("Report the drop", "TODO report the drop")
    msgs = {(f["stage"], f["level"]) for f in design.check(text)}
    assert ("noise", "error") in msgs and ("readout", "error") in msgs


def test_controls_without_a_baseline_is_an_error():
    text = GOOD.replace(design.sections(GOOD)["controls"], "We compare against the other arm.")
    f = [f for f in design.check(text) if f["stage"] == "controls"]
    assert f and f[0]["level"] == "error" and "baseline" in f[0]["message"]


def test_warnings_for_what_a_reviewer_asks_first():
    text = (GOOD.replace(design.sections(GOOD)["data"], "Our questions.")
                .replace(design.sections(GOOD)["noise"], "It should be stable.")
                .replace(design.sections(GOOD)["readout"], "We report the rate."))
    warned = {(f["stage"], f["message"].split(" ")[2]) for f in design.check(text) if f["level"] == "warning"}
    stages = {s for s, _ in warned}
    assert stages == {"data", "noise", "readout"}
    assert len([w for w in warned if w[0] == "data"]) == 3 and len([w for w in warned if w[0] == "readout"]) == 2


def test_headings_can_be_numbered_nested_or_aliased_like_a_pr_description():
    body = GOOD.replace("## ", "### 1. ").replace("# Does", "## Design\n\n# Does")
    body = body.replace("### 1. Controls", "### Baselines and controls").replace("### 1. Readout", "### Pass rule")
    assert design.check("## Neuron(s)\n\nL40-N1\n\n" + body + "\n## Checklist\n\n- [x] done\n") == []


def test_a_subheading_stays_in_its_section_and_a_commented_heading_is_ignored():
    text = GOOD.replace("## Noise\n", "## Noise\n\n<!--\n## Readout\n-->\n### Seeds\n")
    secs = design.sections(text)
    assert "### Seeds" in secs["noise"] and "Report the drop" in secs["readout"]


def test_review_question_fits_and_keeps_the_stages():
    q, cut = design.review_question(GOOD, 4000)
    assert not cut and len(q) <= 4000 and "always-hold" in q
    long = GOOD.replace("modal run eval.py", "x " * 5000)
    q, cut = design.review_question(long, 4000)
    assert cut and len(q) <= 4000 and "## Readout" in q and "x x x x" not in q


# ---------------------------------------------------------------- the commands

def test_new_then_check(tmp_path, capsys):
    code, out, _ = run(capsys, "design", "new", str(tmp_path / "exp"))
    path = tmp_path / "exp" / "DESIGN.md"
    assert code == 0 and path.exists() and "design check" in out
    code, out, _ = run(capsys, "design", "check", str(tmp_path / "exp"))
    assert code == 1 and "error: Claim" in out and "6 error(s)" in out
    # Never overwrites without --force.
    path.write_text(GOOD)
    code, _, err = run(capsys, "design", "new", str(tmp_path / "exp"))
    assert code == 1 and "already exists" in err and path.read_text() == GOOD
    code, out, _ = run(capsys, "design", "check", str(path))
    assert code == 0 and "nothing obvious is missing" in out


def test_new_writes_a_named_file_and_check_json(tmp_path, capsys):
    target = tmp_path / "experiment_requests" / "x" / "EXPERIMENT.md"
    assert run(capsys, "design", "new", str(target))[0] == 0 and target.exists()
    code, out, _ = run(capsys, "design", "check", str(target), "--json")
    data = json.loads(out)
    assert code == 1 and data["ready"] is False and data["findings"][0]["section"] == "Claim"


def test_check_a_missing_file(tmp_path, capsys):
    code, _, err = run(capsys, "design", "check", str(tmp_path / "nope.md"))
    assert code == 1 and "design new" in err


def test_guide_needs_no_sign_in(capsys):
    code, out, _ = run(capsys, "design", "guide")
    assert code == 0 and "## The six stages" in out and "name: experiment-design" not in out


def test_review_sends_the_design_to_the_assistant(tmp_path, signed_in, api, capsys):
    (tmp_path / "DESIGN.md").write_text(GOOD)
    api.routes[("POST", "/api/v1/agent/chat")] = (200, {"reply": "Controls: add a random-LoRA arm."})
    code, out, _ = run(capsys, "design", "review", str(tmp_path))
    assert code == 0 and "random-LoRA" in out
    body = api.calls[0]["body"]
    assert body["project"] == "conspiracybench" and "always-hold" in body["messages"][0]["text"]
    assert len(body["messages"][0]["text"]) <= 4000


def test_review_when_the_assistant_is_off_still_prints_the_checks(tmp_path, signed_in, api, capsys):
    (tmp_path / "DESIGN.md").write_text(GOOD.replace("## Noise", "## Elsewhere"))
    api.routes[("POST", "/api/v1/agent/chat")] = (503, {"error": "The assistant is off"})
    code, out, _ = run(capsys, "design", "review", str(tmp_path), "--json")
    data = json.loads(out)
    assert code == 0 and data["review"] is None and data["findings"][0]["stage"] == "noise"


def test_review_needs_sign_in(tmp_path, capsys):
    (tmp_path / "DESIGN.md").write_text(GOOD)
    code, _, err = run(capsys, "design", "review", str(tmp_path))
    assert code == 1 and "stationhouse login" in err


# ---------------------------------------------------------------- shipped to coding agents

def test_skills_install_ships_both_skills_and_the_template(tmp_path, capsys):
    run(capsys, "skills-install", "--dir", str(tmp_path))
    assert (tmp_path / "experiment-design" / "SKILL.md").read_text().startswith("---\nname: experiment-design")
    assert (tmp_path / "experiment-design" / "TEMPLATE.md").exists()
    assert (tmp_path / "stationhouse" / "SKILL.md").exists()


def test_agents_md_carries_the_design_rules_and_refreshes_in_place(tmp_path, capsys):
    run(capsys, "agents-md", "--dir", str(tmp_path))
    text = (tmp_path / "AGENTS.md").read_text()
    assert text.count("stationhouse-design:begin") == 1 and "stationhouse design check" in text
    assert "name: experiment-design" not in text
    run(capsys, "agents-md", "--dir", str(tmp_path))
    assert (tmp_path / "AGENTS.md").read_text() == text
