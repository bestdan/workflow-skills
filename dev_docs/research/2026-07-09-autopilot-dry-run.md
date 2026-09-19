---
title: Auto-pilot attended dry-run — field notes & workflow optimizations
created: 2026-07-09
status: reference
context: /auto-pilot smoke test on a one-task plan-with-docs source, run attended
audience: a future agent running or improving /auto-pilot
---

# Auto-pilot dry-run — what tripped, and how to make the next run smoother

A record of an **attended** `/auto-pilot` run against a one-task plan source, plus
concrete fixes for the skill and its references. The goal was to smoke-test the
run-phase loop end-to-end; the detached/sandboxed **Step 7** spawn was skipped by
user direction and the loop was executed in-session with normal permission prompts.

- **Command:** `/auto-pilot dev_docs/tasks/agent_feedback_smoke_plan --until 22:45`
- **Source:** plan-with-docs dir (one independent task — a pure stdlib redaction util).
- **Outcome:** 1 task handed-off (PR opened, co-reviewed, `needs_review`), clean
  end-of-run (`list_ready` empty → `status: done`). One real bug caught by co-review.

The loop itself works. The friction was almost entirely in **the seams** — where
the plan source, the handler config, the branch layout, and the `--until` bound
meet the generic machinery. Those are the parts worth hardening.

---

## Top findings (ranked by how much they'd bite an unattended run)

### 1. `--until` can't be met if a task starts too close to it — and nothing guards it

**What happened.** `--until 22:45` was ~40 min after launch. A _single_
`/deliver-task` is: coder dispatch (~7 min here) + verify + PR + co-review with
CLI reviewers bounded at **15 min each** + up to 2 iterate rounds. That trivially
exceeds 40 min. The coder dispatch alone pushed us past 22:45 mid-delivery.

**Why it matters.** A detached orchestrator's outer watchdog **hard-kills** at
`--until`. A task dispatched at 22:40 gets killed at 22:45, leaving a half-built
`claimed`/`implementing` task to `--resume` — the worst state to wake up to.

**Fix.** Add a **pre-dispatch time guard** to the run loop: before claiming the
next task, if `now + min_task_budget > until`, stop the loop cleanly instead of
starting work that can't finish. Surface it: "2 tasks left, 12 min to deadline,
not starting — resume tomorrow." Also **document realistic `--until` sizing** in
the launch phase: a single task needs a floor of ~20–45 min depending on the
co-review reviewer set. `--until now+40min` for anything with cloud reviewers is
under-provisioned.

### 2. Handler resolution ignores the source — plan source + `handler: linear` misroutes

