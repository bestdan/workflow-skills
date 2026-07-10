---
name: deliver-task
description: Take ONE identified task through its full per-task delivery lifecycle — claim, implement via a routed coder worker, verify, open a PR, run non-interactive co-review, iterate on the findings, and hand off at needs_review (never completed — completion stays merge-verified via /sweep-for-complete). Handler-dispatched (repo-pr / linear / gh-issue / jira) like the other task skills, and the per-task unit the /auto-pilot orchestrator calls. Use when the user wants one specific task driven all the way to a reviewed, hand-off-ready PR (e.g. /deliver-task <slug>), not a batch.
---

# deliver-task — one task, claimed, implemented, reviewed, handed off

One task in; a **reviewed PR handed off at `needs_review`** out.
`/deliver-task` is the per-task lifecycle primitive: the depth verb (drive _one_
task properly), as opposed to `/do-tasks`, the breadth verb (select _which_ and
_how many_). The `/auto-pilot` orchestrator calls this once per task.

**The full lifecycle:** claim → do → open PR → co-review → iterate → hand-off. It
ends at **hand-off** — the tracker at `needs_review` with a linked PR — and
**never at `done`**: completion is merge-verified later by `/sweep-for-complete`,
never by this skill (the `linear-claim.md` hard rule it inherits).

## Compose, never duplicate

Every discrete step delegates to an existing, battle-tested procedure; this skill
adds only the per-task spine that strings them together. It contains **zero
restated claim logic** — the claim is the handler's own protocol, referenced, not
copied.

| Step                                            | Delegates to                                                                                    |
| ----------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| Claim                                           | the handler's own claim section (see step 2) — never a bespoke claim                            |
| Route the worker                                | `select-coder` (`skills/select-coder/SKILL.md`), non-interactive                                |
| Run the worker in isolation, integrate the diff | the worktree + integrate rules in `orchestrate-coders` (`skills/orchestrate-coders/SKILL.md`)   |
| Base-freshness                                  | `scripts/preflight-freshness.sh`                                                                |
| Exercise the feature                            | driving the changed behavior end-to-end, inline (see step 3)                                    |
| Review the PR                                   | `/co-review --non-interactive` (`skills/co-review/SKILL.md`)                                    |
| Open the PR + hand off                          | the handler's own PR-link + review-state sections (see steps 4, 7) — never a bespoke transition |

## Arguments

- `<slug>` / `<identifier>` — the one task to deliver (a file slug for `repo-pr`,
  or a tracker id like `PRE-12`). Required — this skill never selects a task; it
  delivers the one named.
- `--base <branch>` — the branch the task's work branch is based on. Default
  `main`. The `/auto-pilot` orchestrator passes a parent task's frozen tip here
  for a **stacked** (dependency-chained) task, so the child builds on the parent.
- `--questions <path>` — a `QUESTIONS.md` decision log to append deferred
  judgment calls to (step 6). The `/auto-pilot` orchestrator passes its run's
  log; standalone, omit it and the calls ride out in the hand-off summary
  instead (this skill never writes run-state files itself).
- `--handler <h>` — override the §0 config read; one of `repo-pr | linear |
  gh-issue | jira`. Omit to resolve from `.task-config.yml` as today. The
  `/auto-pilot` orchestrator passes its run's effective handler (a plan source
  ⇒ `repo-pr`, per `references/adapters.md`) so the handler is never
  mis-derived from a repo whose `.task-config.yml` default differs from the
  run's source.

## 0. Resolve the handler

If `--handler <h>` was given, it is authoritative — skip the config read and
map `<h>` directly (below). Otherwise read `dev_docs/tasks/.task-config.yml`
(absent → `repo-pr`), exactly as `/do-tasks` does:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- absent / `handler: repo-pr` → `commands/handlers/repo-pr-execute.md`
- `handler: linear` → `commands/handlers/linear-claim.md` (+ `linear-common.md`)
- `handler: gh-issue` → `commands/handlers/gh-issue-claim.md`
- `handler: jira` → `commands/handlers/jira-claim.md`
- any other value → **stop**: "Unknown task handler `<value>` … Run /task-config."

If a relative path doesn't resolve, find it with **Glob**
(`**/commands/handlers/<name>.md`) and Read it.

## 1. Fetch the base (before the claim)

The claim step (below) acquires the work branch, so the base must be fresh
**before** it runs — otherwise the branch is cut from a stale local base. Update
the local base ref **explicitly** (a bare `git fetch` may not move the local
`<base>` branch, which is the ref the freshness check reads), then check it:

```bash
git fetch origin <base>:<base>          # default <base> = main
scripts/preflight-freshness.sh --ref <base>
```

On `stale`, stop and surface it (the work branch would start behind); on
`unknown`, warn and proceed.

## 2. Claim (the handler's protocol, verbatim)

Run the resolved handler's **claim** section for the named task — do not restate
or reinvent it here. The claim is the loop's distributed lock, and each handler's
version has hard-won race handling that must not be forked:

- **repo-pr** → the **Claim protocol** in `repo-pr-execute.md` (pre-claim check →
  acquire the work branch + flip `status: ready → in_progress` → open the draft
  `task-claim` PR that names the slug → reconcile). The lock is that draft PR, not
  the branch name.
- **linear** → "Pre-flight: is work already in flight?" then "Claim the issue" in
  `linear-claim.md` (the token-comment election with the live-window + state-backed
  eligibility filters).
- **gh-issue** / **jira** → "Claim the issue" in the respective handler file
  (read-then-write assign + label/transition).

**`--base`:** when the handler's claim acquires the work branch, base it on
`--base` (default `main`) instead of the handler's default base — step 1 has
already fetched it, so the branch starts from its current tip. Everything else in
the claim section is unchanged.

