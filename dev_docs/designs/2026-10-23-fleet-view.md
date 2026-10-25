---
created: 2026-10-23
---

# fleet view — a second task-dashboard instance for agent work across machines

Proposal for a second deployment of the task dashboard
([`2026-10-23-task-dashboard.md`](2026-10-23-task-dashboard.md)). The first
instance mirrors Jira tickets on one work laptop. This one mirrors the owner's
**agent work**: Claude sessions, PRs, issues, worktrees and handoffs, spread
across three personal machines. It runs on the Mac mini and is read from a phone
over the tailnet.

Status: design only. It reuses the first instance's engine, mirror/overlay
split, adapter seam and read-only rule, so this doc covers only what differs.

## The problem it answers

When many agent sessions run at once, the owner's attention goes to whichever
session asks next, not to whichever matters most. Four costs follow:

- **Switching:** frequent switches between sessions.
- **Re-orienting:** prompts spent asking "where was I", "what's next".
- **Rubber-stamping:** approvals answered one at a time as they arrive.
- **Rot:** sessions, worktrees, PRs and handoffs left open until they go
  stale.

The owner's private usage retro measured all four. It also proposed a
work-in-progress reset, a process design that lives in the owner's dotfiles
repo.

That design has two halves:

- **Rules for agents.** Close by evidence, vet new work, standing permission
  for routine steps, the monthly drain sweeps. These stay in that design.
- **Measuring and reporting.** This view absorbs that half:
  - the table of figures and targets becomes a live **Health** panel
  - the weekend-morning "refresh of state" becomes **Since you were away**
  - the monthly drain's results get one place to be read, on the phone
  - the side-quest cap and the deep-work pattern get gauges

workflow-skills#952 (graduated merge autonomy) contributes the morning digest
of PRs with a three-line decision brief, and the cap on the human-review queue.

## Principles

1. **One decision first.** Every view leads with the single item that most
   needs the owner, with a brief and a link. A long list of next steps where
   only the first one needs feedback is the failure this exists to remove.
2. **Read-only, as in the first instance.** The view never answers a prompt,
   merges, closes or removes anything. Each item links to where the action
   happens (the session, the PR) and names the skill that resolves it:
   `/sweep-for-archive`, `worktree-remove.sh`, `/co-review`.
3. **Every rule has a trigger and a check** (borrowed from the reset design).
   A gauge exists only if something feeds it, and every figure has a target
   or is marked "observed".

## Deployment

```
MacBook ─┐                                   ┌─ phone (tailnet link)
lindev ──┼─ collector ── snapshot ──▶ mini: ingest ─▶ SQLite ─▶ HTTP server
mini ────┘   (per machine)   (POST, token)    + GitHub / Claude Remote adapters
```

- **The aggregator runs on the Mac mini**, as the same `launchd` daemon the
  first instance uses, with `server.extra_allowed_hosts` set to the mini's
  tailnet name. It is opened through `ts-share.sh`.
- **Each machine runs a small collector** on a schedule (`launchd` on macOS,
  `systemd --user` on lindev). It builds a snapshot of machine-local state and
  POSTs it to the mini's ingest endpoint over the tailnet, with a per-machine
  token.
  - A laptop that was asleep posts when it wakes, and the snapshot carries its
    own timestamp.
  - Push, not pull, because collecting over ssh fails while the laptop sleeps,
    and messages between sessions on different machines don't arrive
    reliably.
- **The work laptop is not part of this.** It has no tailnet and runs the Jira
  instance. The two instances share code, not data.

## Sources

