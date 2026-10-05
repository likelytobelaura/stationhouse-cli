# API the CLI expects

The CLI is a thin client. These routes live in the `station-house-platform` repo (branches
`feat/contributor-api` and `feat/cli-task-surface`, see `docs/contributor-api.md` there, and
`~/specs/station-house-agent-work-tracking-spec.md`); until a build includes them, the CLI answers
"doesn't support that yet" on a 404.

## Auth

Sign-in is Cognito SRP against the Station House user pool (the same account as the website), so a
contributor uses their Station House email and password. Every call sends:

```
Authorization: Bearer <Cognito access token>
x-station-house-id-token: <Cognito ID token>
```

The platform verifies both against the pool (`withMember`, `verifiedMemberEmail`) and derives the
member's Hugging Face handle from their verified `HfBinding`. Identity is never read from a body.
Admin routes use `withAdmin` (Cognito `admins` group).

## Routes (all under `/api/v1`, JSON)

| Method + path | Body / query | Response |
|---|---|---|
| `GET /me` | | `{ memberId, hfUsername \| null, isAdmin }` |
| `GET /tasks/unclaimed` | `?project=` | `{ tasks: [{ taskId, title, summary }] }` |
| `GET /tasks/{taskId}/people` | | `{ taskId, title, people: [{ hfUsername, prNums?, stale, you? }] }` |
| `GET /me/claims` | | `{ claims: [{ taskId, status, stale }] }` |
| `GET /tasks` | `?project=` | `{ project, you, tasks: [{ taskId, title, parentTaskId, childTaskIds, summary, scope, completionCriteria, available, closed, people, mine, pendingSubtasks }] }` (the whole tree; `explore` and `find-work` read this) |
| `POST /claims` | `{ taskId }` | `{ people: [...], dashboard }` (everyone now on the task; the caller has `you: true`; `dashboard` is `added` / `unchanged` / `failed` for the task's card on their website dashboard) |
| `POST /claims/release` | `{ taskId }` | `{ ok: true, dashboard }` (`removed` / `unchanged` / `failed`) |
| `POST /tasks` | `{ parentTaskId, title, summary }` | `{ proposalId, created, status }` (a subtask proposal an admin approves; `task create`) |
| `POST /tasks/request` | `{ title, description, atomicUnit?, projectId? }` | `{ requestId, created, notified, status }` (a top-level task request; `task request`) |
| `GET /proposals` | (admin only) | `{ proposals: [{ proposalId, parentTaskId, title, summary, hfUsername, prNum, source }] }` |
| `POST /proposals/approve` | `{ proposalId }` (admin only) | `{ taskId, claimed, dashboard }` |
| `POST /proposals/reject` | `{ proposalId }` (admin only) | `{ ok: true }` |
| `POST /claims/assign` | `{ taskId, hfUsername }` (admin only, 403 otherwise) | `{ ok: true }` |

Errors are `{ "error": "message" }` with a normal status. `claim` for a member with no verified HF
handle should be a 409 with a message telling them to verify it on the website.

`dashboard` is optional: an older server omits it and the CLI says nothing about the card.

## What runs where

`explore` and `find-work` read `GET /tasks` and think on your machine. `find-work` is a fixed ranking
(nobody on it beats only stale claims beats shared; a subtask beats a parent with open subtasks; words
you give it narrow the list), so its reasons are the real reasons. `explore` answers who/what/unclaimed
questions straight from the tree; for open-ended ones it can ask a local Ollama
(`STATIONHOUSE_OLLAMA_URL`, default `http://localhost:11434`; `STATIONHOUSE_MODEL`, default
`qwen3.5:4b`; `--no-llm` turns it off) and drops the answer unless every task it names exists. Nothing
is sent to any hosted model.
