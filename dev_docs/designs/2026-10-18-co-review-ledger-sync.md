---
created: 2026-10-18
status: proposed
tracks: https://github.com/bestdan/workflow-skills/issues/1041
---

# Sync co-review ledgers across machines

Each machine keeps its own co-review ledger at
`~/.claude/co-review/findings.jsonl`, and `/co-review-stats` reads only that
one file. Reviewer quality is a property of the reviewer, not the machine, so
the table should count every machine's runs. A cloud session's ledger is lost
when its container is.

This design moves the rows. Each machine flushes a redacted copy of its own
ledger to a private GitHub repo, one file per machine, and the stats read the
union. The review path is unchanged: it still writes only to the local file,
with no network. The flush is a separate step that runs at session end, by
hand, and before `/co-review-stats`.

The ledger's format and the firewall it serves are in
[`../reviewer-quality-score-design.md`](../reviewer-quality-score-design.md);
this design depends on both and changes neither.

## What is true today

Checked in the code on `main` (v2.93.0):

- **The writer** (`scripts/co-review-record.py`) appends one run record and
  its findings as one block, under an exclusive `flock`, and refuses a
  `(host, run_id)` already in the file. `host` defaults to
  `socket.gethostname()`. If the file's last line is torn, it ends that line
  before appending.
- **The reader** (`scripts/co-review-stats.py`) takes one `--ledger` path. It
  keys runs on `(host, run_id)`, keeps the first copy of a repeated run, and
  drops the findings that follow a repeat or an unknown-schema run. It relies
  on the writer's one-block layout, not on finding identity.
- **No hook** in the plugin runs at session start or end; `hooks/hooks.json`
  registers only `PreToolUse`, `PostToolUse`, `WorktreeCreate` and
  `WorktreeRemove`.

## Proposal

### The shared repo

A private GitHub repo the owner creates once, holding one file per machine on
a branch pinned to `main`:

```
ledgers/<host>.jsonl
```

Each machine writes only its own file, so two machines with distinct hostnames
never edit the same file. A machine is pointed at the repo by one environment
variable:

```
CO_REVIEW_LEDGER_REMOTE=git@github.com:<owner>/<repo>.git
```

It goes in the synced Claude settings (`env` block), so every local machine
that syncs settings gets it at once. Unset means sync is off: the flush exits
0 with `ledger sync: off`, and the stats read the local file alone, as today.
Cloud sessions do not read that block; see "Not decided here".

The clone lives at `~/.claude/co-review/shared/`. That keeps it outside every
working tree, and under the path the design's optional deny rule
(`Read(~/.claude/co-review/**)`) already covers, so the shared copy is fenced
exactly as the local one is.

### The clone is disposable

The flush never repairs a clone. Every row in the shared file came from a
local ledger, so a clone can always be rebuilt from the remote plus this
machine's ledger, and rebuilding is simpler and safer than any repair:

- **No rebase, ever.** A rebase can stop on a conflict and leave state that
  makes every later `git pull` fail, and after a history rewrite on the remote
  (the remedy for a leaked field) it can replay the leaked commit and push it
  again. The flush only ever fast-forwards or starts over.
- **Start over on any doubt.** Under the lock, before doing anything else, the
  flush deletes the clone and clones afresh when any of these holds: the
  directory is not a valid repo (a clone killed partway), a rebase or merge is
  in progress, or the upstream is not an ancestor of the local branch (the
  remote was rewritten). A dirty working tree or an unpushed commit is not a
  reason: step 7 resets both away. Deleting is safe because unpushed rows are rebuilt from
  the local ledger on the next step.
- **Clone atomically.** A clone goes to `shared.tmp/` and is renamed to
  `shared/` only once it succeeds, so a killed clone never looks like a clone.

### The flush — `scripts/co-review-ledger-sync.py`

Stdlib Python, PEP 723, the same tier as the writer. It has two modes, because
the two callers need different things: session end wants to send this
machine's rows cheaply, and `/co-review-stats` wants every machine's rows.

`--push` (session end, and by hand):

1. If `CO_REVIEW_LEDGER_REMOTE` is unset, print `ledger sync: off` and exit 0.
2. Take an exclusive `flock` on `~/.claude/co-review/shared.lock`, so two
   sessions on one machine never flush at once.
3. Make sure a clean clone exists, per "The clone is disposable". This is the
   only step that may need the network when nothing is new, and only when the
   clone was missing or unusable.
4. Read the **pushed** copy of this machine's file,
   `git show @{u}:ledgers/<host>.jsonl` (empty when the remote has no such file
   or no branch yet), and collect its `(host, run_id)` keys. That committed,
   pushed file is the watermark. Reading the pushed copy rather than the
   working tree means rows that were appended or committed but never pushed
   are selected again, so a kill at any step loses nothing.