| Source                      | Read by                                  | Gives                                                                                                                                                                       |
| --------------------------- | ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Claude Code Remote sessions | aggregator, adapter                      | optional, off by default: cloud-hosted sessions (not Remote Control), with title, repo, branch, `updated_at` and a coarse `status`; it has no post-turn fields (see below). |
| GitHub, configured owners   | aggregator, adapter (task 3's `gh` code) | open PRs (age, draft, review decision, CI), issues at `auto:human-review-needed`, designs at `status: proposed`                                                             |
| Session-state hook          | collector                                | local sessions the Remote list misses, with exact turn-end and prompt times and permission prompts                                                                          |
| Worktrees                   | collector                                | per repo: branch, dirty or unpushed state, and the landed-PR evidence `branch-remove.sh` already computes                                                                   |
| Handoffs                    | collector                                | `$HOME/src/worktrees/*/.handoffs/*.md`: front matter `aim`, `expires`, `branch`, `pr`                                                                                       |
| tmux                        | collector                                | session names, created time, attached state                                                                                                                                 |
| Prompt history              | collector                                | `~/.claude/history.jsonl`, for the switch rate and the count of sessions active at once                                                                                     |
| Drain runs                  | aggregator, adapter                      | the monthly drain's tracking issue: each machine's "removed / kept / needs you"                                                                                             |

**The session-state hook is the one new piece of instrumentation.** It is a
Claude Code hook on `UserPromptSubmit`, `Stop` and `Notification`. Each event
appends `{session, cwd, event, ts}` to
`~/.local/state/fleet/sessions/<session>.jsonl`, and the collector ships the
tails. The hook gives three things the reset design couldn't get from
transcripts:

- **Both waits, exactly.** The owner waiting on an agent is prompt to `Stop`.
  An agent waiting on the owner is `Stop` or `Notification` to the next prompt.
- **A count of harness permission prompts**, which was recorded as "not
  recorded in transcripts".
- **A live "waiting on you" bit** for local sessions that never appear in the
  Remote list.

It ships through the plugin's `hooks/hooks.json`, and **does nothing unless
the machine opts in**: it exits at once unless
`~/.config/task-dashboard/config.toml` has `[collector] enabled = true`. The
plugin is public and installed by people who never asked to have their
sessions logged. On an opted-in machine it needs no further setup.

**Session state comes from the hook; the Remote list is an optional
inventory.** #1098 found that a background process can read the session list
only through an `@internal` request (`list_cloud_sessions`, answered by
`claude -p` for the program that started it). That request returns one page of
at most 200 sessions with no cursor, about 20 days on the owner's account, not
ordered by `updated_at`, and none of the service's `status_bucket`,
post-turn `status_category` or `needs_action`. The adapter derives coarse
stand-ins for those three from `status` alone. So, as decided on #1100:

- Every sync on an instance that ingests snapshots folds the hook events into
  one row per session (`sessions.py`, `source = 'claude-hook'`): waiting or
  working, `need_input` after a permission prompt, the empty-spawn flag, and
  exact times. Sessions on a machine without the plugin are not in it.
- With `claude_remote.enabled = true` (default `false`), each sync also reads
  the list (`claude_remote.py`, `source = 'claude-remote'`). With it off,
  **cloud-only sessions are invisible**. With it on they appear with the coarse
  status only, and each read is a full snapshot: it cannot fetch "updated
  since the last run" or page back 30 days. A session the page reports
  `archived` is dropped; one missing from the page is kept, since it may have
  fallen off a truncated page, and ages out after 30 days unseen.
- The two sources key a session differently, and nothing joins them, so the
  list keeps only `anthropic_cloud` sessions. A Remote Control session is the
  hook's to report, and is invisible on a machine without the plugin.

**The GitHub row is a sync step of its own, behind `github.fleet`.** With
`github.fleet = true` (default `false`; `github.enabled = false` also turns it
off), every scheduled sync and every `dashboard.py sync` runs
`github_fleet.fetch()` and `write()` after the Jira sync, whatever that sync's
outcome. The step records its own `sync_runs` row, kind `github_fleet`, so its
failure never touches the Jira rows or the Jira run's outcome, and staleness is
judged on Jira runs alone. A failed fleet run gets its own header alert (the
`gh auth login` hint on an auth error) and shows as `/status.fleet_run`.
`/status.review_queue` and the header's review-queue gauge read the same
counts: "—" / null before the first successful fleet run or while the gate is
off, and 0 after a successful run that found nothing. `dashboard.py sync`
prints both runs and exits nonzero if either failed; the Jira run's exit code
wins when both did.

## Views

1. **Needs you** — the home page.
   - **What's on it:** sessions at `need_input` or with a fresh
     `Notification`, PRs waiting on the owner's review, and drain items marked
     "needs you". Ordered by priority from the overlay, then by how long each
     has waited.
   - **The first item is expanded** with a three-line brief:
     - what changed
     - the one thing to decide
     - what a "no" would invalidate

     The rest stay collapsed to one line each.
   - **Approvals are batched.** Plain approvals ("commit and open the PR?")
     are grouped into one sweep, so they don't interleave with real decisions.
   - A session's brief comes from its post-turn summary. A PR's comes from its
     body and the co-review summary, when one exists.
2. **Since you were away.**
   - What merged, closed or finished since the last visit (recorded in the
     overlay), and what newly needs the owner.
   - The weekend design block opens here, scoped to the designs the overlay
     marks as the block's focus. That is the reset design's "morning refresh
     of state".
3. **Stale, with evidence.**
   - **What's on it:**
     - sessions idle past a threshold
     - Remote Control sessions with no user prompt (empty spawns)
     - worktrees whose PR landed
     - handoffs past `expires`
     - PRs and issues past a threshold
   - **Each item shows its evidence and the action that resolves it.** Age
     only orders the list. The close-by-evidence rule decides, and the view
     never suggests closing something on age alone.
4. **Health.** The reset design's figures, live, each with its target.

   | Figure                                           | Source                           |
   | ------------------------------------------------ | -------------------------------- |
   | Approval-shaped and bare "y"/"yes" prompts       | prompt history                   |
   | Harness permission prompts                       | session-state hook               |
   | Switch rate; prompts with 3+ sessions active     | prompt history                   |
   | Owner waiting on agents; agents waiting on owner | session-state hook               |
   | Open PRs across the configured repos             | GitHub                           |
   | Issues opened : closed                           | GitHub                           |
   | Designs at `proposed`                            | GitHub (design-doc front matter) |
   | Side quests in flight                            | overlay tags                     |
   | Human-review queue length                        | GitHub                           |

   The panel replaces the manual re-measure. The first data point is whatever
   the collectors have gathered by the reset design's re-measure date. A dated
   research record still captures each re-measure, as the reset design
   requires.
5. **Gauges and focus**, shown in every page's header:
   - **Side quests:** in flight against the cap of 3. The overlay's
     `side-quest` tag marks them.
   - **Review queue:** length against the cap of 5 from #952. A PR counts
     when its CI is green and it is co-reviewed at its head. A PR that closes
     an issue in a milestone also needs one of those milestones marked
     `approved: yes`; a PR that closes none skips that rule, since most repos
     use no milestones. The full rule set is `github_fleet.py`'s module
     docstring.
   - **Focus pin:** marks one item as the deep work. While it is set, Needs you
     shows the focus item's own needs and counts the rest as "held". It never
     shows the held items one by one. That is the deep-work pattern: others may
     notify, and no answer is expected.

**The two caps limit different things and both stand.** The side-quest cap
limits which tooling work is open. The review-queue cap limits how much
finished work waits on the owner. The reset design rejected a cap on live
sessions, and this view adds none.

## Overlay additions

Overlay rows are keyed by `(source, key)`, so they attach to any item kind. On
top of the first instance's pins, order, snoozes and notes:

- `kind_tag`: `side-quest` or `substantial`. An item tagged `substantial` must
  name the figure it moves, as a free-text note.
- `focus`: at most one item at a time.
- `last_visit`: one timestamp, written when Since you were away is opened.

## Engine changes this needs from the first instance

- **Generic work items.** The mirror holds items with `source` and `kind`,
  keyed by `(source, key)`. This was requested on #1089, before its sync
  engine is written.
- **An ingest endpoint** on the server. It accepts a machine's snapshot `POST`
  under a per-machine token, separate from the page token. It is enabled only
  when configured, so the work laptop instance never has one.
- **Adapters:** Claude Code Remote sessions, the snapshot reader, prompt-history
  metrics, and GitHub issues and design docs alongside task 3's PRs.

## Verify before building

1. **Can a background process read the Claude Code Remote session list,
   including `status_category` and `needs_action`?** So far it has only been
   read through a Claude session's MCP tools.
   - If it can't, every Remote Control session must come from the session-state
     hook instead. That works only for sessions on machines with the plugin, so
     cloud-only sessions would be invisible.
   - This is the first spike, because Needs you depends on it.
2. **Does the session-state hook see Remote Control sessions?** Do
   `Notification` and `Stop` fire for a session driven from the phone?
3. **Is `history.jsonl` enough for the switch rate?** It needs a session id and
   a timestamp per prompt, the same as the retro's analysis script used.
4. **Can ingest reach the mini from lindev and the MacBook over the tailnet,
   with the Host and token checks in place?**

## Out of scope

- Answering prompts or approvals from the view. That is dotfiles#1013's
  problem, on the phone.
- Messaging between sessions (dotfiles#1020). The view reads state and never
  relays.
- Enforcing the caps. The reset design's "habits before enforcement" decision
  stands: the gauges show the caps, and nothing blocks on them.
- The work laptop, and any Jira data, in this instance.
