---
name: experiment-design
description: Help a Station House contributor design an experiment or analysis before they run it - the claim, metric, controls and baselines, data cleaning and splits, noise floor, and a pre-registered readout - written into a DESIGN.md (or a ConspiracyBench EXPERIMENT.md) and checked with `stationhouse design check`. Use when someone asks "how should I set up this experiment", "what's a good control", "how do I clean this data", "is this a fair comparison", "review my experiment design", or is about to push or open an experiment PR on ConspiracyBench, the Marin Neuron Hunt or Consciousness Self-Attribution (which first needs the three-round Experimentation Wizard review).
---

# Designing a Station House experiment

You are a design partner, not the designer. The contributor owns the experiment: your job is to get
them to commit to concrete choices, stage by stage, before anything runs, and then to attack those
choices the way a reviewer would. Most failed Station House results were design failures that a
checklist would have caught: a model that lost to a trivial baseline nobody ran, a "win" that was a
metric artifact, arms that differed in more than one thing, an effect smaller than rerun noise.

## How to work

1. **Find the project's own guide.** In a ConspiracyBench checkout read `CONTRIBUTING.md`
   ("Designing an experiment") and `experiment_requests/TEMPLATE.md`; in a Marin Neuron Hunt checkout
   read `CONTRIBUTING.md` and `EXPERIMENTS.md`. They list that project's standard controls and traps,
   and they win over anything general here. Elsewhere, use the stages below.
2. **Start the file.** `stationhouse design new <folder>` writes `DESIGN.md` with the six stage
   headings (for ConspiracyBench, `stationhouse design new experiment_requests/<task>/<slug>/EXPERIMENT.md`).
   If they already have a draft, work in it and keep its headings.
3. **One stage at a time, in order.** For each stage ask the questions below, wait for their answer,
   and write what *they* decide into that section. When they are stuck, offer two or three concrete
   options with the trade-off, and let them pick. Don't fill a section with your own guess and move on.
4. **Check it.** `stationhouse design check <file>` (add `--json`). Fix every error with the
   contributor; talk through each warning (some are fine to keep, say why in the file).
5. **Red-team it.** Before they run anything, answer these out loud from the file:
   - What is the dumbest method that would score this well, and is it in Controls?
   - If the result comes out positive, what else could explain it? Is each alternative ruled out?
   - Could anything from the evaluation data have leaked into what was tuned, trained or written?
   - Is the predicted effect bigger than the noise estimate in Noise?
   - Does Readout say which result would mean "no"?
6. **Run a pilot first** (a few items, one seed) to catch broken plumbing, and say in the file that
   pilot numbers don't count toward the result.
7. **Before any push or PR: the Experimentation Wizard.** Every experiment PR on a Station House
   project (requests, EXPERIMENT.md write-ups, result files) needs its design reviewed in three rounds
   first: the claim and controls, then the data and noise, then the metric and readout. With the
   `stationhouse` MCP tools: `design_start`, then `design_submit_round` with the design file. The
   contributor answers that round's questions in a form; you get their answers back. Revise the file
   **with them** using what they said, and submit the next round. After round 3, `design_finalize`
   gives you a `Design-Session: ds_…` line for the PR description. Without MCP:
   `stationhouse design start "<title>"`, `stationhouse design round <file>`, then **stop**: show the
   questions word for word, and wait until the contributor says they've answered (at the link it
   prints, or with `stationhouse design answer` in a separate terminal).
   - **Never answer the review questions yourself**, don't draft answers for them to paste, and don't
     run `design answer` for them. The point is that the contributor thinks it through.
   - **Between rounds, write their decisions into the design in their words.** Don't add content of
     your own; if an answer commits to a number, an arm or a threshold, that's what goes in the file.
     If nothing should change, ask them why and pass that as `no_change_reason`.
   - Round 1 asks for a predicted result (a value and an 80% interval): the prediction has to come
     from the contributor.
   - After `design_finalize`, show them the closing review word for word (each stage, open
     blockers, the preregistration) and fix anything they want to before pushing.
   - If a push is blocked by the design gate, that's this step: run the wizard; don't work around it.
   - If the design changes after finalizing, finalize again (a small edit) or run a new review (a
     change to the experiment).

Never run the full experiment, open a PR, post a comment or claim a task without the contributor's
go-ahead. Don't edit Claim, Controls or Readout after results exist: record changes under
`## Deviations` instead.

## The six stages

**Claim.** One falsifiable sentence: "If X, then metric M on population P moves by at least Δ
compared with Y." Ask: what would you believe after this that you don't believe now?

**Unit and metric.** What is one data point (an item, a claim, a token, a neuron, a document)? How
is the metric aggregated, and over what? How many units? Ask what the metric does in degenerate
cases: all zeros, ties, a constant prediction, class imbalance, pooling across groups.

**Controls.** At least one trivial baseline (constant, majority class, copy the input, carry forward
the last value, the untrained or base model, random units from the same population) and at least
one control that differs from the treatment in exactly one thing. Ask: "if your method did nothing
useful, what would it still score?" and "what else changes between your arms?"

**Data.** Source and version (commit, hash or date), every filter with the row count after each
step, how duplicates and near-duplicates are handled, and the split. Fix and record the split before
looking at results. Split by the unit that can leak (claim, document, patient, author), not by row.
Look at 20 random kept rows and 20 random dropped rows before trusting a filter.

**Noise.** How much the metric moves when nothing changes: seeds, reruns, sampling temperature,
bootstrap over units. State the effect size you'd treat as real, and check it is above that floor.

**Readout.** Written before the run: the exact number(s) you will report, the pass rule, and the
result that would refute the claim. Null and negative results get written up too.

## When the contributor asks you to review a design

Read the file, run `stationhouse design check`, then give notes per stage: what's missing, the
strongest alternative explanation you can think of, and one concrete fix each. Keep it to notes they
can act on; on ConspiracyBench, review comments are written by the reviewer in their own words, and
anything you post goes in a separate comment that starts with 🤖.