**What happened.** `.task-config.yml` said `handler: linear`, but the source was a
plan dir. `/deliver-task` **step 0 resolves the handler purely from the config**,
so the literal run-loop call `/deliver-task <slug> --base main …` would have
dispatched the **linear** claim protocol against a plan-file slug (`status: ready`
frontmatter, not a `PRE-123` issue) and failed. I overrode to `repo-pr` (the plan
adapter's mandated handler) and logged it as a `QUESTIONS.md` decision.

**Why it matters.** The auto-pilot **plan adapter** explicitly delegates to
`repo-pr-execute.md`, but `/deliver-task` has no way to be _told_ that — it
re-derives the handler from config and will silently pick the wrong one whenever a
repo's default handler (`linear`) differs from the plan-source handler (`repo-pr`).

**Fix.** The auto-pilot launch/run phase should resolve the **effective handler**
(plan source ⇒ `repo-pr`, regardless of `.task-config.yml`) and pass it explicitly:
give `/deliver-task` a `--handler <h>` argument that overrides its step-0 config
read. Without that flag, the "run loop calls `/deliver-task` verbatim" contract in
SKILL.md is broken for any plan source in a linear-configured repo.

### 3. Plan-lives-on-a-feature-branch breaks repo-pr's task-file bookkeeping

**What happened.** The plan/task files were committed on `bestdan/autopilot-smoke`,
**not on `main`**. But repo-pr assumes task files live on `main`: it scans `main`,
flips `status: ready → in_progress` in the file, and **deletes the file in the
code PR** as the "done" signal. Here the task code branches from `main` (where the
`friction/` package belongs) but the task file isn't on `main`, so the code PR
can't carry the status-flip/delete. I decoupled it: the code PR carries only code,
and the plan adapter's `set_needs_review` flips the task-file status on the
**run-state branch** instead.

**Why it matters.** This is the _normal_ shape for auto-pilot — the plan is a
`/plan-with-docs` output on a working branch, and the run-state branch is cut from
it. The repo-pr handler's "task file on main, deleted on merge" model wasn't
written with that in mind.

**Fix.** The **plan adapter** (`references/adapters.md`) should state explicitly
that for an auto-pilot plan source the task-file status transitions
(`ready → in_progress → needs_review`) happen on the **run-state branch**, fully
decoupled from the code PR's diff — the code PR never contains the task file. Right
now the adapter delegates to `repo-pr-execute.md` verbs that assume otherwise.

### 4. Step 7 (detached + sandboxed spawn) has no shipped helper — high-risk to hand-roll

**What happened.** We skipped it by direction, but: the launch step calls for a
`sandbox-exec` seatbelt profile (RW worktrees, RO creds, no-access elsewhere) +
a narrowed network egress allowlist + a **relaunchable** `launchd` supervisor +
an auth smoke-test _through_ the wrapper. There is **no script** for any of this
in `skills/auto-pilot/` (only `references/*.md` prose). An agent must author a
`bypassPermissions`-jailing profile from scratch, correctly, in one shot.

**Fix.** Ship `skills/auto-pilot/scripts/spawn-orchestrator.sh` (+ a `.sb`
template and a `launchd` plist template) that materializes the profile from the
resolved coder/network allowlist. Getting the jail wrong on a detached
`bypassPermissions` process is exactly the failure mode the fail-closed pre-flight
exists to prevent — so the safe construction should be code, not left to prose.
Also: step 2 references `scripts/probe-coders.sh` as the "single source of truth"
for coder auth probes — confirm it actually ships in the auto-pilot skill dir
(only `scripts/preflight-freshness.sh` was present on the path I used).

### 5. Pre-flight probes vs. state-materializing side effects should be clearly separated

**What happened.** I ran the supply-side probes (steps 2–6: auth, fingerprint,
verify tooling, graph scout) **before** creating any worktree/branch/run-state,
then paused to ask the user a question. The user's follow-up assumed the run-state
branch + `RUN.md` already existed ("use them"). They didn't — I'd stopped before
Step 1's actual creation. Minor confusion, easily avoided.

**Fix.** Make the launch phase state which steps are **read-only probes** and which
**create durable state**, and if the agent pauses to ask a human anything, have it
say explicitly what has and hasn't been materialized yet. Ordering suggestion:
probe everything read-only first (fail-closed on supply gaps), then do all the
state-creating steps (worktree, branch, `RUN.md`) as one committed unit.

---

## Smaller notes

- **Co-review earns its keep.** The worker delivered "green" (19 tests, full
  `check.py` passing). Independent co-review still found a **real privacy leak**:
  `$-50,000.00` (a sign _between_ `$` and the digits) escaped the money regex, and
  the comma-broken digit groups escaped the `\d{4,}` scrubber, leaving the raw
  figure visible — in a _redaction_ utility. The reconciler empirically verified it
  (`scrub_text('You owe $-50,000.00 today')` returned unchanged). Lesson: keep the
  adversarial reviewer pass even when the worker's own tests are green; the worker
  tests only what it thought of.

- **Cloud reviewer latency dominates short runs.** `codex` is fast (~1–2 min,
  stateless, sandboxed, no auth hang). `devin`/`agy` are cloud and slow; the
  `--non-interactive` bound is 15 min _each_. For a time-boxed run, resolve a
  **fast reviewer set** (codex + Claude + reconciler) and treat cloud reviewers as
  optional/skippable — a skipped reviewer is never fatal, and the report records it.

- **`codex exec` prints a harmless stderr line.** Observed:
  `ERROR rmcp::transport::worker: worker quit … Auth(AuthorizationRequired)`. This
  is codex's MCP transport, **not** a review failure — the review verdict still
  followed. Don't classify a reviewer as failed on stderr noise; look for the
  actual findings / a `tokens used` marker.

- **`RUN.md` has no phase for a materialized-but-unclaimed task.** The seven
  lifecycle phases start at `claimed`. I used an informal `ready` in the initial
  table row. Either define a pre-claim marker in `references/run-state.md`, or state
  that the table lists only in-flight tasks and the graph readiness is computed
  separately.

- **`QUESTIONS.md` is a genuinely good primitive.** Logging each reversible
  orchestrator decision (handler override, worktree-isolation simplification, base
  choice, co-review disposition) with options/call/why/reversibility made the run
  auditable without blocking. Keep it; it's the right shape.

- **Independent verify caught nothing but is still worth it.** Re-running the
  worker's tests + acceptance assertions + lint/types myself (rather than trusting
  the worker's report) cost ~30s and is cheap insurance. The value showed up one
  step later in co-review, not here — but the habit is correct.

---

## A clean sequence for the next attended dry-run

If a future agent runs `/auto-pilot <plan-dir>` attended (Step 7 skipped), this
ordering avoids the friction above:

1. **Probe read-only first** (fail-closed): git state + plan committed?, `gh auth`,
   coder binaries on `PATH`, verify command (`scripts/check.py`), base freshness,
   `gh repo view --json viewerPermission` (ready-vs-draft). No side effects yet.
2. **Resolve the effective handler from the _source_, not the config** — plan dir
   ⇒ `repo-pr`. Log the override if `.task-config.yml` disagrees.
3. **Materialize state as one unit:** create the run-state worktree + branch
   (`auto-pilot/<run_id>`), write + commit `RUN.md`/`QUESTIONS.md`/`REPORT.md`.
4. **Guard the deadline:** if `now + ~30 min > --until`, don't start; say so.
5. **Per task:** claim (draft `task-claim` PR is the lock) → coder in worktree →
   **verify independently** → convert claim PR to `task-loop` + ready → co-review
   `--non-interactive` (fast reviewer set) → apply high-confidence fixes only,
   defer mediums to `QUESTIONS.md`, skip lows → iterate ≤2 rounds, stop on
   no-new-high → hand off (`set_needs_review` on the run-state branch; freeze PR).
6. **Re-check readiness** → empty → `status: done`, rewrite `REPORT.md`, stop.
7. **Commit run state after every phase** in write order (push code → tracker →
   run-state commit). Five bookkeeping commits for a one-task run is normal.

Everything the run produces is a **test artifact** — the PR is closed after review,
and the worktrees/branches are torn down once inspected.
