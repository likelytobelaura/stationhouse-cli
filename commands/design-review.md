---
description: Run the Station House Experimentation Wizard on this experiment's design (three rounds the contributor answers)
argument-hint: "[design file]"
---

Run the Station House Experimentation Wizard on the design in $ARGUMENTS (if empty: this experiment's
DESIGN.md or EXPERIMENT.md; ask if there are several).

1. Run `stationhouse design check` on the file and fix errors with the contributor first.
2. Call the `design_start` tool with a short title (skip if `design_status` shows a review for this branch).
3. Call `design_submit_round` with the file. The contributor answers in the form. Never answer for them.
4. Revise the file with the contributor using their answers, then submit the next round. Three rounds in all.
5. Call `design_finalize` and put the `Design-Session:` line it returns in the PR description.