5. Read the local ledger under a **shared `flock` on `findings.jsonl`**, the
   same file the writer locks exclusively, so the flush never sees a write in
   progress. Select every run block (a run record and the findings after it)
   whose key is not in the watermark. A block whose last line is unterminated
   or unparseable is skipped whole, not trimmed, so a partial run is never
   copied and then counted as flushed.
6. If nothing was selected, print `ledger sync: up to date` and exit 0. **This
   path makes no network call.** Rows left in an unpushed commit by an earlier
   flush are not in the watermark, so they are selected again here rather than
   stranded.
7. Otherwise reset the branch to its upstream (`git reset --hard @{u}`, which
   drops any unpushed commit whose rows step 5 just reselected), append the
   selected blocks redacted (below), commit (`ledger: <host> +<n> runs`) and
   push. When the remote branch does not exist yet, push with
   `-u origin HEAD:main`.
8. If the push is rejected because another machine pushed first, fetch,
   fast-forward (another machine never touches this file, so the
   fast-forward always applies), redo steps 4 to 7 once against the new
   upstream, and push again. Anything else, including a fast-forward that
   fails, deletes the clone; the next flush starts over.
9. Print one receipt line and exit 0: `ledger sync: pushed <n> runs` or
   `ledger sync: ❌ <reason>`. Never ledger contents. The flush never exits
   non-zero, so neither the hook nor the command can fail because of it.

`--pull` (before `/co-review-stats`): run `--push`, then fetch and
fast-forward so every machine's file is current, and print a second receipt
line naming how many host files the clone holds. This always makes one network
call, which is the price of a fresh table.

Failure leaves the local ledger untouched, always: the flush only reads it,
under a shared lock.

### Redaction: an allowlist, not a denylist

The shared repo is private, but git history is permanent, so a field that
leaks is expensive to take back: a history rewrite, after which every clone
must be discarded rather than pulled (the clone rules above do that). The
flush therefore copies **only** the fields below, and a field added to the
ledger later stays local until someone adds it here on purpose. This matters
more than it looks: the writer copies every key the reconciler returns onto a
finding (`co-review-record.py:123`), so a finding can carry fields nobody
listed.

| Record              | Fields copied                                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------------------------- |
| run                 | `type`, `schema`, `run_id`, `host`, `ts`, `repo`, `mode`, `diff_lines`, `co_review_version`, `reconciler_model` |
| run → each reviewer | `name`, `disposition`, `model`, `duration_s`                                                                    |
| finding             | `type`, `run_id`, `host`, `sources`, `human_corroborated`, `verdict`                                            |

Every field `co-review-stats.py` reads is in that table. Left behind, with the
reason:

- **`issue`, `rationale`, `recommended_fix`, `file`, `line`**: code and prose
  from private repos, including work repos. No stats column uses them.
- **Each reviewer's `command` and `reason`**: a custom reviewer's command
  string can carry a token or a path, and the stats use neither.
- **`pr`, `base_sha`, `head_sha`**: they identify a commit in a private repo,
  and the stats use none of them.

`repo` and `host` are the identifying fields kept. `repo` is the only way to
ask whether a reviewer is good on one codebase and bad on another (see the open
question below), and `host` names the file and the run.

### The read

`co-review-stats.py` takes `--ledger` more than once, and a directory as a
`--ledger` value means every `*.jsonl` in it, in sorted order. With sync on,
its default becomes the local ledger followed by
`~/.claude/co-review/shared/ledgers/`. The local file comes first, so this
machine's full rows win over its own redacted copies, which the existing
`(host, run_id)` rule then drops. The empty-ledger message names every path it
looked at.

The existing rule needs no change, because each file keeps the one-block
layout: the flush appends whole blocks, in local order, and every shared file
begins with a run record, which resets the drop-after-a-repeat state at the
file boundary. The `seen` set carries across files, so a run that appears in
two files counts once.

