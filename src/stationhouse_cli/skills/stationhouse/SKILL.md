---
name: stationhouse
description: See who is working on what in Station House projects (ConspiracyBench), find unclaimed tasks, and claim or release a task for the signed-in contributor, using the `stationhouse` CLI. Use when asked "what should I work on", "who is on X", "claim X", or "am I on anything".
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
stationhouse unclaimed [--project P]   open tasks nobody is on, with a summary
stationhouse who <task-id>             people on a task, and whether each is active or stale
stationhouse mine                      the contributor's own claims
stationhouse claim <task-id>           put the contributor on a task
stationhouse release <task-id>         take them off it
stationhouse assign <task-id> <hf-handle>   admins only
```

## Picking work

1. `stationhouse unclaimed --json`, read the summaries, and pick the closest fit to what the
   contributor asked for. Say which one you picked and why.
2. `stationhouse who <task-id>` before claiming; if others are on it, mention them so the
   contributor can coordinate.
3. **Claim only after the contributor agrees.** A claim is recorded under their name.

## Errors

- `error: You aren't signed in` or `session has expired`: ask the human to run `stationhouse login`.
- `This Station House server doesn't support that yet`: the platform hasn't shipped that command;
  tell the contributor and don't retry.
- Only an admin can `assign`. If it is refused, say so; don't look for a workaround.
