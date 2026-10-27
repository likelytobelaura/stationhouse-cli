# <Experiment title>

<!-- Fill in each section before you run anything. Comments like this one are prompts: replace them.
`stationhouse design check <this file>` checks it. -->

## Claim

<!-- One falsifiable sentence: "If X, then metric M on population P moves by at least Δ compared with Y."
What would you believe after this that you don't believe now? -->

## Unit and metric

<!-- What is one data point (item, claim, token, neuron, document)? How many? Which metric, aggregated
how and over what? What does it do with all zeros, ties, a constant prediction, or pooled groups? -->

## Controls

<!-- At least one trivial baseline (constant, majority class, copy the input, carry forward, base model,
random units from the same population), and one control that differs from the treatment in exactly
one thing. If the method did nothing useful, what would it still score? -->

## Data

<!-- Source and version (commit/hash/date). Each filter with the row count after it. Duplicates.
The split, by the unit that can leak (claim, document, author), fixed before you look at results. -->

## Noise

<!-- How much the metric moves when nothing changes: seeds, reruns, bootstrap over units. The smallest
effect you'd treat as real. A pilot run (doesn't count) to check the plumbing. -->

## Readout

<!-- Written before the run: the numbers you'll report, the pass rule, and the result that would
refute the claim. -->

## How to run

<!-- Exact commands, models and versions, hardware, and what gets saved. -->

## Deviations

<!-- After the run: anything you changed from the plan above, and why. Leave the sections above as
they were. -->
