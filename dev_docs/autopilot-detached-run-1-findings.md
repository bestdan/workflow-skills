---
title: Auto-pilot first detached run — issues & improvement backlog
created: 2026-07-10
status: reference
context: /auto-pilot linear-fastpath-p2, first FULLY DETACHED + sandboxed run (Step 7 live)
audience: a future agent hardening /auto-pilot
related: ./autopilot-dry-run.md (the earlier attended dry-run; Step 7 skipped)
---

# Auto-pilot detached run #1 — everything that tripped, and how to fix it

First real **detached + sandboxed** auto-pilot run (the attended dry-run in
`autopilot-dry-run.md` skipped the Step-7 spawn). It **succeeded** — 6/6 tasks
handed off in ~1h05m, 0 parked, PRs #162–#167, the human merged #162–#165 mid-run
and the freeze rule held. But getting there required hand-patching three fatal
spawn bugs and making five launch-time judgment calls, and the run surfaced a
genuine conflict between the sandbox and the project's own verify contract. This
is the ranked backlog.

- **Command:** `/auto-pilot dev_docs/tasks/linear_fastpath_p2_plan --until now+4h`
- **Source:** plan-with-docs (repo-pr), 6 tasks, chain `1→2→{3,4}`, `5` indep, `6` last
- **Env:** `local-full`, macOS (Mac mini), coders opus+codex, fast reviewer set

---

## P0 — Fatal spawn bugs (the run is dead-on-arrival without these)

All three live in `scripts/spawn-orchestrator.sh` (+ its templates). Each one, left
unpatched, kills the detached orchestrator instantly or makes every tool call fail.
I patched all three by hand at launch; they must be fixed in the generator so the
`launch` subcommand works end-to-end.

