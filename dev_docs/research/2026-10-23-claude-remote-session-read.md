---
created: 2026-10-23
---

# Can a background process read the Claude Code Remote session list?

**Measured 2026-10-23** on the Mac mini, over SSH with no desktop session, using
Claude Code 2.1.309 signed in with a claude.ai (Max) account. This record answers
the first "Verify before building" item in
[`../designs/2026-10-23-fleet-view.md`](../designs/2026-10-23-fleet-view.md) for
#1098, and it decides the shape of #1100. Session titles, ids and the org id are
left out because this repo is public. Counts, timings and field names are as
measured.

## Verdict: readable without status (partly verified)

A daemon has one route it can legitimately use: it starts `claude -p` itself and
sends the `list_cloud_sessions` control request on stdin. That request returns
each session's id, title, `updated_at`, a coarse `status`, `environment_kind`,
repository and branch.

It does **not** return `status_bucket`, `post_turn_summary.status_category` or
`needs_action`. It returns one page of at most 200 sessions, with a `truncated`
flag and no cursor, so it **cannot page back 30 days**.

The full-status data exists on the same service. Every route to it uses Claude
Code's own OAuth token against an undocumented endpoint. The issue rules that
out, since the token was not issued for a dashboard.

What was and wasn't verified:

- **Verified:** the control request exists, it is answered without a model turn,
  and it refuses any process started inside a Claude Code session.
- **Inferred from the shipped CLI code:** the fields it returns on success. I
  could not observe a successful read, because this probe ran inside a Claude
  Code session and the guard refused it. I did not spoof the guard. The owner
  can confirm with the command under "Owner check" below.

**For #1100:** take the "readable" branch, but in reduced form. Build a polling
adapter for the session **inventory**: id, title, `environment_kind`, branch,
`updated_at`, and the coarse `status`, including `requires_action`. This makes
cloud-only sessions visible. The **Needs you** categories (`need_input`,
`review_ready`, `completed`) and `needs_action` have to come from the
session-state hook, so cloud-only sessions get presence and `requires_action`,
not a post-turn category. The adapter's "incremental since last run" and "page
back 30 days" requirements can't be met on this route: each read is a full
newest-first snapshot of at most 200 sessions.

## What was tried

| # | Route                                                                                 | Result                                                                                                                                                                                                          | How known                                            |
| - | ------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 1 | Docs at code.claude.com                                                               | No public API or CLI command lists a user's cloud sessions. Cloud and other-machine sessions are listed only to a session connected to Remote Control, through `ListAgents`                                     | Read `llms.txt` and the cross-session messaging page |
| 2 | `claude --help`, `claude remote-control --help`                                       | No `claude remote …` list subcommand. `remote-control` only starts a session                                                                                                                                    | Ran                                                  |
| 3 | `claude agents --json` (and `--all`)                                                  | Works without a TTY in under 1 s. **Local sessions only**: 19 `interactive` + 1 `background`, fields `pid, cwd, kind, sessionId, name, status` (`idle`/`busy`), `state`. No cloud sessions, no post-turn fields | Ran                                                  |
| 4 | The `claude-code-remote` MCP in a headless `claude -p`                                | **Not attached.** The init event listed 17 MCP servers and 205 tools, and the only remote-related tool was `RemoteTrigger`                                                                                      | Ran                                                  |
| 5 | SDK control request `list_cloud_sessions` over `claude -p --input-format stream-json` | Answered in 2.1–3.7 s, with no model turn: `{"status":"unavailable","reason":"started_by_session"}`, and the same for `user_initiated: true`                                                                    | Ran                                                  |
| 6 | Hosted MCP `/v2/ccr-sessions/-/meta/mcp`, or REST `GET /v1/code/sessions`             | Carries the full record, `status_bucket` and `post_turn_summary` included, and pages. Auth is Claude Code's OAuth token. **Not called**                                                                         | CLI code, plus a saved MCP result                    |

### Route 5 in detail: the usable one

The CLI's code describes the request as "`@internal` Requests the signed-in
account's cloud sessions, read by the CLI with its own sign-in. Answered only for
the program that started the CLI on the same machine, over its stdin; refused on
any other connection." Two more details from the same code:

- **The `started_by_session` guard** is one environment check:
  `CLAUDE_CODE_CHILD_SESSION` is set. It was set (`1`) in this probe's
  environment, as it is for anything a Claude Code session spawns. A `launchd`
  job doesn't inherit it. The other refusal reasons are `provider`,
  `evaluation_run`, `nonessential_traffic` (when
  `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` is set), `policy`,
  `policy_unverified`, `not_signed_in`, `sign_in_expired`, `forbidden`,
  `temporarily_unavailable` and `failed`.
