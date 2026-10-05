---
name: stationhouse
description: See who is working on what in Station House projects (ConspiracyBench), find work, ask questions about the tasks, claim or release a task, propose a subtask, or request a new task for the signed-in contributor, using the `stationhouse` CLI. Use when asked "what should I work on", "who is on X", "claim X", "add a subtask to X", "can we have a task for Y", or "am I on anything".
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
stationhouse find-work [about words]        pick an open task for the contributor, with the reasons
stationhouse explore "question"             ask about the tasks and who is on them (no question: explore mode)
stationhouse task claim <task-id>           put the contributor on a task or subtask; it shows on their dashboard
stationhouse task release <task-id>         take them off it (and off their dashboard)
stationhouse task create <parent-id> <title...> -s "summary"      propose a SUBTASK of an existing task
stationhouse task request <title...> -d "description"             ask for a new TOP-LEVEL task
stationhouse unclaimed [--project P]        open tasks nobody is on, with a summary
stationhouse who <task-id>                  people on a task, and whether each is active or stale
stationhouse mine                           the contributor's own claims
stationhouse assign <task-id> <hf-handle>   admins only
stationhouse task proposals|approve|reject  admins only: review proposed subtasks
```

`claim` and `release` also work without `task`.

## Picking work

1. `stationhouse find-work --json` (add what the contributor is into, e.g. `find-work adversarial prompts`).
   Say which task you'd pick and why, using the `reasons`.
2. `stationhouse who <task-id>` before claiming; if others are on it, mention them so the
   contributor can coordinate.
3. **Claim only after the contributor agrees.** A claim is recorded under their name.

## Answering questions about tasks

Use `stationhouse explore --json "<question>"`. Who is on what comes from the platform, so report it as
given; don't guess. `explore` may add a local model's words about what a task is (`answer`); that
text is only about the task, never about people.

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
