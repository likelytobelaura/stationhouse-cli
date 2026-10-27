# stationhouse-cli

Station House from the terminal: see who is working on what, find unclaimed work, and claim it.
Many people can be on one task. You sign in with your Station House account (the email and password
you use on the website); there is no separate token to manage.

Two ways in:

- **You ask.** Type a question and the Station House assistant answers: `stationhouse` opens a chat,
  or just `stationhouse what could I work on?`.
- **Design an experiment.** `stationhouse design` gives you a template, checks it and critiques it
  before you run anything. See [Designing an experiment](#designing-an-experiment).
- **Your coding agent works it.** The commands (`find-work`, `context`, `prs`, `claim`, … with
  `--json`) return plain facts, and your own agent (Claude Code, Codex, Cursor) reasons over them.
  See [Using it from a coding agent](#using-it-from-a-coding-agent).

## Credit

The idea, a small CLI plus agent skills that give an agent a way into a project's own world, comes
from [**mumwelt**](https://github.com/Open-Athena/mumwelt) (`mum`), the CLI that [**Open Athena**](https://github.com/Open-Athena)
built for the [Marin](https://github.com/marin-community/marin) project. `stationhouse-cli` follows its shape:
a `pip`-installable command, a hosted service behind it, and a skill that teaches an agent when to
use it. Thank you to the Open Athena team for it.

## Install

```bash
uv tool install git+https://github.com/likelytobelaura/stationhouse-cli   # or: pip install .
stationhouse login            # your Station House email + password
stationhouse projects         # the projects you can see
stationhouse project use <id> # the one you work in (skip it if there's only one)
stationhouse unclaimed        # open tasks nobody is on
stationhouse who <task-id>    # who is on a task, active or stale
stationhouse context <task-id>  # the task's neighbourhood: people, its PRs/discussions and their latest comments
stationhouse claim <task-id>  # put yourself on it
stationhouse release <task-id>
stationhouse inbox            # PRs and discussions waiting on you: feedback, replies, mentions
stationhouse inbox done 348   # mark one handled (it comes back if someone comments again)
stationhouse mine
stationhouse prs              # open PRs waiting for a review (not yours), unreviewed first
```

`sth` is a short alias. Add `--json` to any command for machine-readable output.

The first time you run it in a terminal, a train pulls through to welcome you (once; `stationhouse
train` brings it back). It never runs for scripts, coding agents or `--json`, and
`STATIONHOUSE_NO_TRAIN=1` turns it off.

Tasks live inside projects. Project-wide commands (`unclaimed`, `prs`, `find-work`, `explore`, `task
request`) work in `--project <id>`, else `STATIONHOUSE_PROJECT`, else the project you picked with
`project use`, else the only one you can see. Commands that take a task id find its project
themselves, so `stationhouse claim <task-id>` works from anywhere.

## Chat

Run `stationhouse` on its own and talk to it, or put the question straight on the command line
(`stationhouse who is working on the scorer?`, or `stationhouse ask "…"`) to ask once:

```
$ stationhouse
Station House. Ask about the projects and tasks, or what to work on next. /help lists commands, /exit leaves.
you> I like adversarial prompts, what could I pick up?
Try `stress-test-solver`: nobody is on it yet, and it's about breaking the solver with hostile inputs.
Claim stress-test-solver, "Stress test the solver" (matches what you're into)? [y/N] y
You're on stress-test-solver.
It's on your dashboard now.
you> /context stress-test-solver
```

Plain lines go to the Station House assistant, which reads the projects, tasks, who is on them and
what's happening in their PRs (it runs on the platform, so there's nothing to set up). It never
changes anything itself: when it suggests claiming or releasing a task you're asked, and on a yes the
CLI does it as you. Lines starting with `/` run any command above (`/who`, `/claim`, `/login`, …).
If the assistant is switched off or has used its monthly budget, you get the offline `explore`
answer instead.

In zsh (the macOS default) a trailing `?` is a filename wildcard, so `stationhouse what's open?` fails
with "no matches found" before the CLI runs. Leave the `?` off, quote the question
(`stationhouse ask "what's open?"`), or add `alias stationhouse='noglob stationhouse' sth='noglob sth'`
to your `~/.zshrc`.

A command typed exactly runs as that command (`stationhouse who add-question`); anything that isn't
a command is a question. A near-miss like `stationhouse claimm x` is an error ("did you mean
`claim`?"), never a question.

## Designing an experiment

Before you run an experiment or analysis, write down how it's set up and check it:

```bash
stationhouse design new experiments/my-idea   # writes DESIGN.md with six stages to fill in
stationhouse design check experiments/my-idea # every stage filled in, a baseline named, ... (exit 1 on errors)
stationhouse design review experiments/my-idea  # the Station House assistant critiques it (signed in)
stationhouse design guide                     # the method, stage by stage
```

The six stages are **Claim** (one falsifiable sentence), **Unit and metric**, **Controls** (at least
one trivial baseline, and one control that differs in exactly one thing), **Data** (version, filters
with counts, a split by the unit that can leak), **Noise** (seeds, reruns, bootstrap) and
**Readout** (the pass rule and the result that would mean "no", written before the run). `new`
takes a folder (it writes `DESIGN.md`) or a file name, so on ConspiracyBench it can write the
request's `EXPERIMENT.md` directly. Projects keep their own controls and traps in their
`CONTRIBUTING.md`; the headings are the same everywhere, so `check` works on any of them, including a
PR description with a `## Design` section.

`check` is plain text rules with no model: it catches a missing piece, not a bad idea. For the
thinking, use `review`, or your coding agent with the `experiment-design` skill (below): it walks you
through the stages one at a time, asks rather than fills in, and red-teams the design before you run
it. `review` sends the design as one assistant question (at most 4,000 characters; a longer file is
cut to the six stages).

## Using it from a coding agent

```bash
stationhouse skills-install   # Claude Code: copies both skills to ~/.claude/skills/ (stationhouse, experiment-design)
stationhouse agents-md        # Codex, Cursor and others: writes the same rules into ./AGENTS.md
```

`agents-md` adds a marked section per skill to the project's `AGENTS.md` and leaves the rest of the
file alone; run it again after upgrading to refresh them (`--print` shows them without writing).
Then ask your agent "help me set up this experiment" or "review my design".

Then ask your agent "what should I work on?" It runs `find-work --json`, reads the summaries and
reasons, and suggests a task. It claims only after you agree. The skill keeps your agent on the
fact-returning commands rather than the platform's assistant: your agent is already the one doing
the thinking, and `explore` is a plain lookup with no model in it. **You sign in yourself**: the
skill tells the agent to ask you to run `stationhouse login` and never to handle your password.

## How sign-in works

- `login` does a Cognito SRP sign-in to the Station House user pool, so the password is proven, not
  sent. Only your email and password work: the CLI has no other way in.
- The session is stored in `~/.config/stationhouse/credentials.json` (mode 0600): your email and
  tokens, never your password. The access token refreshes itself; `stationhouse logout` deletes the
  file.
- Every API call carries your Cognito tokens. The platform works out who you are from them, and
  your Hugging Face handle from the one you verified on the website, so nobody can claim as someone
  else.

## Status

Live against `https://app.station-house.net`. The routes it calls are listed in `docs/API.md`; the
tests run the CLI against a fake server.

## Development

```bash
uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest
```

Set `STATIONHOUSE_API_URL` to point at another server (default `https://app.station-house.net`).
