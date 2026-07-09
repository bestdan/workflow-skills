---
name: deliver-task
description: Take ONE identified task through its per-task delivery lifecycle — claim it, implement it via a routed coder worker, and verify the result — leaving a verified diff on a task branch with evidence captured. Handler-dispatched (repo-pr / linear / gh-issue / jira) like the other task skills, and the per-task unit the /auto-pilot orchestrator calls. Use when the user wants one specific task driven all the way to a reviewed-ready change (e.g. /deliver-task <slug>), not a batch. This is the claim→do half; PR creation, co-review, and hand-off are the review half (added separately).
---

# deliver-task — one task, claimed, implemented, verified

One task in; a **verified diff on a task branch, with evidence captured** out.
`/deliver-task` is the per-task lifecycle primitive: the depth verb (drive _one_
task properly), as opposed to `/do-tasks`, the breadth verb (select _which_ and
_how many_). The `/auto-pilot` orchestrator calls this once per task.

**Scope of this skill:** claim → do. It stops at a clearly named seam —
**"verified diff on the task branch, evidence captured"** — **before** opening a
PR. PR creation, `/co-review`, iterate, and hand-off are the delivery _review
half_ and are out of scope here.

## Compose, never duplicate

Every discrete step delegates to an existing, battle-tested procedure; this skill
adds only the per-task spine that strings them together. It contains **zero
restated claim logic** — the claim is the handler's own protocol, referenced, not
copied.

| Step                                            | Delegates to                                                                                  |
| ----------------------------------------------- | --------------------------------------------------------------------------------------------- |
| Claim                                           | the handler's own claim section (see step 1) — never a bespoke claim                          |
| Route the worker                                | `select-coder` (`skills/select-coder/SKILL.md`), non-interactive                              |
| Run the worker in isolation, integrate the diff | the worktree + integrate rules in `orchestrate-coders` (`skills/orchestrate-coders/SKILL.md`) |
| Base-freshness                                  | `scripts/preflight-freshness.sh`                                                              |
| Exercise the feature                            | the `verify` skill where the change has a runtime surface                                     |

## Arguments

- `<slug>` / `<identifier>` — the one task to deliver (a file slug for `repo-pr`,
  or a tracker id like `PRE-12`). Required — this skill never selects a task; it
  delivers the one named.
- `--base <branch>` — the branch the task's work branch is based on. Default
  `main`. The `/auto-pilot` orchestrator passes a parent task's frozen tip here
  for a **stacked** (dependency-chained) task, so the child builds on the parent.

## 0. Resolve the handler

Read `dev_docs/tasks/.task-config.yml` (absent → `repo-pr`), exactly as
`/do-tasks` does:

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

## 1. Claim (the handler's protocol, verbatim)

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
`--base` (default `main`) instead of the handler's default base — fetch `--base`
first (step 2) so the branch starts from its current tip. Everything else in the
claim section is unchanged.

If the claim reports the task already claimed / in flight / blocked, **stop** and
report it — never double-claim.

## 2. Do (implement + verify)

With the claim held and the work branch checked out:

1. **Fetch + base-freshness.** Before dispatching, make sure the base isn't
   stale: `git fetch` the base, then run the shared fixture on it —
   `scripts/preflight-freshness.sh --ref <base>`. On `stale`, stop and surface it
   (the work branch would start behind); on `unknown`, warn and proceed.
2. **Route the worker.** Invoke `select-coder --non-interactive <task>` (via the
   `Skill` tool) to pick the coder backend/model for this task — the
   non-interactive path never prompts and reads the resolved `.coders.yml`
   (`/auto-pilot`'s launch phase populates it). Route mechanical work cheap,
   judgment-heavy work strong.
3. **Dispatch in isolation, then integrate.** Dispatch the implementation to the
   chosen coder **in its own git worktree on its own branch**, per the
   `orchestrate-coders` dispatch + integrate rules — the worker never edits this
   session's checkout. The orchestrating session then **owns the task branch**:
   read the worker's diff, integrate it onto the task branch, and **clean up the
   worker worktree**.
4. **Verify.** Run the project's named check command (the caller/config names it;
   else detect — `just check`, `scripts/check.*`, or `dli check`) **and exercise
   the feature itself** — drive the changed behavior end-to-end (the `verify`
   skill, where the change has a runtime surface), not just the tests. Capture
   **observable evidence**: the check output, and any screenshots / command
   output / artifact paths. Record those paths — the review half puts them in the
   PR body.

Definition of done for the work (from the design's anti-superficiality rule):
fewer tasks genuinely finished beats all tasks superficially touched; you
exercised the feature itself, not just its tests.

## 3. Seam — stop here

This skill ends at **"verified diff on the task branch, evidence captured."**
Do **not** open a PR, run `/co-review`, or move the tracker to `needs_review` —
those are the delivery _review half_. Report: the task, the work branch, the
verify result, and the evidence paths, so the caller (a human, or the
`/auto-pilot` orchestrator) can carry it into the review half.

If the work can't be completed, use the handler's own **bail** path (repo-pr:
relabel the claim PR `task-blocked`; linear/gh-issue/jira: the release/bail in
their claim files) rather than leaving a half-claim behind.
