---
created: 2026-10-18
status: accepted
tracks: https://github.com/bestdan/workflow-skills/issues/1041
---

# Sync co-review ledgers across machines

Each machine keeps its own co-review ledger at
`~/.claude/co-review/findings.jsonl`, and `/co-review-stats` reads only that
one file. Reviewer quality is a property of the reviewer, not the machine, so
the table should count every machine's runs. A cloud session's ledger is lost
when its container is.

This design moves the rows. Each machine flushes a redacted copy of its own
ledger to a private GitHub repo, `bestdan/co-review-ledgers`, one file per
machine, and the stats read the union. It covers local machines; cloud
sessions are a follow-up (see "Decisions"). The review path is unchanged: it still writes only to the local file,
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
Cloud sessions do not read that block; see "Decisions".

The clone lives at `~/.claude/co-review/shared/`. That keeps it outside every
working tree, and under the path the design's optional deny rule
(`Read(~/.claude/co-review/**)`) already covers, so the shared copy is fenced
exactly as the local one is.

### The clone is disposable

The flush never repairs a clone. Every row in the shared file came from a
local ledger, so a clone can always be rebuilt from the remote plus this
machine's ledger, and rebuilding is simpler and safer than any repair:

- **No rebase or merge, ever.** A rebase can stop on a conflict and leave state
  that makes every later `git pull` fail, and after a history rewrite on the
  remote (the remedy for a leaked field) it can replay the leaked commit and
  push it again. The flush moves its branch only with `git reset --hard` onto
  the fetched upstream, or starts over.
- **Start over when the clone is broken.** Under the lock, before anything
  else, the flush deletes the clone and clones afresh when the directory is not
  a valid repo (a clone killed partway) or a rebase or merge is in progress.
  A dirty working tree is reset (`git reset --hard`, no network). Unpushed
  commits are left for step 7 to reset away.
- **Start over when the remote was rewritten.** After every fetch (step 8 and
  `--pull`), if the fetched upstream is not an ancestor of the local branch,
  the flush deletes the clone. The check needs a fetch, so the no-network path
  cannot see a rewrite; the next fetch does, and the rewrite changes no keys,
  so a stale watermark meanwhile does no harm.
- **Clone atomically.** A clone goes to `shared.tmp/` with `-b main` and is
  renamed to `shared/` only once it succeeds, so a killed clone never looks
  like a clone, and a remote whose default branch is missing cannot leave a
  clone with no `HEAD`.

Deleting is always safe, because unpushed rows are rebuilt from the local
ledger. The same property means **rows cannot be deleted from the shared repo
while a local ledger still holds them**: the next flush pushes them back. That
is by design. To remove rows everywhere, remove them from the local ledger
first.

### The flush — `scripts/co-review-ledger-sync.py`

Stdlib Python, PEP 723, the same tier as the writer. It has two modes, because
the two callers need different things: session end wants to send this
machine's rows cheaply, and `/co-review-stats` wants every machine's rows.

**The empty remote.** A fresh remote has no branch, so a clone of it has no
upstream and no `HEAD`, and every git command that names `@{u}` fails. The
flush probes `git rev-parse --verify -q '@{u}'` once. When it fails, the
watermark is empty, steps 7 and 8 skip their reset, and the push is
`git push -u origin HEAD:main`, which creates the branch. The branch is pinned
to `main` throughout.

`--push` (session end, and by hand):

1. If `CO_REVIEW_LEDGER_REMOTE` is unset, print `ledger sync: off` and exit 0.
2. Take an exclusive `flock` on `~/.claude/co-review/shared.lock`, so two
   flushes on one machine never run at once. Try without blocking and retry
   for at most 30 seconds; on timeout, print
   `ledger sync: ❌ another flush is running` and exit 0. A background flush
   at session end can hold the lock through a slow push, and
   `/co-review-stats` must not wait on it without bound.
3. Make sure a usable clone exists, per "The clone is disposable". This is the
   only step that may need the network when nothing is new, and only when the
   clone was missing or broken.
4. Read the **pushed** copy of this machine's file,
   `git show @{u}:ledgers/<host>.jsonl`, and collect its `(host, run_id)` keys.
   That pushed file is the watermark. When `git cat-file -e` says the file is
   absent from `@{u}`, the remote has no file for this host yet and the
   watermark is empty; with no upstream at all, it is empty too (above). Any
   other failure deletes the clone and ends the flush. Reading the pushed copy rather than the working tree
   means rows that were appended or committed but never pushed are selected
   again, so a kill at any step loses nothing.
5. Read the local ledger, opened read-only, holding a **shared `flock` on
   `findings.jsonl`** for the whole read. The writer locks the same file
   exclusively, so the flush never sees a write in progress. A missing ledger
   is an empty one. Select every run block (a run
   record and the findings after it) whose key is not in the watermark. A
   block whose last line is unterminated or unparseable is skipped whole, not
   trimmed, so a partial run is never copied and then counted as flushed.
6. If nothing was selected, print `ledger sync: up to date` and exit 0. **This
   path makes no network call.** Rows left in an unpushed commit by an earlier
   flush are not in the watermark, so they are selected again here rather than
   stranded.
7. Otherwise reset the branch to its upstream (`git reset --hard @{u}`, which
   drops any unpushed commit whose rows step 5 just reselected), append the
   selected blocks redacted (below), commit (`ledger: <host> +<n> runs`) and
   push.
