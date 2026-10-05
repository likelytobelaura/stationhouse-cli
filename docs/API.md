# API the CLI expects

The CLI is a thin client. These routes live in the `station-house-platform` repo and **do not exist
there yet** (see `~/specs/station-house-agent-work-tracking-spec.md`); the CLI answers "doesn't
support that yet" on a 404.

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
| `POST /claims` | `{ taskId }` | `{ people: [...] }` (everyone now on the task; the caller has `you: true`) |
| `POST /claims/release` | `{ taskId }` | `{ ok: true }` |
| `POST /claims/assign` | `{ taskId, hfUsername }` (admin only, 403 otherwise) | `{ ok: true }` |

Errors are `{ "error": "message" }` with a normal status. `claim` for a member with no verified HF
handle should be a 409 with a message telling them to verify it on the website.