### 1. `write-launch` omits `--verbose` → stream-json aborts on the first line
`claude -p --output-format stream-json` **requires `--verbose`** ("When using
--print, --output-format=stream-json requires --verbose"). The generated launch
script has no `--verbose`, so the detached process exits before doing anything.
The `smoke-test` subcommand does **not** catch this — it runs `claude -p 'ok'`
*without* `--output-format`, so it green-lights an invocation shape the real run
never uses.
**Fix:** add `--verbose` to `write_launch`'s emitted command; make `smoke-test`
run the *exact* flag set the launch script uses (verbose + stream-json), not a
reduced one.

### 2. No `PATH` injection → launchd's minimal PATH hides the whole toolchain
A `launchd` job runs with `PATH=/usr/bin:/bin:/usr/sbin:/sbin`. `gh`
(`/opt/homebrew/bin`), `claude`/`codex`/`uv` (`~/.local/bin`), `node` (`~/.nvm`)
are all invisible — every `gh pr`, `codex exec`, `uv run` fails
command-not-found. The generated launch script sets only `AUTO_PILOT_UNTIL`.
**Fix:** `write-launch` should take a `--path` (or derive one from the resolved
coder/tool binaries) and `export PATH=…` in the launch script. The pre-flight
already resolves every binary's absolute path (§2 fingerprint) — feed those dirs in.

### 3. Exec allowlist can't cover a real toolchain (and drifts on tool updates)
Seatbelt `process-exec` is strictly allowlisted (verified: unlisted `sed`/`git`/
`head` are denied). The generator emits only `(literal <resolved-bin>)` for the
handful of `--exec` args, but the real workload shells out to **dozens** of
coreutils, `git`-core helpers, `uv`→python, `node`, and `codex`→`sandbox-exec`.
Two compounding traps:
- **Symlink/version drift:** `claude` → `~/.local/share/claude/versions/2.1.207`,
  `codex` → `~/.codex/packages/standalone/current/bin/codex`. `render-profile`
  canonicalizes `--exec` (good), but a version-pinned literal breaks after the
  next `claude update`, and a caller passing bin *dirs* (as I did) must know to
  add `~/.local/share/claude` + `~/.codex`, which is non-obvious.
- **Unusable as-shipped:** with only the literal list, `bash scripts/check.sh`
  and the coders can't run at all.
**Fix:** give the profile a **toolchain-exec mode** — permit `(subpath …)` over
the resolved bin dirs (`/bin /usr/bin /usr/sbin /usr/libexec /opt/homebrew
~/.local ~/.codex ~/.nvm` here), or maintain a resolved+version-agnostic exec set.
Keep the two walls that actually contain the agent (write-confinement +
network-egress) tight; treat exec breadth as the coarse guard (reads are already
broad in the template for exactly this reason). I verified the broadened profile
still DENIES writes to `$HOME`/`/etc`/sibling `~/src` repos and off-allowlist egress.

---

## P1 — Sandbox vs. the project's own contracts (design conflicts)

### 4. The jail defeats `check.sh` — verify can't reach "definition of done" in-jail
`scripts/check.sh` runs `bash scripts/test-*.sh`; under the two-layer jail those
harnesses fail on `execve` of `#!/usr/bin/env bash` scripts-under-test
(`bad interpreter: Operation not permitted`, exit 126) **regardless of the diff** —
they fail identically on pristine `main`. So the run's stated verify command
(`bash scripts/check.sh`, pre-flight §5) **cannot pass inside the sandbox**. The
orchestrator degraded gracefully (Q6: ran the content gates that execute —
`dprint check`, `claude plugin validate --strict`, `uv run validate.py` — and
classified harness failures as environmental, confirming no failing harness
referenced the changed files), and every PR body flags "re-run check.sh outside
the jail." That's the right call, but the human must remember it.
**Fix options (pick one, document it):** (a) run the verify command **outside**
the jail (a separate, un-sandboxed verify step the orchestrator shells to); (b)
teach the jail a "repo scripts are exec-allowed" grant so `check.sh` completes;
(c) formally split the verify command into jail-runnable content gates vs.
out-of-jail harness gates in `RUN.md`, so the degradation is declared, not
discovered. (a) or (b) is cleaner than the current per-task re-derivation.

### 5. Credential stores can't be read-only — tools write their own state
`launch-runtime.md` §3 mounts `~/.claude`/`~/.config/gh`/`~/.codex` **read-only**.
But a long orchestrator + its coders **write** to `~/.claude` (sessions/todos),
`~/.codex` (rollouts), `~/.cache` (uv/dprint), `~/.config`. RO there breaks a real
run. I made them RW (Q1).
**Fix:** separate the **credential file** (RO — the token) from the **tool state
dir** (RW). Where a tool co-locates both (codex under `~/.codex`), the design's
per-worker "credential-subtractive" model (§4) needs to actually be implemented,
or accept RW state dirs as the v1 posture and say so.

### 6. Nested `sandbox-exec` doesn't compose
`codex --sandbox workspace-write` and `scripts/test-spawn-orchestrator.sh` /
`smoke-confinement.sh` themselves invoke `sandbox-exec` **inside** the
orchestrator's seatbelt. Nesting doesn't compose cleanly — part of why #4's
harnesses die. `launch-runtime.md` §4 chose "one profile over the whole process
tree" precisely to avoid nesting; the reality that *coders and tests* also nest
sandboxes needs an explicit stance (allow-listed re-exec, or run those out of jail).

---

## P2 — Launch-phase ergonomics & doc gaps

### 7. No end-to-end pre-flight helper — the launch is ~20 manual steps
I ran the supply-side probes ad hoc: `gh auth status`, `probe-coders.sh`,
`preflight-freshness.sh`, `gh repo view` (draft-vs-ready), binary fingerprint,
base FF. SKILL §2 itself says "good candidates to extract into a small pre-flight
helper" — still not done. An agent hand-orchestrating this is error-prone.
**Fix:** ship `scripts/preflight.sh` that runs all read-only probes, emits the
environment fingerprint + a go/no-go with specific blockers, and the resolved
paths that write-launch/render-profile consume.

### 8. The atomic `launch` subcommand is unusable the moment you need P0 fixes
`spawn-orchestrator.sh launch` does write-launch→smoke→detach→record in one shot —
but I couldn't use it, because P0 #1/#2 require editing the launch script
**between** write-launch and detach. Fold the `--verbose`/`--path` fixes into
`write-launch` and the atomic `launch` becomes usable again (its safe ordering is
otherwise exactly right).

### 9. Helper-script location is ambiguous in the SKILL
SKILL/references cite `scripts/probe-coders.sh` and `scripts/claude-usage.sh` as if
under `skills/auto-pilot/`; they actually live at **repo-root** `scripts/`. The
dry-run doc already flagged "confirm probe-coders ships in the skill dir." Pick one
location and make every reference consistent.

### 10. Base freshness isn't automated — I had to fetch + fast-forward `main` by hand
Local `main` was 2 commits stale vs `origin/main`; tasks branch from `main`, so a
stale base would have opened PRs against an outdated main. Not in the fail-closed
pre-flight (the dry-run "clean sequence" lists it as a probe, but there's no step).
**Fix:** add a base-freshness step (fetch + FF-if-clean, or block on divergence)
to launch, using `preflight-freshness.sh` which already exists.

### 11. `RUN.md` still has no phase for a materialized-but-unclaimed task
The seven lifecycle phases start at `claimed`; I used an informal `pending`
(dry-run flagged this too). Either add a `pending`/`ready` pre-claim marker to
`run-state.md`, or state that the table's `phase` is only in-flight and graph
readiness is computed separately.

---

## P3 — Plan-source (repo-pr) adapter friction

### 12. Reservation `task-claim` PR doesn't fit a single-orchestrator plan run (Q5)
repo-pr's claim opens a draft PR seeded by an **on-branch task-file status flip** —
but a plan-source code branch bases on `main`, where the task file doesn't exist
(adapter decoupling), so there's nothing to seed it, and one serialized
orchestrator has no race to arbitrate. The orchestrator correctly skipped it and
made the run-state branch the lock — but it had to *derive* that.
**Fix:** the plan adapter should state outright: no reservation PR for a
single-orchestrator plan run; the run-state branch is the lock; still do the
pre-claim `gh` PR scan for resume idempotency.

### 13. `task_6`'s "delete the plan folder" is impossible from a `main`-based PR
The plan scaffolding (`dev_docs/tasks/…_plan/`) was force-added on the working/
run-state branch and never existed on `main`; a code PR branched from `main` can't
delete it. Flagged at launch, confirmed by the orchestrator. The plan-lifecycle
"clean up scaffolding" task assumes the file is on `main`.
**Fix:** make scaffolding cleanup a **run-level teardown** step (on the run-state/
working branch), not a task in the graph.

### 14. Human-merges-mid-run vs. stacked bases needs explicit guidance
The human merged #162–#165 while #164/#165 were still stacked on task_2's branch.
It worked because they merged **bottom-up in dependency order**, so each child's
base content was already in `main`. The freeze/`base_sha` park logic guards the
*orchestrator* moving a base, not a *human* merging one.
**Fix:** the run should emit explicit "merge bottom-up, in this order" guidance
(REPORT already lists PRs; add the ordering + a one-line why), and `run-state.md`
should note the human-merge interaction with stacked bases.

---

## P4 — Orchestrator-loop & co-review observations (worked, with notes)

### 15. Deferred co-review mediums are cross-cutting spec issues, not per-task fixes
Q7 (unbounded `attachments` sub-connection), Q8 (`duplicateOf` direction), Q9
(unbounded nested connections) were all correctly **deferred** — because the
faithful fix spans **every sibling script + the canonical spec** in lockstep, not
one task's diff. Right call, but such findings risk being lost in `QUESTIONS.md`.
**Fix:** let auto-pilot optionally route a "cross-cutting follow-up" into a real
tracked task (`/add-task`) instead of only a decision-log entry.

### 16. The 2-round co-review bound can leave a real-but-unaddressed finding (Q10)
task_5's round-2 finding (smoke-test assertions vacuous on an empty scope) was
recorded-and-proceeded per the bound. Fine — but it's exactly the kind of thing a
follow-up task should carry, per #15.

---

## P5 — Observability

### 17. Live status requires hand-rolled log parsing
The orchestrator log is stream-json only; I wrote ad-hoc `python -c` to extract
phases/events for each 10-min update. `REPORT.md` is good for outcomes but lags
per-event.
**Fix:** a `spawn-orchestrator.sh status --label` that prints phase table +
last event + run-level status + PID liveness in one call; and/or a predictable
"done" sentinel so a watcher needn't poll `ps` + parse `RUN.md`.

### 18. A project `Stop` hook errors every turn under the jail (benign, noisy)
`stop-hook-error` fired on every turn-stop (a project Stop hook not jail-
compatible). Non-fatal (final result was `success`) but noise in the log.
**Fix:** the detached launch should neutralize/skip incompatible Stop hooks (or
run with a minimal settings hook set), and note it.

---

## What worked — preserve these

- **`QUESTIONS.md` decision log** — every reversible call (options/call/why/
  reversibility) made the unattended run fully auditable. Q1–Q10 tell the whole story.
- **Fast reviewer set** (codex + Claude + reconciler) → ~13 min/task, ~1h05m total,
  ~2h40m under the deadline. Cloud reviewers would have dominated the window.
- **Stacking + freeze rule** held under concurrent human merges (bottom-up).
- **Independent verify + adversarial codex caught real bugs** the worker missed:
  task_4 fast-path-vs-floor divergence, task_5 `estimate` parity gap (pre-commit),
  task_5 enum-drift (high, applied). Keep the adversarial pass even on green workers.
- **Rate-window sampling** (24→31→34→46→59% consumed) stayed in the free window;
  no pause, no circuit-breaker, no paid dispatch.
- **The two containment walls that matter** (write-confinement, network-egress)
  were empirically verified at launch and held — the P0/P1 loosening was exec +
  tool-state only.
