---
created: 2026-10-24
status: proposed
---

# Ideas to borrow from workdesk for the task dashboard and fleet view

Four suggestions for the task dashboard
([`2026-10-23-task-dashboard.md`](2026-10-23-task-dashboard.md)) and the fleet
view ([`2026-10-23-fleet-view.md`](2026-10-23-fleet-view.md)), taken from
[adbutler007/workdesk](https://github.com/adbutler007/workdesk) at commit
`f33e7d3`. Two of them change what the fleet view can know about a session; the
other two are small fixes that stop stale or missing data from looking current. They
are ranked by value first. Within a value tier, a suggestion that another one
leans on comes first, whatever its size: suggestion 2 needs suggestion 1's
registry status for one case. Each is meant to be planned separately.

workdesk is a single-user, always-on localhost board over one person's Claude
and Codex sessions on one Mac. Like ours, it is a stdlib Python HTTP server
over SQLite under a LaunchAgent. Unlike ours, it also acts on sessions (launch,
close, focus a Terminal window) and calls the Anthropic API to summarize them.
Its main design record is `docs/DESIGN.md`.

**License: none.** The repository has no license file, and GitHub reports no
license, so its code is all rights reserved. This doc borrows ideas only. Each
one cites the file it came from, and no code is copied.

**Evidence.** "Verified" below means the code in workdesk, or in our repo, was
read. On this machine (Claude Code 2.1.310), the session registry that
suggestion 1 depends on was also inspected. "Inferred" means the point rests on
workdesk's `docs/DESIGN.md` or on reasoning, not on code.

## Ranking

Size is the 1/2/3/5/8 estimate scale: 1 is the smallest change, 8 the largest.

| # | Suggestion                                                  | Size | Value                                                  |
| - | ----------------------------------------------------------- | ---- | ------------------------------------------------------ |
| 1 | Read Claude Code's session registry in the collector        | 3    | High: liveness and status the hook cannot give         |
| 2 | Tell "asked a question" apart from "finished" at `Stop`     | 2    | High: Needs you depends on it                          |
| 3 | Browser-side liveness check on every page                   | 1    | Medium: an open phone tab otherwise looks current      |
| 4 | Keep a failed scan distinct from an absence, show host ages | 2    | Medium: stops worktrees vanishing on a transient error |

## 1. Read Claude Code's session registry in the collector

**What workdesk does (verified).** `src/workdesk/discovery.py` (`Discovery.claude`,
`claude_session`) reads `~/.claude*/sessions/<pid>.json` every 5 seconds. It
keeps an entry only if the pid is running **and** the process start time matches
`procStart`. A file that vanishes between listing and reading counts as a
session that ended. A file it cannot parse while its pid runs is recorded as
`unreadable`, not ended. `src/workdesk/transcript.py` (`derive_status`) trusts
the registry's `status` only while `statusUpdatedAt` is under 5 minutes old.
After that the status is treated as unknown. `docs/DESIGN.md` ("Claude
discovery") gives the reasons, measured over 17.6 hours: `claude agents --json`
leaves out sessions with a parked background job, and `status` is not refreshed
during a long turn (busy ages up to 32 minutes).

**What the registry holds here (verified).** On this machine each live session
has `~/.claude/sessions/<pid>.json` with `pid`, `procStart`, `sessionId`, `cwd`,
`kind`, `entrypoint`, `version`, `name`, `status` (seen: `idle`),
`statusUpdatedAt`, `tmux` and `bridgeSessionId`. The format is undocumented.

**Why it helps us.** The fleet view's only local session source is the
session-state hook (`scripts/session-state-hook.py`), which records events.
Events cannot show whether a session still exists. Three gaps follow:

- **A killed or crashed session looks idle forever.** Its last line is `Stop`,
  so Stale's "sessions idle past a threshold" cannot tell dead from parked. A
  pid plus `procStart` check settles it.
- **Sessions started before the machine opted in, or before a plugin update,
  are invisible** until their next event. The registry lists them at once.
- **`bridgeSessionId` may join a local session to its Remote Control session**
  (inferred from the field name, not tested). If it does, the fleet view can
  de-duplicate the Remote list against the hook's sessions. It may also narrow
  the fleet design's "Verify before building" items 1 and 2.

**Where it lands.**

- `scripts/task-dashboard/snapshot.py`: a new `live_sessions` section, built
  from an allowlist (`session_id`, `pid_alive`, `status`, `status_age_s`,
  `kind`, `entrypoint`, `version`, `bridge_session_id`). That needs `schema: 2`,
  because `validate()` keeps keys closed.
- `scripts/task-dashboard/snapshot_adapter.py`: `issues` rows of kind
  `session`, replaced per host like worktrees.
- The fleet design's Sources table.
- A fixture per Claude Code version seen, with a test that fails on an unknown
  shape, as workdesk's `docs/DESIGN.md` ("Claude discovery") proposes.

**Risks.**

- The format is undocumented and can change in any release. The contract test
  must fail loudly, and the snapshot must still ship without the section.
- `name` and `cwd` can carry sensitive text. `cwd` already ships in hook lines.
  `name` is a session title, so leave it out until the owner decides; the
  allowlist above omits it.
- `status` is stale during long turns. Report its age and let the view treat
  old statuses as unknown, as `derive_status` does.

## 2. Tell "asked a question" apart from "finished" at `Stop`

**What workdesk does (verified).** `src/workdesk/transcript.py` (`State.asked`)
counts a session as asking when either is true:

- an `AskUserQuestion` tool call is still pending;
- no tool is running, and the last assistant text, stripped of trailing
  markdown, ends in `?` (`ends_with_question`).

`src/workdesk/cards.py` and `docs/DESIGN.md` ("Needs you") put those sessions
first. A fresh registry status of `waiting` counts as asking without reading
the transcript.

**Why it helps us.** The fleet view's home page, Needs you, lists "sessions at
`need_input` or with a fresh `Notification`". For a local session, `need_input`
has no source today. The hook writes `Stop` with no hint whether the turn ended
in a question or in finished work. So a local session that asked something
either fills Needs you with every `Stop`, or is missed until the harness raises
an `idle_prompt` notification.

**Where it lands.**

- `scripts/session-state-hook.py`: on `Stop` only, read the last ~64 KiB of
  the transcript, discard the bytes before the first newline (the seek lands
  mid-record), and write one field, `asked`: `true`, `false`, or `null` when
  the tail cannot be parsed. Add a fixture whose tail starts mid-record. The
  harness passes the path as
  `transcript_path`; that is documented for hook input, but this hook has not
  tested it. No text is written, which keeps the hook's "records no prompt
  text" rule. This is hook format `v: 2`.
- `scripts/task-dashboard/snapshot.py`: `HOOK_KEYS` gains `asked`, and
  `hook_tails` must accept `v: 2` lines as well as `v: 1`. Today it drops any
  line whose `v` is not `HOOK_FORMAT_VERSION`, so a v2 line would vanish
  silently. `validate()` checks session events against `HOOK_KEYS`.
- `scripts/task-dashboard/snapshot_adapter.py`: `HOOK_FIELDS` gains `asked`,
  and the `session_events` table gains an `asked` column (in `db.py`'s schema)
  for the adapter's insert to write.
- `scripts/task-dashboard/fleet_views.py`: Needs you lists a `Stop` with
  `asked = true` and no prompt after it; a `null` is not listed. Since you
  were away (`away_items`) labels every `Stop` "finished", and should not
  when `asked` is true.
- **A pending `AskUserQuestion` never reaches `Stop`.** While the question
  waits for an answer the turn has not ended, so a check that runs at `Stop`
  cannot see it (inferred from hook semantics, not tested). That case needs
  another signal: the registry's `waiting` status from suggestion 1, or a
  `Notification` line, if the harness raises one for the question. Check what
  `waiting` means, and whether such a notification fires, before building
  either.

**Risks.**

- The hook runs on every turn and is measured at 15 ms. A bounded tail read
  should stay well under that, but measure it.
- The `?` test is a heuristic: a rhetorical question gives a false positive.
  workdesk accepts that. We should too, and label the item "asked (heuristic)".
- Transcript lines are an undocumented format. On any parse error, write
  `asked: null`, never fail the turn.

## 3. Browser-side liveness check on every page

**What workdesk does (verified).** `src/workdesk/static/app.js` (`poll`)
fetches `/api/board` on an interval. It records the time of the last fetch that
succeeded **and** returned data whose `generated_at` is fresh. Once that time is
older than `STALE_MS`, it shows a "not responding" banner. `docs/DESIGN.md`
("What the page shows") says why the check runs in the browser: it still works
when the server is hung.

**Why it helps us (verified).** Our header's "synced Nm ago" is rendered on
the server, once per page load (`render_header` in
`scripts/task-dashboard/server.py`). The page's script polls `/status` only
while a sync is running. A tab left open, which is how the fleet view is meant
to be read on the phone, keeps showing "synced 2m ago" after the server or the
tailnet link has died. That breaks the dashboard design's rule that the
dashboard "never presents stale data as current".

**Where it lands.** The `JS` block in `scripts/task-dashboard/server.py`. Poll
the existing `/status` every 30–60 s, each request with an `AbortController`
timeout so a hung server counts as a failure rather than a pending request.
Record the browser's own time of the last successful poll. Advance the age
text locally, as `age_seconds` plus the time since that success, apply the
`stale` class from `stale`, and show a "dashboard not responding" alert once
the last success is older than a threshold. `/status` already returns
`age_seconds` and `stale`, so no server change is needed.

**Risks.** Few. The poll should back off while the tab is hidden
(`document.visibilityState`), so a phone does not wake to poll.

## 4. Keep a failed scan distinct from an absence, and show each host's age

**What workdesk does (verified).** A failed discovery pass raises, and the
caller keeps its previous view: `Pass.unreadable` and `CodexFailed.partial`
in `src/workdesk/discovery.py`. `docs/DESIGN.md` ("Freshness, not guesses")
states the rule: a session ends only when it is verifiably gone, never because
a pass failed. Its Health panel shows discovery freshness.

**Why it helps us (verified).** In `snapshot.py`, `worktrees()` skips a repo
whose `git worktree list` exits nonzero (`continue`). `snapshot_adapter.ingest`
then deletes and rewrites all of that host's rows. So a transient git failure,
such as `git worktree list` running past snapshot.py's 20 s `GIT_TIMEOUT`
(the runner returns rc 124), removes that repo's worktrees from the
mirror until the next snapshot. Stale's "worktrees whose PR landed" loses them,
and a missing row reads as "cleaned up". Separately, `machines.last_snapshot_at`
is stored but not rendered anywhere. A laptop asleep for two days looks the
same as one with nothing open.

**Where it lands.**

- `snapshot.py`: a `scan_errors` list (repo and step, no output text). It
  needs a `schema: 2` bump of its own; whichever of suggestions 1 and 4 lands
  second adds its section to the bump the first one made.
- `snapshot_adapter.py`: keep the previous rows for a repo named in
  `scan_errors`. Rows carry no repo column; the repo sits only in
  `raw_json`. So narrow `ingest`'s host-wide `DELETE FROM issues WHERE source = ?`
  to skip worktree rows whose `json_extract(raw_json, '$.repo')` is in
  `scan_errors`, and have `scan_errors` name a repo by the same basename that
  `snapshot.py` writes to `worktrees[].repo`.
- The fleet header or the Health panel: one "lindev 4m · MacBook 2d" line
  from `machines`.

**Risks.** Kept rows can go stale if a repo fails on every run. Show them with
the failure and the host's age, never as current.

## Not borrowed, and why

- **Model-written session cards and checkpoints** (`src/workdesk/cards.py`,
  `continuation.py`, `docs/DESIGN.md` "Session cards"). They need a direct API
  key and a spending cap; workdesk budgets about $40 a month for cards alone.
  They also send transcript text to a model. That conflicts with the dashboard
  design's no-LLM rule for v1 and with the snapshot's rule that prompt text
  never leaves the machine. One part is worth keeping in mind if the fleet
  view's three-line brief is ever model-written: workdesk checks that any
  question a card quotes appears word for word in the agent's messages
  (`docs/DESIGN.md`, "Decisions keep Adam's words"; Adam is the person workdesk is built
  for). That check is cheap and
  stops an invented quote.
- **Acting on sessions**: Close, Close idle, Fresh start, Resume, Go to window,
  and launch scripts (`start.py`, `terminal.py`, `procs.py`). Our fleet view is
  read-only by principle 2 of the fleet design ("Read-only, as in the first
  instance", in [`2026-10-23-fleet-view.md`](2026-10-23-fleet-view.md)), and the owner's sessions run under tmux, not in
  Terminal.app windows. workdesk's four-part identity check (pid, start time, tty, tty
  leader) exists to make a kill safe; we only need the pid and start time half,
  for liveness, which suggestion 1 covers.
- **An independent watchdog process** (`deploy/watchdog.py`, per-loop
  heartbeats in `engine.py`'s `Supervisor`). Our server already has most of
  what it buys. The LaunchAgent has `KeepAlive` (`server_install.py`), the sync
  thread survives a failing run (`Syncer._run`), every `gh` and `acli` call has
  a timeout (`github_gh.run`, `jira_acli.run_acli`), and a dead sync shows up as `stale` after three
  intervals. A hung HTTP server is the remaining gap. Suggestion 3 makes it
  visible, which is enough for a read-only view. Revisit if the mini instance
  turns out to hang unattended.
- **Cookie-authenticated reads and a strict Content Security Policy**
  (`src/workdesk/server.py`). Our Host check already stops DNS rebinding, and
  every value is escaped with `html.escape`. A CSP would need nonces for our
  inline script and style, which costs more than it adds while every page is
  rendered on the server.
- **Codex discovery** (thread locks, UUIDv7 binding in `discovery.py`), and the
  Notion, Slack, Outlook and calendar sources. Out of scope: the fleet view
  tracks Claude sessions, and the dashboard reads Jira and GitHub.
- **The launchd PATH lesson** (`docs/DESIGN.md`, "Hardening"). We already have
  it: `server_install.py` and `collector_install.py` resolve each tool and write
  an explicit PATH.