8. If the push is rejected because the remote moved, fetch, apply the rewrite
   check, and redo steps 4 to 7 once. The reset in step 7 is what moves the
   branch onto the other machine's commit; the local commit is discarded and
   its rows re-appended from the fresh watermark. This works whether the other
   machine wrote a different file or, with a shared hostname, the same one. A
   second rejection, or any other failure, deletes the clone; the next flush
   starts over.
9. Print one receipt line and exit 0: `ledger sync: pushed <n> runs`, or
   `ledger sync: ❌ <reason>` where `<reason>` is a fixed word or phrase
   (`unreachable`, `clone failed`, `push rejected twice`, `another flush is
   running`), never git's own message, which can carry the remote URL. Never
   ledger contents. The flush never exits non-zero, so neither the hook nor
   the command can fail because of it.

`--pull` (before `/co-review-stats`): run `--push`, then fetch, apply the
rewrite check, and `git reset --hard @{u}` so every machine's file is current.
A reset, not a fast-forward: a fast-forward refuses a dirty tree and a
rewritten upstream, and a reset handles both. Print a second receipt line
naming how many host files the clone holds. This always makes one network
call, which is the price of a fresh table.

Failure leaves the local ledger untouched, always: the flush only reads it,
under a shared lock. A shared reader cannot hold up the writer for long: one
flush runs at a time, it holds the lock only while reading the file, and the
kernel releases it if the flush dies. The lock order is one-way (the flush
takes `shared.lock` then `findings.jsonl`, the writer only `findings.jsonl`),
so the two cannot deadlock.

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
`(host, run_id)` rule then drops. A missing `shared/ledgers/` is not an error:
it is absent on a fresh machine, and a failed `--pull` may have just deleted
the clone, so the stats then read the local file alone, as with sync off. The
empty-ledger message names every path it looked at.

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
the flush itself: it starts the flush detached and exits at once. Detached
means a new session (`setsid`, or a double fork) with stdin, stdout and stderr
all redirected away from the hook, to `/dev/null` or a log file; Claude Code
refuses a hook whose background child keeps its output open. The flush then
finishes after the session has gone, **if** the harness does not kill the
hook's whole process group or session when it cancels the hook. Its docs do
not say, so this is unverified, and the Tests section carries a manual check
that must pass before the hook ships. If it fails, the fallback is Claude
Code's documented `async: true` hook (see "Decisions").

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
- **The watermark is the pushed file, and that cuts both ways.** Step 7's reset
  discards an unpushed commit, which is safe only while the local ledger still
  holds those rows. A local ledger deleted between a failed push and the next
  flush loses them. And rows removed from the remote come back from any
  machine whose local ledger still has them (see "The clone is disposable").
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

Bats, against a local bare repo as the remote (`file://`, created with
`git init --bare -b main`, since a bare repo whose `HEAD` names a branch that
does not exist clones with no `HEAD` at all), with
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
- A push rejected because another host pushed: fetched, reset, retried, the
  push succeeds, **the clone survives**, and the other host's file is
  untouched. The same with two machines sharing a hostname: both runs end up
  in the file.
- An empty remote: the first `--push` runs steps 3 to 7 with no upstream and
  creates `main` without recloning in a loop; a remote that has `main` but no
  file for this host gives an empty watermark.
- `--pull` right after a `--push` that deleted the clone: the stats still run,
  on the local ledger alone.
- `--pull` on a dirty clone and on a clone whose remote was rewritten: both end
  on the fetched upstream, the rewritten one by recloning.
- A flush that cannot get `shared.lock` within its bound prints
  `another flush is running` and exits 0.
- The receipt is the only output, the exit code is 0 on every failure, and the
  `❌` reason is one of the fixed words, never git's message.
- Manual, on a real Claude Code session: the `SessionEnd` hook returns within
  1.5 s, and the shared repo has the session's rows after it exits.
- Unset remote: `ledger sync: off`, exit 0, no clone made.
- Stats over two hosts' files plus the local ledger: run counts are the sum of
  distinct runs, and this machine's runs count once.

## Decisions

Made by the owner on 2026-10-18.

1. **The shared repo is a new private `bestdan/co-review-ledgers`.** A repo of
   its own, so the flush's resets and reclones never act on anything else.
2. **`repo` is copied as-is.** It names work repos, but the shared repo is
   private to its owner, and copying it keeps `--repo` working on the merged
   data. Rejected: a salted hash (a salt to sync across machines) and dropping
   the field (`--repo` would see only local rows).
3. **The `SessionEnd` hook starts a detached flush**, and ships only after the
   manual check in Tests passes. If the detached flush does not survive the
   hook's cancellation, the fallback is an `async: true` hook, which Claude
   Code documents as running in the background with no timeout, provided
   `SessionEnd` accepts it. Rejected: asking the owner to raise the budget in
   their settings, and flushing only by hand and before `/co-review-stats`.
4. **Cloud sessions are out of scope.** Their rows stay lost, as today. Three
   unverified conditions decide whether they can be saved: the plugin is
   installed there, `CO_REVIEW_LEDGER_REMOTE` reaches the session, and the
   container can push to the shared repo. Checking them is
   [#1048](https://github.com/bestdan/workflow-skills/issues/1048).

## Graduation

- When built, the sync section moves into
  [`../reviewer-quality-score-design.md`](../reviewer-quality-score-design.md)
  §2 ("Several machines, one table"), replacing its pointer to #1041.
- The decision to copy `repo` as-is becomes a decision record.
