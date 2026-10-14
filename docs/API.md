# API the CLI expects

The CLI is a thin client. These routes live in the private `station-house-platform` repo
(`src/app/api/v1/`, documented in its `docs/contributor-api.md`) and are deployed at
`https://app.station-house.net`. Against an older server the CLI answers "doesn't support that yet"
on a 404.

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

Projects come first and tasks live under their project. A task asked for under the wrong project is a
404 whose body names its project (`{ error, projectId, taskId }`); the CLI follows it unless you passed
`--project` yourself.

| Method + path | Body / query | Response |
|---|---|---|
| `GET /me` | | `{ memberId, hfUsername \| null, isAdmin }` |
| `GET /me/claims` | | `{ claims: [{ taskId, projectId, title, status, stale }] }` |
| `GET /projects` | | `{ projects: [{ id, title, summary }] }` |
| `GET /projects/{p}` | | `{ project, overview }` |
| `GET /projects/{p}/tasks` | | `{ project, you, tasks: [{ taskId, title, parentTaskId, childTaskIds, summary, scope, completionCriteria, available, closed, workable, full, people, mine, pendingSubtasks }] }` (the whole tree; `explore` and `find-work` read this) |
| `GET /projects/{p}/unclaimed` | | `{ tasks: [{ taskId, title, summary }] }` |
| `GET /projects/{p}/prs` | `?limit=` | `{ project, prs: [{ repo, num, title, url, author, taskId, reviewers, ... }] }` |
| `POST /projects/{p}/requests` | `{ title, description, atomicUnit? }` | `{ requestId, created, notified, status }` (`task request`) |
| `GET /projects/{p}/tasks/{t}` | | the task in full |
| `GET /projects/{p}/tasks/{t}/people` | | `{ projectId, taskId, title, people: [{ hfUsername, prNums?, stale, you? }] }` |
| `GET /projects/{p}/tasks/{t}/context` | | the task's work-graph neighbourhood (`context`) |
| `POST /projects/{p}/tasks/{t}/claim` | (no body) | `{ projectId, taskId, people, dashboard }` |
| `POST /projects/{p}/tasks/{t}/release` | (no body) | `{ ok: true, dashboard }` |
| `POST /projects/{p}/tasks/{t}/assign` | `{ hfUsername }` (admin only) | `{ ok: true }` |
| `POST /projects/{p}/tasks/{t}/subtasks` | `{ title, summary }` | `{ proposalId, created, status }` (`task create`) |
| `GET /proposals`, `POST /proposals/approve`, `POST /proposals/reject` | (admin only) | the subtask-proposal queue |
| `POST /agent/chat` | `{ messages: [{ role, text }], project? }` | `{ reply, suggestedActions, taskIds }` (the assistant, for people) |

Errors are `{ "error": "message" }` with a normal status. `claim` for a member with no verified HF
handle is a 409 with a message telling them to verify it on the website. `dashboard` is `added` /
`removed` / `unchanged` / `failed` for the task's card on the website dashboard.

## Who thinks where

- **People asking questions** (`stationhouse`, `stationhouse <question>`, `stationhouse ask`) get the
  platform's assistant (`POST /agent/chat`), which reads the same data and never changes anything:
  claims it suggests are put to you as y/N and made by the CLI.
- **Coding agents and scripts** use the other commands. They return facts and do no thinking of
  their own: `find-work` is a fixed ranking (nobody on it beats only stale claims beats shared; a
  subtask beats a parent with open subtasks; words you give it narrow the list), so its reasons are
  the real reasons, and `explore` is a keyword lookup over the task tree. No model runs on your
  machine.
