---
created: 2026-09-21
aim: "stand the nightly gh-issue job up as a local LaunchAgent on the mini, because a cloud routine cannot run the handler"
branch: bestdan/nightly-gh-issue-routine
pr: https://github.com/bestdan/workflow-skills/pull/819
expires: "when the LaunchAgent is installed on the mini and has completed one nightly run"
---

# Nightly gh-issue job — finish it on the mini

## Why this file is tracked, against this directory's rule

[`README.md`](README.md) says every file here except itself stays untracked, and
that the asymmetry is the point. This file is force-added anyway, for the same
reason the README is: **the next session is on a different machine.** An
untracked note cannot reach the mini, and the mini is where the remaining work
happens. That is the README's own stated exception ("the conventions have to
reach the next machine"), applied to a handoff rather than to conventions — a
deliberate override, not an accident. It is still a handoff, so **delete it when
the work is done** (see the end).

## The aim

Run a nightly pass over this repo's GitHub-Issues board: repair the label
invariants, triage what arrived untriaged, then take **one** ready issue as far
as an open PR at `status:4_needs_review`. Nothing merges or closes unattended.

The pipeline itself is settled and committed —
[`dev_docs/nightly-gh-issue-routine.md`](../nightly-gh-issue-routine.md) on this
branch is the runbook, and it is correct. Only the **runner** is unresolved.

## What is already done

- **The runbook**, committed on this branch. Three verbs, each invoked by name:
  `/reconcile-tasks --all --apply`, `/promote-tasks`,
  `/do-tasks --local --non-interactive`, behind a step-0 preflight.
- **The measurements**, in
  [`dev_docs/research/2026-09-21-nightly-gh-issue-routine-preflight.md`](../research/2026-09-21-nightly-gh-issue-routine-preflight.md).
  Four cloud-routine probe runs. Read this before re-deriving anything.
- **The cloud routine is built and now DISABLED**: `trig_01CaxcFEgmXmJ4T4q8yy1pGc`,
  <https://claude.ai/code/routines/trig_01CaxcFEgmXmJ4T4q8yy1pGc>. Left in place
  rather than deleted, so it can be re-enabled if the REST port below ever lands.
  Disabled because it would otherwise fail its preflight nightly for no benefit.
  There is also a probe routine, `trig_01EErQvfaW8qBHYVfdtqpXSb`, cron
  `0 4 29 2 *` (manual runs only) — reuse it rather than making another.

## The blocker that forced this decision

**A cloud routine's proxy refuses GitHub GraphQL entirely** — `HTTP 403`, on
every GraphQL-backed call, measured across twelve calls on 2026-09-21. REST
(`gh api repos/{owner}/{repo}/...`) works fine for the same data.

`gh`'s porcelain is GraphQL. The handler's **shipped executable assets** call
`gh issue list` (12), `gh pr list` (12), `gh pr edit` (10), `gh pr view` (8),
`gh repo view` (6), `gh pr ready` (2). Only `gh-issue-state.py` is on `gh api`.

So candidate-finding and PR creation both fail, and **no step of the pipeline is
independently runnable in the cloud**. This is not a `gh` version problem and not
a `gh` presence problem — `gh` 2.45.0 is installed there now and REST works.

On the mini, GraphQL is served normally, so the runbook works unchanged. That is
the whole reason for running it locally.

## Build this

Follow the house pattern already in `bestdan/dotfiles`:
**`scripts/ups-power-watch.sh`** is the model — a per-user LaunchAgent with
`install` / `uninstall` / `status` / foreground-run subcommands, and a header
comment explaining why it is an agent rather than a daemon. Match its shape,
its subcommand surface, and its commenting register.

1. **`dotfiles/scripts/nightly-gh-issue.sh`**
   - `install` writes `~/Library/LaunchAgents/<label>.plist` and `launchctl load`s it;
     `uninstall` unloads and removes; `status` reports loaded/not and last run;
     a foreground subcommand runs the job once for testing.
   - The job invokes `claude -p` with a prompt that points at the runbook and
     tells it to follow the file exactly. The cloud routine's prompt (on the
     trigger above) is a good starting text — it already says to stop rather than
     improvise if the file is missing.
   - **Sweep leftover worktrees at the START of each run**, before anything else.
     Decided deliberately: a crashed or killed run must not block the next night.
     Use `dli git worktree-remove`, never `rm -rf`.
2. **`dotfiles/hosts/<mini-hostname>/NN-nightly-gh-issue.sh`** — calls
   `scripts/nightly-gh-issue.sh install`, so it lands on the mini only.
   `hosts/lindev/` is the existing example of that directory's conventions,
   including its `README.md` and test pair.
3. That repo's gate covers shell: `shfmt -i 2 -ci -bn`, ShellCheck via
   `scripts/lint-shell.sh`, and macOS Bash 3.2 as the floor — no associative
   arrays. Give it a test pair like `ups_power_notify.test.sh`.

**The mini's hostname is not recorded here because I never learned it.** Ask, or
read it off the machine; do not guess a directory name.

## Constraints an unattended run has that an interactive one does not

These are the ones that will bite, and none is obvious from the code:

- **Worktrees accumulate.** `AGENTS.md` in dotfiles: the `WorktreeRemove` hook
  "never fires in an unattended run, so `claude -p` sessions and subagents still
  leave worktrees behind." One per night, forever, unless the sweep above runs.
- **`op` cannot work at all** — no session, no desktop app to prompt. If any step
  turns out to need a secret it needs `OP_SERVICE_ACCOUNT_TOKEN`. The current
  pipeline appears not to, but that is inference, not a measurement.
- **Prerequisites on the mini, none of which were verified from the laptop:**
  `gh` installed and authenticated, `claude` authenticated, the
  `workflow-skills` plugin installed, and the repo cloned somewhere the job can
  reach.
- **The mini must be awake at the fire time**, or launchd runs the job late on
  wake. Fine for an always-on box; worth confirming it is one.

## Also open, and deliberately not started

**Give the gh-issue handler a REST channel** so it works in any unattended cloud
caller — this routine, `/do-tasks --remote`, auto-pilot's dispatched sessions,
and the `-n N` batch path. `gh-issue.remote_batch` already defaults to `false`;
the 2026-09-07 record says it stays off "for caution rather than for a reason",
and this is now the reason.

Every refused call has a REST equivalent. The measured/inferred split is in the
research file — respect it, and **measure the writes before trusting them**:
everything probed on 2026-09-21 was a read, and `POST /git/refs` is separately
known to be refused. The 403 body names CCR routes on `api.github.com` for
review threads, auto-merge and draft/ready-for-review, which is a lead nobody
has exercised.

No issue has been filed for this. File one rather than letting it live here.

## The immediate next step

Land PR #819 (runbook + measurements; the gate passed on this branch), then build
the two files above on the mini.

---

**When the work described above is done, delete this file.** It is a handoff,
not a record. If what it says is worth keeping, it belongs in a commit message, a
PR body, a design doc under `dev_docs/designs/`, or the tracker. Move it there
first, then delete this file.
