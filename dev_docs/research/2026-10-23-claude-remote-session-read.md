---
created: 2026-10-23
---

# Can a background process read the Claude Code Remote session list?

**Measured 2026-10-23** on the Mac mini, over SSH with no desktop session, using
Claude Code 2.1.309 signed in with a claude.ai (Max) account. This record answers,
for #1098, the first "Verify before building" item in the fleet view's design,
and it decides the shape of #1100. The live doc's
[Fleet instance](../task-dashboard.md#fleet-instance) section has since replaced
that design. Session titles, ids and the org id are
left out because this repo is public. Counts, timings and field names are as
measured.

## Verdict: readable without status (verified, including from a LaunchAgent with a user logged in)

A daemon has one route it can legitimately use: it starts `claude -p` itself and
sends the `list_cloud_sessions` control request on stdin. That request returns
each session's id, title, `updated_at`, a coarse `status`, `environment_kind`,
repository and branch.

It does **not** return `status_bucket`, `post_turn_summary.status_category` or
`needs_action`. It returns one page of at most 200 sessions, with a `truncated`
flag and no cursor, so it **cannot page back 30 days**. On the owner's account
that page's `updated_at` values reached back about 20 days.

The full-status data exists on the same service. Every route to it uses Claude
Code's own OAuth token against an undocumented endpoint. The issue rules that
out, since the token was not issued for a dashboard.

What was and wasn't verified:

- **Verified:** the control request exists, it is answered without a model turn,
  and it refuses any process started inside a Claude Code session.
- **Verified by the owner's run:** the fields it returns on success, and one
  page of exactly 200 sessions returned with `truncated: true`. That 200 is the
  limit the CLI requests, so the ceiling is a request parameter, not a measured
  service maximum. The probe itself ran inside a Claude Code session, where the
  guard refused it. I did not spoof the guard. The owner
  ran the command under "Owner check" from their own terminal, and the result is
  recorded there.
- **Verified from a LaunchAgent:** the same read, made from a `launchd` job in
  the background domain, returned `status: ok`. A user was logged in at the
  mini's console at the time, so a read with no desktop session at all is still
  untested.
- **Still not observed:** a `pending` or `failed` status. `running` and
  `requires_action` appeared once each, in the LaunchAgent read.

**For #1100:** take the "readable" branch, but in reduced form. Build a polling
adapter for the session **inventory**: id, title, `environment_kind`, branch,
`updated_at`, and the coarse `status`, including `requires_action`. This makes
cloud-only sessions visible. The **Needs you** categories (`need_input`,
`review_ready`, `completed`) and `needs_action` have to come from the
session-state hook, so cloud-only sessions get presence and `requires_action`,
not a post-turn category. The adapter's "incremental since last run" and "page
back 30 days" requirements can't be met on this route: each read is a full
snapshot of at most 200 sessions, and it is not ordered by `updated_at`.

## What was tried

| # | Route                                                                                 | Result                                                                                                                                                                                                                    | How known                                            |
| - | ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| 1 | Docs at code.claude.com                                                               | No public API or CLI command lists a user's cloud sessions. Cloud and other-machine sessions are listed only to a session connected to Remote Control, through `ListAgents`                                               | Read `llms.txt` and the cross-session messaging page |
| 2 | `claude --help`, `claude remote-control --help`                                       | No `claude remote …` list subcommand. `remote-control` only starts a session                                                                                                                                              | Ran                                                  |
| 3 | `claude agents --json` (and `--all`)                                                  | Works without a TTY in under 1 s. **Local sessions only**: 19 `interactive` + 1 `background`, fields `pid, cwd, kind, sessionId, name, status` (`idle`/`busy`), `state`. No cloud sessions, no post-turn fields           | Ran                                                  |
| 4 | The `claude-code-remote` MCP in a headless `claude -p`                                | **Not attached.** The init event listed 17 MCP servers and 205 tools, and the only remote-related tool was `RemoteTrigger`                                                                                                | Ran                                                  |
| 5 | SDK control request `list_cloud_sessions` over `claude -p --input-format stream-json` | Answered in 2.1–3.7 s, with no model turn: `{"status":"unavailable","reason":"started_by_session"}`, and the same for `user_initiated: true`. Outside a session, the owner's read returned `status: ok` (see Owner check) | Ran                                                  |
| 6 | Hosted MCP `/v2/ccr-sessions/-/meta/mcp`, or REST `GET /v1/code/sessions`             | Carries the full record, `status_bucket` and `post_turn_summary` included, and pages. Auth is Claude Code's OAuth token. **Not called**                                                                                   | CLI code, plus a saved MCP result                    |

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
  and `truncated`. The owner's run confirmed this key set, and no other keys
  appeared. `repository` is an object `{owner, name}`. `repository` and `branch`
  appear only when the session has them: `repository` was on 151 of 200
  sessions and `branch` on 46.
  - `status` takes the service's `session_status`: `running`, `idle`,
    `requires_action`, `archived`, `pending` or `failed`.
  - `environment_kind` is `bridge` (Remote Control), `anthropic_cloud`, `byoc`
    or `snap`.
  - `truncated` is true when the service reports a `next_cursor` or `has_more`.
  - The mapping discards everything else. Nothing in the request takes a cursor.

**Call count and time for one full read:** one `claude` process start and one
request. The refused read answered 2.1–3.7 s after launch, and the whole process
ran 3.2–5.1 s wall, almost all of it CLI startup. A later in-session repeat of
the refused read ran 1.5 s wall. A successful read from a LaunchAgent ran 2 s
wall for the whole pipeline (see the LaunchAgent result). The HTTP round trip
alone was not separated out. The code's timeouts are 15 s per request and
25 s overall.

**30 days:** not reachable. The owner's terminal read returned 200 sessions
with `truncated: true`, and the page's `updated_at` values spanned 2026-10-03 to
2026-10-23, so nothing on it was last updated more than about 20 days ago.
Because the page is truncated and takes no cursor, older sessions cannot be
read. Sessions inside that span may also be missing if the service selects the
page by creation time (below). This replaces the eight-day estimate in this
record's first version (#1115). That estimate was a creation rate extrapolated
from route 6's 50-session page, which measures something different from the
`updated_at` span of the CLI's 200-session page.

The page is not sorted by `updated_at`: 46 of its 199 adjacent pairs are out of
order. The order may be newest-created first, but the response carries no
`created_at`, so that is a guess. If the service picks the page by creation
time, a session created long ago and updated today could fall off the page while
newer idle sessions stay on it. This has not been observed.

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
  It also worked from a LaunchAgent. Inside that job, `security
  show-keychain-info` on the login keychain failed with "User interaction is not
  allowed", and the read still returned `ok`. That suggests the CLI's sign-in
  doesn't depend on reading the login keychain interactively, but it is
  inferred: the job did not record where the CLI read its token from.
  `~/.claude/.credentials.json` also exists on this host (see route 6), so the
  CLI may have read its token from that file rather than the keychain. This was
  not checked. A user was
  logged in at the console during that run, so a mini with nobody logged in is
  still untested. A `not_signed_in` or `sign_in_expired` reply means someone has to
  run `claude auth login`.
- **The 200-session ceiling.** The terminal read's `updated_at` values spanned
  about 20 days. Archived sessions count toward the 200: 132 of that read's
  sessions were archived and only 68 were `idle` (134 and 64 in the LaunchAgent
  read later that day). Older sessions drop off the snapshot, so the reconcile pass must
  not read absence as archival.
- **It's a coarse status.** `requires_action` is the only "needs you" signal.
  Reading it as "waiting on a permission prompt" is inferred from the enum, not
  observed. The LaunchAgent read held one `requires_action` and one `running`
  session, so both values do arrive on the wire. Which state a
  `requires_action` session was in was not checked.

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

### Result, 2026-10-23

The owner ran it from their own terminal, outside any Claude Code session. It
returned `"status":"ok"`:

| Measure            | Value                                                                |
| ------------------ | -------------------------------------------------------------------- |
| Sessions           | 200, `truncated: true`                                               |
| Keys               | `id`, `title`, `updated_at`, `status`, `environment_kind` on all 200 |
| Optional keys      | `repository` (`{owner, name}`) on 151, `branch` on 46                |
| `status`           | `archived` 132, `idle` 68                                            |
| `environment_kind` | `bridge` 194, `anthropic_cloud` 6                                    |
| `updated_at` span  | 2026-10-03 to 2026-10-23                                             |
| Order              | Not by `updated_at`: 46 of 199 adjacent pairs out of order           |

The wall time was not measured.

### Result from a LaunchAgent, 2026-10-23

The owner ran a script that bootstraps a one-shot job into `user/<uid>`, the
`launchd` background domain. The job ran the same pipeline, with the plist
setting only `PATH`, and the script removed the job afterwards. The script was a
one-off and is not kept in the repo. Its job amounts to a plist with these keys:

- `Label`: any reverse-DNS name
- `ProgramArguments`: `/bin/bash` and a script holding the Owner check pipeline,
  writing its output to a file
- `EnvironmentVariables`: `PATH` only, so `claude` resolves
- `LimitLoadToSessionType`: `Background`
- `RunAtLoad`: true

It was started with `launchctl bootstrap user/<uid> <plist>` and removed with
`launchctl bootout user/<uid>/<label>`. The job saw:

| Measure                     | Value                                                          |
| --------------------------- | -------------------------------------------------------------- |
| `CLAUDE_CODE_CHILD_SESSION` | unset                                                          |
| `SSH_CONNECTION`            | unset                                                          |
| Console user                | the owner, logged in                                           |
| Login keychain              | `show-keychain-info` failed: "User interaction is not allowed" |
| Reply                       | `status: ok`, 200 sessions, `truncated: true`                  |
| `status`                    | `archived` 134, `idle` 64, `requires_action` 1, `running` 1    |
| Wall time                   | 2 s for the whole pipeline, with nothing on stderr             |

So a LaunchAgent gets past the `started_by_session` guard without any help, as
the `CLAUDE_CODE_CHILD_SESSION` check predicts, and reads with the CLI's existing sign-in.