- **The request and the mapping.** The request is one
  `GET /v1/code/sessions?limit=200`, with `/v1/sessions` plus
  `anthropic-beta: ccr-byoc-2025-07-29` on the older API. The response maps to
  `sessions[]: {id, title, updated_at, status, environment_kind, repository, branch}`
  and `truncated`.
  - `status` takes the service's `session_status`: `running`, `idle`,
    `requires_action`, `archived`, `pending` or `failed`.
  - `environment_kind` is `bridge` (Remote Control), `anthropic_cloud`, `byoc`
    or `snap`.
  - `truncated` is true when the service reports a `next_cursor` or `has_more`.
  - The mapping discards everything else. Nothing in the request takes a cursor.

**Call count and time for one full read:** one `claude` process start and one
request. The refused read answered 2.1–3.7 s after launch, and the whole process
ran 3.2–5.1 s wall, almost all of it CLI startup. A successful read adds one HTTP
round trip of unmeasured length. The code's timeouts are 15 s per request and
25 s overall.

**30 days:** not reachable. The saved MCP page (route 6) held 50 sessions created
over about two days (2026-10-21 to 10-23) and reported `has_more: true`. At that
rate 200 sessions cover about eight days, not 30. This rate is inferred from one
page.

### Route 6: the full-status data, and why it's off-limits

One earlier session on this machine had the `claude-code-remote` MCP attached.
Its saved `list_sessions` result (50 sessions, 76 KB) has the shape the design
assumed. Under `ccr.data[]` it shows:

- `status_bucket` (`…_WORKING` 3, `…_REVIEW_READY` 18, `…_COMPLETED` 29)
- `session_status`
- `environment_kind` (all 50 were `bridge`)
- `connection_status`
- `origin`
- `external_metadata.post_turn_summary.{status_category, status_detail, needs_action, recent_action}`, present on 23 of 50
- `has_more` / `last_id` for paging

That data comes from the same `/v1/code/sessions` service. The CLI's own state
mapper reads `status_bucket` and `external_metadata.post_turn_summary` off these
records. A daemon could only reach it with one of two credentials:

- **The Claude Code OAuth access token.** It lives in the macOS keychain item
  `Claude Code-credentials`, and `~/.claude/.credentials.json` also exists on
  this host. I checked that both exist and read neither. Claude Code issued the
  token for its own use and refreshes it itself. A daemon holding a copy would
  be calling an undocumented endpoint with a borrowed credential that rotates
  underneath it.
- **`claude setup-token`.** It mints a long-lived token for running Claude Code
  non-interactively. It is still a Claude Code inference credential, not one
  issued for reading the session API, and creating it needs a browser sign-in
  the owner would have to do.

Either one breaks the issue's "use none that wasn't issued for this purpose"
rule, so neither was tried. The hosted MCP is also not attached to a headless
`claude -p` here (route 4). The docs make cloud-session listing available only to
sessions connected to Remote Control, which a daemon is not.

## What a daemon must store, and where

**Nothing of its own.** Route 5 reads with the CLI's existing sign-in. The daemon
needs:

- `claude` on its `PATH`
- a user whose Claude Code sign-in is current
- no `CLAUDE_CODE_CHILD_SESSION` in its environment, which a `launchd`
  LaunchAgent gets by default

The mirror stores the rows it reads, which is #1089's job. No token goes in the
repo or in the dashboard's config.

## What makes it fragile

- **It's `@internal`.** The request isn't in the public Agent SDK docs, and the
  CLI marks its schema internal. It can change or vanish in any release, so the
  adapter should treat an unknown `reason`, or a missing `sessions` array, as
  "source unavailable" rather than as "no sessions".
- **The sign-in and the keychain.** Over SSH, `claude mcp list` reported that the
  stored credentials of four plugin MCP servers couldn't be read because "your
  Mac's keychain is locked". Claude Code's own sign-in worked in the same shell.
  Whether it still works for a LaunchAgent, with no desktop session on the mini,
  is untested. A `not_signed_in` or `sign_in_expired` reply means someone has to
  run `claude auth login`.
- **The 200-session ceiling.** At about 25 sessions a day, a read covers about
  eight days. Older sessions drop off the snapshot, so the reconcile pass must
  not read absence as archival.
- **It's a coarse status.** `requires_action` is the only "needs you" signal.
  Reading it as "waiting on a permission prompt" is inferred from the enum, not
  observed.

## Owner check

Run this from your own terminal or a LaunchAgent, not from inside a Claude Code
session:

```bash
printf '%s\n' \
  '{"type":"control_request","request_id":"i","request":{"subtype":"initialize"}}' \
  '{"type":"control_request","request_id":"l","request":{"subtype":"list_cloud_sessions"}}' \
  | claude -p --input-format stream-json --output-format stream-json --verbose \
  | rg -F '"request_id":"l"'
```

From inside a session, this exact pipeline printed the `started_by_session`
refusal, so closing stdin early does not cut off the reply. A `"status":"ok"`
line confirms route 5. Its `sessions[]` keys confirm or correct the field list
above.