If the claim reports the task already claimed / in flight / blocked, **stop** and
report it — never double-claim.

## 3. Do (implement + verify)

With the base fetched (step 1), the claim held, and the work branch checked out:

1. **Route the worker.** Read the task's **content** first — the card body
   (`repo-pr`) or the issue description (tracker) — and pass _that_ (not the bare
   slug/id, which carries no signal) to `select-coder --non-interactive` (via the
   `Skill` tool) to pick the coder backend/model. The non-interactive path never
   prompts and reads the resolved `.coders.yml` (`/auto-pilot`'s launch phase
   populates it). Route mechanical work cheap, judgment-heavy work strong.
2. **Dispatch in isolation, then integrate.** Dispatch the implementation to the
   chosen coder **in its own git worktree on its own branch**, per the
   `orchestrate-coders` dispatch + integrate rules — the worker never edits this
   session's checkout. The orchestrating session then **owns the task branch**:
   read the worker's diff, integrate it onto the task branch, and **clean up the
   worker worktree**.
3. **Verify.** Run the project's named check command — the caller/config names it;
   else detect it (a `just check` recipe, an executable `scripts/check.sh` /
   `scripts/check.py`, or `dli check` — locate the actual file before running it,
   don't shell-glob `scripts/check.*`) — **and exercise the feature itself**:
   drive the changed behavior end-to-end, not just the tests. Capture
   **observable evidence**: the check output,
   and any screenshots / command output / artifact paths. Record those paths —
   step 4 puts them in the PR body.

Definition of done for the work (from the design's anti-superficiality rule):
fewer tasks genuinely finished beats all tasks superficially touched; you
exercised the feature itself, not just its tests.

## 4. Open the PR

Open the PR for the task branch, targeting **`--base`** (default `main`) — a
stacked/chained task must point at its parent's branch, not the repo default, or
its diff shows the parent's changes too.

Readiness follows repo **write access**: **ready-for-review** when the viewer can
write, **draft** otherwise. Detect with `gh repo view --json viewerPermission` —
`ADMIN` / `MAINTAIN` / `WRITE` → ready; else draft. (`MAINTAIN` also grants
write, so it must count as ready.) If the permission can't be determined, default
to **draft** — the safe choice.

- On **repo-pr** this is the claim→review-PR **conversion** — relabel the draft
  `task-claim` PR to `task-loop`, fill the body — per `repo-pr-execute.md`; **do
  not open a second PR**. Call `gh pr ready` **only when the viewer can write**
  (the same `ADMIN`/`MAINTAIN`/`WRITE` gate); otherwise leave it a draft and hand
  off in that state — never fail trying to ready a PR you lack permission to. On
  **linear/gh-issue/jira**, open the PR with the handler's own title/link
  conventions (e.g. `[PRE-12]` + the linked issue), draft per the same gate.
- **PR body:** the evidence captured in step 3 (check output, screenshots,
  artifact paths) **plus how-to-evaluate steps** for any human-judgment item —
  exactly how to judge it and what a "no" would invalidate.
- If the repo's review bots won't run on a **draft** PR, record
  `bot review skipped (draft)` rather than waiting — step 5 falls back to the
  reviewers that can run.

## 5. Co-review

Run `/co-review --non-interactive` on the PR (via the `Skill` tool). Its
never-prompt guarantee and bounded per-class timeouts are what make it safe in an
unattended run. **Record which reviewer classes ran / timed-out / skipped** — that
line goes into the hand-off summary (step 7). If `/co-review` can't run **at all**
(every reviewer unavailable, MCP/auth failure), don't bail — the work is already
verified; note `co-review unavailable` and proceed to hand-off (review is
advisory).

## 6. Iterate (bounded)

Each iteration is a full round: apply co-review's **high-confidence** fixes,
re-verify (step 3's check), re-push, then **re-run `/co-review --non-interactive`
on the updated PR** to gather fresh findings. **Judgment calls** (medium findings)
are never applied silently:

- append each to the **caller-provided** `--questions <path>` decision log when
  one is passed (the `/auto-pilot` orchestrator's `QUESTIONS.md`);
- **standalone (no `--questions`)**, carry them in the hand-off summary instead —
  this skill never writes run-state files itself.

**Hard bound: 2 rounds** (two review→fix passes total). Stop early when a round
surfaces no new high-confidence fixes; after the second round, record any
remaining findings and proceed — don't loop.

## 7. Hand off (and freeze)

- **Tracker → `needs_review`** via the handler's own linking/state sections
  (`linear-claim.md` "Move to review on PR open"; on repo-pr the ready
  `task-loop` PR is itself the review signal). **Never `done`/`completed`** —
  completion is merge-verified by `/sweep-for-complete`, per the `linear-claim.md`
  **hard rule** (the execute path never moves an issue to a completed/canceled
  state). This is the skill's terminal success state.
- **Hand-off summary** (structured — for a human, or the `/auto-pilot` morning
  report): task id, PR URL, reviewer classes that ran, outstanding findings (the
  deferred judgment calls), and evidence paths.
- **Freeze rule:** once a task hands off, its PR is **frozen for the rest of a
  run** — late-arriving findings (e.g. a bot review that lands after co-review's
  timeout) are **logged, never applied**. This keeps a stacked child's base
  stable: a parent another task chains onto never moves post-hand-off. Within a
  single `/deliver-task`, step 6 is therefore the only place fixes land; across a
  run, the `/auto-pilot` orchestrator enforces the freeze.

## Bail

If the work can't be completed at any step, use the handler's own **bail** path
(repo-pr: relabel the claim PR `task-blocked`; linear/gh-issue/jira: the
release/bail in their claim files) rather than leaving a half-claim behind.