The reader stays offline. `/co-review-stats` runs `--pull` first and then the
stats, and a failed flush still shows the table with its receipt line above
it. The flush needs the network and writes `~/.claude/co-review/shared/`,
neither of which the Bash sandbox allows, so the command runs it unsandboxed,
the way `recording.md` already runs the writer, and grants it with its own
`allowed-tools` entry (plus the `rtk` twin the machine's settings need).

### When the flush runs

- **At session end**, from a new `SessionEnd` hook in the plugin, running
  `--push`. With a usable clone and nothing new it makes no network call, so
  running it at the end of every session is cheap.
- **Before `/co-review-stats`**, running `--pull`.
- **By hand**, for a machine that wants to flush now.

**The session-end budget is 1.5 seconds**, and a plugin cannot raise it:
Claude Code kills a `SessionEnd` hook that overruns and discards its output,
and only a `timeout` in the user's own settings (up to 60 s) or
`CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` extends it. A clone or push often
takes longer. The design survives the kill (steps 3, 4 and 8 make any partial
state harmless), but a killed flush pushes nothing. So the hook does not run
the flush itself: it starts the flush as a detached background process and
exits at once. On a local machine the flush then finishes after the session
has gone. In a cloud container, teardown may kill it first; see "Not decided
here".

The hook always exits 0. Its output goes to the debug log, not to any model's
context: Claude Code keeps `SessionEnd` output out of the conversation, shows
stderr to the user only on a non-zero exit, and has no way for this event to
add context.

### `/doctor`

A new check, written as a script that prints one line and never rows. When
`CO_REVIEW_LEDGER_REMOTE` is set, it confirms the clone is a valid repo and
that `git ls-remote` reaches the remote (unsandboxed, like the flush). When it
is unset, it reports sync as off, which is not a warning.

## Risks

- **Two machines with the same hostname** would share one file. Their
  `run_id`s still differ (each carries 4 random hex characters,
  `recording.md`), so nothing is double-counted. But each machine's step 7
  resets the shared file to the pushed copy and appends its own rows, so their
  pushes race. Step 8 retries once, and a loser that loses again pushes its
  rows on the next flush. Rows are delayed, never lost, because the watermark
  is the pushed file. Containers and cloned VMs are where this happens.
  Mitigation in scope: the writer and the flush both honour a `CO_REVIEW_HOST`
  override.
- **A cloud session's hostname may change every session** (inferred, not
  checked), so each one would add a new file. That is correct, just untidy:
  every file is a valid ledger and the union is unchanged.
- **The firewall now covers a second path.** The shared clone holds every
  machine's scores. The flush prints only its receipt, the hook's output does
  not reach a model, and the clone sits under the existing deny path. A
  session that runs `/co-review-stats` carries the scores in context exactly
  as today, and the same-session rule still applies.
- **A leaked field is permanent** until a history rewrite. The allowlist makes
  a leak something a person did on purpose, and the clone rules mean a rewrite
  cannot be undone by an old clone pushing the leak back.

## Tests

Bats, against a local bare repo as the remote (`file://`), with
`GIT_CONFIG_GLOBAL=/dev/null`:

- Redaction: a run with every field set, plus an unknown field on a finding,
  reaches the shared file with exactly the allowlisted fields.
- A second `--push` with nothing new appends nothing, makes no commit, and
  does not contact the remote (an unreachable remote still says up to date).
- Rows committed but not pushed are pushed by the next `--push`, even with
  nothing new locally.
- A kill after the append, after the commit, and during the clone: the next
  `--push` produces the same shared file as an uninterrupted run.
- A deleted, half-cloned, mid-rebase or rewritten-upstream clone is discarded
  and recloned, and a dirty one is reset; the shared file comes out the same.
- Two sessions on one host: the second waits on the lock, and the shared file
  holds each run once.
- A writer appending during a flush: the flush copies either the whole block
  or none of it.
- A push rejected because another host pushed: fetched, fast-forwarded,
  retried, and the other host's file is untouched.
- An empty remote: the first `--push` creates `main`.
- The receipt is the only output, and the exit code is 0 on every failure.
- Unset remote: `ledger sync: off`, exit 0, no clone made.
- Stats over two hosts' files plus the local ledger: run counts are the sum of
  distinct runs, and this machine's runs count once.

## Not decided here

These need the owner:

1. **The repo's name and owner.** Proposed: a private
   `bestdan/co-review-ledgers`.
2. **Whether `repo` is copied as-is.** It names work repos. The alternatives
   are to copy a salted hash, which keeps `--repo` working only for a caller
   who knows the salt, or to drop it and lose `--repo` on the merged data.
   Proposed: copy it as-is, since the repo is private to its owner.
3. **The `SessionEnd` hook.** Proposed: the detached background flush above.
   The alternatives are to ask the owner to raise the budget in their own
   settings (60 s at most), or to flush only by hand and before
   `/co-review-stats`, which loses cloud sessions' rows.
4. **Cloud sessions.** Three things are unverified, and all three must hold
   for a cloud session's rows to survive: the plugin is installed there, so
   the hook exists; `CO_REVIEW_LEDGER_REMOTE` reaches it, since the local
   settings `env` block does not; and the container has credentials to push
   to the shared repo, which is not the repo the session started on. If any
   fails, cloud rows are lost, as today, and the design still serves local
   machines.

## Graduation

- When built, the sync section moves into
  [`../reviewer-quality-score-design.md`](../reviewer-quality-score-design.md)
  §2 ("Several machines, one table"), replacing its pointer to #1041.
- The decision on `repo` redaction becomes a decision record.
