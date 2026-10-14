---
name: stationhouse
description: See the Station House projects the contributor can see and who is working on what in them, find work, find PRs to review, see what's happening on a task, claim or release a task, propose a subtask, or request a new task for the signed-in contributor, using the `stationhouse` CLI. Use when asked "what should I work on", "who is on X", "what's happening on X", "find me a PR to review", "claim X", "add a subtask to X", "can we have a task for Y", or "am I on anything".
---

# stationhouse: who is doing what

The `stationhouse` CLI (alias `sth`) talks to the Station House platform as the signed-in
contributor. Many people can be on one task; claiming never locks it.

## Signing in

Run `stationhouse whoami` first. If it says you aren't signed in, **stop and ask the human to
run `stationhouse login` themselves** (it asks for their Station House email and password).
Never ask for, type, or store their password, and never try to sign in on their behalf.

## Commands

Add `--json` for output you can parse.

```
stationhouse projects                       the projects they can see (and which one they picked)
stationhouse project [use <id> | clear]     show / pick / forget the project they work in
stationhouse find-work [about words]        pick an open task for the contributor, with the reasons
stationhouse explore "question"             ask about the tasks and who is on them (no question: explore mode)
stationhouse task claim <task-id>           put the contributor on a task or subtask; it shows on their dashboard
stationhouse task release <task-id>         take them off it (and off their dashboard)
stationhouse task create <parent-id> <title...> -s "summary"      propose a SUBTASK of an existing task
stationhouse task request <title...> -d "description"             ask for a new TOP-LEVEL task
stationhouse unclaimed [--project P]        open tasks nobody is on, with a summary
stationhouse who <task-id>                  people on a task, and whether each is active or stale
stationhouse context <task-id>              what's happening on a task: who, which PRs/discussions, their latest comments
stationhouse mine                           the contributor's own claims
stationhouse prs [--project P]              open PRs waiting for a review (not the contributor's own), unreviewed first
stationhouse chat "question"                one question to the platform's Station House assistant (optional; see below)
stationhouse assign <task-id> <hf-handle>   admins only
stationhouse task proposals|approve|reject  admins only: review proposed subtasks
```

`claim` and `release` also work without `task`.

## Projects

Station House has several projects, and tasks live inside them. Nothing is assumed: run
`stationhouse projects --json` to see the ones the contributor can see (`current` is the one they
picked, if any). Project-wide commands (`find-work`, `explore`, `unclaimed`, `prs`, `task request`)
work in `--project <id>` if given, else the picked one, else the only one; with several and none
picked they stop and ask, so pass `--project` when the contributor named one. Commands that take a
task id (`who`, `context`, `claim`, `release`, `task create`) find the task's project themselves.
Don't run `project use` on the contributor's behalf unless they ask; it changes their default.

`stationhouse` with no command opens an interactive chat for people; don't run it bare (it waits for
typing). Use the commands above, which give you the facts directly.

## Picking work

1. `stationhouse find-work --json` (add what the contributor is into, e.g. `find-work adversarial prompts`).
   Say which task you'd pick and why, using the `reasons`.
2. `stationhouse who <task-id>` before claiming; if others are on it, mention them so the
   contributor can coordinate.
3. **Claim only after the contributor agrees.** A claim is recorded under their name.

## Reviewing PRs

When the contributor wants something to review, run `stationhouse prs --json` (with `--project` if
they named one). Each PR has its number, title, link (`url`), author, the task it's for (`taskId`) and
who besides the author has commented (`reviewers`; empty means nobody has looked yet). Offer one or
two, with the link. A PR is not a task: don't offer a task when they asked for a PR, or the reverse.
Before they review, `stationhouse context <taskId> --json` shows what else is going on around it.

## Answering questions about tasks

- What's happening on a task, who is involved, recent PRs and comments: `stationhouse context <task-id> --json`.
- Who is on what, or which task covers something: `stationhouse explore --json "<question>"`. Who is on
  what comes from the platform, so report it as given; don't guess. `explore` may add a local model's
  words about what a task is (`answer`); that text is only about the task, never about people.
- You can reason over that JSON yourself, which is usually best. If you want the platform's own
  assistant instead, `stationhouse chat "<question>"` asks it once and prints its reply. It never
  changes anything; a line starting `Suggested: claim <task-id>` is only a suggestion. Put it to the
  contributor, and claim only if they agree.

## Subtask or new task?

- The work is a **piece of an existing task** (a slice, an extra check, a follow-up inside its scope):
  `task create <parent-id> <title> -s "<what it is and when it's done>"`. It is a *proposal*: an admin
  approves it, and then it's created and the contributor is put on it. Subtasks only go one level
  deep, so if the parent is itself a subtask, use its parent.
- The work is **something new that isn't under any existing task**: `task request`. The maintainers decide.
- Check `explore "is there already a task for X"` first, and tell the contributor if there is.
- Both file something under the contributor's name for a human to read. Confirm the wording with them first.

## Errors

- `error: You aren't signed in` or `session has expired`: ask the human to run `stationhouse login`.
- `This Station House server doesn't support that yet`: the platform hasn't shipped that command;
  tell the contributor and don't retry.
- Only an admin can `assign`, `task proposals`, `task approve` or `task reject`. If it is refused, say
  so; don't look for a workaround.
- `Warning: ... couldn't put its card on your dashboard`: the claim went through but the website card
  didn't; tell the contributor to add it from the website.
- `only one level of subtasks is supported`: propose it under the parent's parent instead.
