# stationhouse-cli

Station House from the terminal: see who is working on what, find unclaimed work, and claim it.
Many people can be on one task. You sign in with your Station House account (the email and password
you use on the website); there is no separate token to manage.

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
stationhouse unclaimed        # open tasks nobody is on
stationhouse who <task-id>    # who is on a task, active or stale
stationhouse claim <task-id>  # put yourself on it
stationhouse release <task-id>
stationhouse mine
```

`sth` is a short alias. Add `--json` to any command for machine-readable output.

## Using it from a coding agent

```bash
stationhouse skills-install   # copies the skill to ~/.claude/skills/stationhouse
```

Then ask your agent "what should I work on?" It runs `unclaimed`, reads the summaries, and suggests
a task. It claims only after you agree. **You sign in yourself**: the skill tells the agent to ask
you to run `stationhouse login` and never to handle your password.

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

The CLI is complete and tested against a fake server; real Cognito sign-in is exercised up to the
credential check. The server routes it calls (`docs/API.md`) are built in `station-house-platform`
(branch `feat/contributor-api`) but **not deployed yet**, so until they ship every command other than
`login` / `logout` answers "doesn't support that yet".

## Development

```bash
uv venv && uv pip install -e '.[dev]' && .venv/bin/python -m pytest
```

Set `STATIONHOUSE_API_URL` to point at another server (default `https://app.station-house.net`).
