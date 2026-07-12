#!/usr/bin/env bash
# spawn-orchestrator.sh — materialize the auto-pilot detached orchestrator's
# launch wrapper and its two-layer jail from a run's resolved inputs, so an
# agent never hand-authors a sandbox-exec profile at launch (PRE-484).
#
# The jail is two layers (skills/auto-pilot/references/launch-runtime.md
# "Sandbox profile"):
#   1. filesystem + process/exec  → this script's Seatbelt profile (below)
#   2. host-level network egress  → the detached `claude -p`'s own
#      sandbox.network allowlist (added by a later task)
#
# Ships the layer-1 profile renderer + compile check (render-profile /
# check-profile) and the layer-2 egress-allowlist emitter (render-settings).
# Subcommands added by sibling tasks: write-launch, detach, teardown.
#
# Verify broker (task 4 — run the run's verify_command OUTSIDE the jail): the
# jailed orchestrator can't spawn an un-jailed verifier (children inherit the
# seatbelt profile) and `bash scripts/check.sh` execve-denies in-jail (finding
# #4). So write-verify-broker installs a SEPARATE, un-jailed launchd job that
# polls a sentinel dir; the orchestrator hands off with verify-request and reads
# the outcome with verify-await. The broker runs a COMMAND-PINNED verify only,
# in a worktree confined to the run root — see the "Task 4" block below.
#
# Usage:
#   spawn-orchestrator.sh render-profile \
#       --rw <path> [--rw <path> ...] \
#       --ro <path> [--ro <path> ...] \
#       --cred-ro <file> [--cred-ro <file> ...] \
#       --exec <path> [--exec <path> ...] \
#       --exec-dir <dir> [--exec-dir <dir> ...] [--toolchain] \
#       --out <file> [--template <file>]
#   spawn-orchestrator.sh render-settings \
#       --source <linear|plan> \
#       [--coder <codex|devin|agy> ...] [--agy-host <host>] \
#       [--mcp-host <host> ...] [--add-task-host <host> ...] [--npm] \
#       --out <file>
#   spawn-orchestrator.sh check-profile <file>
#   spawn-orchestrator.sh status --label <label> [--dir <run-dir>] [--task-ceiling <s>]
#   spawn-orchestrator.sh teardown --label <label> [--done-sentinel <path>] [--reason <r>]
#   spawn-orchestrator.sh exit-reason --dir <run-dir> --reason <r> [--label <label>] [--detail <text>]
#   spawn-orchestrator.sh clear-exit-state --dir <run-dir>
#   spawn-orchestrator.sh heartbeat --dir <run-dir> [--note <text>]
#   spawn-orchestrator.sh assert-run-head --dir <run-worktree> --run-id <run_id> \
#       [--questions <QUESTIONS.md path>]
#
#   assert-run-head  The run-loop / --resume HEAD guard (PRE-hardening task 13):
#           asserts the run worktree's HEAD is `auto-pilot/<run_id>`; if not,
#           restores it (`git checkout`) and, with --questions, appends a
#           QUESTIONS.md entry recording the deviation (which then reaches
#           REPORT.md via the existing rolling rewrite). Fail-closed only if
#           the restore itself fails (e.g. uncommitted changes block checkout).
#   spawn-orchestrator.sh classify-exit --exit-code <n> --output <file> \
#       [--since-offset <bytes>]
#   spawn-orchestrator.sh supervisor-check --exit-code <n> --log <file> \
#       [--wake-start <epoch>] [--since-offset <bytes>] \
#       --dir <run-dir> --label <label> --state <file> [--no-progress-limit <n>] \
#       [--park-limit <n>]
#   spawn-orchestrator.sh supervisor-gate --dir <run-dir> --label <label>
#   spawn-orchestrator.sh supervisor-scan --dir <run-dir> --label <label> \
#       [--park-limit <n>]
#   spawn-orchestrator.sh alarm --dir <run-dir> [--label <label>] \
#       --condition <id> --reason <text> [--action <text>]
#   spawn-orchestrator.sh alarm-request --dir <run-dir> \
#       --condition <id> --reason <text> [--action <text>]
#   spawn-orchestrator.sh alarm-clear --dir <run-dir>
#
#   alarm      Task 16 — the run must ACTIVELY tell a human. Emits an OS-level
#              notification (osascript, falling back to terminal-notifier),
#              writes the `.auto-pilot/ALARM` sentinel, and prepends a one-line
#              reason + REQUIRED ACTION to the very top of REPORT.md. Idempotent
#              per <id> per run (the sentinel is the key), so a 300s relaunch
#              cadence can't turn the alarm into the new noise. Pure shell — a
#              rate-limited or auth-dead run cannot make a model call to alert
#              anyone. MUST run from the UN-JAILED supervisor: the jail
#              exec-denies osascript/open/launchctl, so an alarm raised inside
#              the agent is silently denied (the very failure this prevents).
#              The notification never fails the caller: a missing/denied notifier
#              still leaves the sentinel + the REPORT.md line.
#   alarm-request  The JAILED side's seam: an in-agent detector (the invariant
#              doctor) records a condition it cannot itself deliver; the
#              supervisor drains and delivers it on its next wake.
#   alarm-clear  Retire this run's alarms (the sentinel + any undelivered
#              requests). `--resume` calls it first: the sentinel is the
#              idempotency key, so one that outlives the resume would SUPPRESS
#              the alarm when the same condition recurs. REPORT.md's history stays.
#
#   classify-exit  Read-only, no model call (task 10 / finding #22): classify
#                  an orchestrator exit from its exit code + captured
#                  stdout+stderr. Prints exactly one of `done` (exit 0),
#                  `fatal: <reason>` (a non-retryable auth failure —
#                  authentication_failed / an expired-OAuth message / a 401 in
#                  an auth CONTEXT, never a bare `401` substring, which occurs
#                  incidentally in any transcript), or `retry: <reason>` (a
#                  rate-limit signal, or any other non-zero exit). Always exits
#                  0 — the classification is the payload, not a pass/fail
#                  signal. `--since-offset` bounds the read to the bytes THIS
#                  wake appended to the shared log, so a past wake's auth
#                  failure can't keep halting a run that has since been
#                  re-authenticated.
#   exit-reason    The EXIT CONTRACT (task 15). The orchestrator DECLARES why it
#                  is exiting, on the run-state branch, before it exits — one of
#                  `continuing` (work remains, context exhausted → relaunch me),
#                  `paused` (rate window / paused_until → relaunch me past the
#                  reset), `done` (no ready tasks → tear down), `systemic`
#                  (circuit breaker / fatal auth / failed invariant → tear down +
#                  alarm), `deadline` (the pre-dispatch guard stopped with tasks
#                  still ready → tear down; resume only by an explicit --resume).
#                  Without it, "I finished the run" and "I ran out of context
#                  mid-task" are the SAME observable event (exit 0) and the
#                  supervisor can only relaunch on a blind timer. Writes
#                  `exit_reason` + `exit_reason_at` (epoch) to RUN.md's front
#                  matter and commits them to the run-state branch; for the three
#                  TERMINAL reasons it also writes the done-sentinel — the SAME
#                  file `teardown --done-sentinel` writes and `status` reads,
#                  never a second marker — carrying the reason. It never calls
#                  launchctl: the orchestrator is jailed and cannot; the
#                  supervisor (below), which runs outside the jail, does the
#                  actual bootout after reading the declared reason. A NON-terminal
#                  reason also REMOVES any pre-existing done-sentinel: a live run
#                  declaring "relaunch me" must not leave "the run is over" on disk.
#   clear-exit-state  The inverse, and `--resume`'s first act: remove the
#                  done-sentinel and blank `exit_reason`/`exit_reason_at`/
#                  `exit_reason_detail` on the run-state branch. A terminal reason is
#                  DURABLE (committed) and `deadline` is BY DEFINITION the one whose
#                  recovery is an explicit --resume — so without this, a resumed run
#                  carries a "the run is over" marker into its first wake: `status`
#                  reports `relaunch=no`, a PathState watcher treats it as complete,
#                  and the supervisor can tear it down on its first wake.
#   heartbeat      Touch <dir>/.auto-pilot/heartbeat (epoch + ISO + a note). The
#                  orchestrator touches it at each loop iteration and each
#                  /deliver-task sub-step boundary, and the launch wrapper touches
#                  it at the top of every wake. That is the ONE signal that
#                  separates *slow* from *wedged*: `status` compares its age to
#                  the per-task ceiling ("last heartbeat 40m ago, ceiling 45m").
#                  Deliberately NOT committed to the run-state branch — it beats
#                  many times per task and would drown the run's durable record in
#                  churn; it is wake-local liveness, like `supervisor-state`.
#   supervisor-check  The per-wake decision the generated launch script calls
#                  AFTER `claude -p` exits. It READS THE DECLARED EXIT REASON (in
#                  shell, no model call) and acts on it — relaunch on
#                  `continuing`/`paused`, tear down on `done`/`deadline`, halt +
#                  alarm on `systemic` — instead of relaunching on a blind timer.
#                  The agent DECLARING beats the supervisor INFERRING; inference
#                  (classify-exit, below) remains the fallback for a wake that
#                  declared nothing (a hard-killed agent, a pre-task-15 prompt).
#                  Fail-SAFE: an unknown/missing/garbage reason never tears a live
#                  run down — it falls back to inference. A declaration is only
#                  honored when it is THIS wake's (`exit_reason_at` >=
#                  --wake-start), so a previous wake's `done` can't tear down a
#                  live run and a previous wake's `continuing` can't out-vote a
#                  fatal auth exit. And a `fatal` classification halts regardless
#                  of what was declared (over-halting is the safe direction; that
#                  is finding #22's whole lesson). A missing/garbage --wake-start
#                  (a launch.sh generated before it existed, an unreachable `date`)
#                  DEGRADES to "honor no declaration" — it never disables the
#                  supervisor: classification, the no-progress guard, the halt and
#                  the teardown all still run. A supervisor that silently stops
#                  supervising is the worst outcome available to this system.
#                  Classifies the exit (above); a
#                  `fatal` classification halts immediately. A `retry`
#                  classification is checked against the no-progress guard:
#                  if the run-state branch HEAD (under --dir) hasn't moved
#                  across --no-progress-limit (default 3) consecutive
#                  non-zero wakes, it halts too — the general backstop that
#                  would have caught #22 even without matching the 401 string.
#                  A wake that lands while RUN.md's `status:` is `paused`
#                  never counts against the guard (a paused wake makes no
#                  progress by design — see run-budget.md "Two pause kinds").
#                  A halt writes run-level `status: systemic` + `pause_reason`
#                  to RUN.md, appends one alarm entry to REPORT.md, RAISES THE
#                  ALARM (below), commits both to the run-state branch, and tears
#                  the supervisor job down (`launchctl bootout`) so it never
#                  relaunches into the same condition. Exits with the classified
#                  exit code (0 for `done`) so the launch script's own exit is
#                  meaningful.
#                  Before classifying anything it ALSO scans (task 16) for the
#                  alarm conditions the supervisor can see on its own: a pending
#                  in-jail `alarm-request` (a failed invariant), the agent's own
#                  `status: systemic` circuit-breaker terminal, a blown `--until`
#                  deadline, and a park storm (≥ --park-limit, default 3, parked
#                  tasks). The first three halt; a park storm reports.
#   supervisor-gate  The pre-invoke gate the generated launch script calls
#                  BEFORE `claude -p` runs at all (task 11 / finding #19): a
#                  pure `date`/string comparison against RUN.md's
#                  `paused_until`, so a wake that lands mid-pause costs no
#                  model call. A run-level `status: done`/`systemic` tears
#                  the launchd job down (reusing `teardown`) instead of
#                  relaunching. Exits 20 to mean "gate closed, do not invoke
#                  the agent"; any other exit means proceed — fail-safe
#                  against a missing RUN.md or an unparseable paused_until.
#                  It gates the AGENT INVOCATION only — see supervisor-scan.
#   supervisor-scan  The supervisor's own per-wake bookkeeping, called by the
#                  generated launch script ABOVE the gate, so it runs on EVERY
#                  wake — including the ones the gate closes. Runs the task-16
#                  alarm scan (the same one supervisor-check runs after the
#                  agent exits) and halts on a terminal condition. Without it,
#                  the gate's `exit 0` would swallow the alarm on exactly the
#                  wakes that prove the run is stuck: an unannounced
#                  `status: systemic` gets torn down in silence, and a blown
#                  `--until` or a park storm waits out a multi-hour pause.
#                  Always exits 0 — bookkeeping, never a gate.
#   spawn-orchestrator.sh restack --run-dir <dir> --verify-cmd <cmd> \
#       [--repo <path>] [--remote <name>] [--gh <path>] [--dry-run]
#
#   restack  Post-merge restack of stacked PRs (finding #25): reads RUN.md's
#            task table (base/base_sha/pr per task), and for every chained task
#            whose parent PR has merged, does the four-command incantation a
#            human must otherwise remember at the right moment — fetch,
#            `rebase --onto <remote>/<base_branch> <base_sha> <branch>`,
#            `push --force-with-lease`, `gh pr edit --base <base_branch>` —
#            in dependency order, idempotent (a PR already based on
#            base_branch is a no-op), fail-closed on a rebase conflict (aborts,
#            reports, never force-pushes). Also flags orphaned children (a PR
#            whose live base is a deleted or already-merged branch) as a
#            defect, whether or not this pass could fix them, and appends every
#            outcome to the run's REPORT.md.
#            Every rebase runs in a THROWAWAY worktree: restack never moves the
#            caller's HEAD (finding #23 / task 13's run-HEAD invariant), and
#            asserts that on exit. `--gh` is mockable — pass a local stub so
#            the offline test suite never calls GitHub while the git mechanics
#            run against a real repo.
#
#            Task 21 — a clean rebase is NOT evidence of correctness, so every
#            successfully-restacked child is now a RE-VERIFICATION TRIGGER, not
#            just a git operation: (1) `--verify-cmd` (required — the run's own
#            `verify_command`) is RE-RUN against the restacked tip before it is
#            trusted; a failure PARKS the child (`_set_task_phase` → `parked`)
#            and fires the task-16 alarm channel, never silently keeping a PR
#            that no longer builds on the new base. (2) For an `onto-base`
#            restack, the parent's post-hand-off review delta is fetched from
#            GitHub — `gh pr view <parent#> --json headRefOid`, cross-checked
#            against `refs/pull/<parent#>/head` (fetchable even after a
#            squash-merge deletes the parent branch) — and line-audited against
#            the child's post-rebase tree: any review-added line the rebase
#            dropped is a DEFECT (parks + alarms), not a warning. An
#            UNDETERMINED gh read (network/auth, distinct from a positive
#            NOT_FOUND) also parks rather than silently skipping the audit.
#            (3) When the parent's review touched a file the child also
#            touches, the child's co-review is marked STALE
#            (`.auto-pilot/co-review-stale/<task>`) and its phase moves
#            `handed-off` → `iterating` — see `co-review-stale-check` /
#            `co-review-stale-clear` below and run-state.md "Restack" for the
#            legal transition back out of `handed-off`.
#
#   spawn-orchestrator.sh co-review-stale-check --run-dir <dir> --task <task>
#
#   co-review-stale-check  Read-only gate a hand-off step calls before writing
#            `needs_review`: exit 0 (clear) when no stale marker exists for
#            `--task`, exit 3 (BLOCKED) when `restack` marked it STALE and it
#            has not yet been cleared. Never mutates anything.
#
#   spawn-orchestrator.sh co-review-stale-clear --run-dir <dir> --task <task> \
#       [--questions <path>]
#
#   co-review-stale-clear  Removes `--task`'s stale marker and moves its phase
#            back to `handed-off` — called after `/co-review --non-interactive`
#            has been RE-RUN on the restacked child and passed. `--questions`,
#            if passed, appends a QUESTIONS.md entry recording the re-review.
#
#   spawn-orchestrator.sh doctor --dir <run worktree> --run-id <run_id> \
#       [--label <launchd label>] [--questions <path>] \
#       [--handler repo-pr|linear] [--gh <path>] [--no-progress-limit <n>] \
#       [--context loop|resume]
#
#   doctor  The run doctor (task 14, generalizing findings #22/#23): a cheap,
#           deterministic, no-model-call invariant audit run at the top of
#           every run-loop iteration and at the top of --resume. Seven
#           invariants, each with a stated repair or a halt — see the
#           "Task 14" block above doctor()'s definition, and
#           skills/auto-pilot/references/run-state.md ("Run doctor") for the
#           full table. Exit codes a caller GATES the run loop on: 0 = every
#           invariant holds or was repaired/parked (the loop may proceed);
#           30 = HALT — an invariant demanded `status: systemic` (the loop
#           must NOT dispatch); 2 = bad usage / an unrepairable fail-closed
#           condition. `--label` is optional (a bare --resume may have no
#           launchd job registered yet); omitting it still halts (writes
#           `status: systemic` + the REPORT.md alarm) but skips teardown.
#           `--gh` is mockable like restack's, for a fully offline test suite.
#           `--context loop|resume` (default loop) tells invariant 7 whether
#           this call is the top-of-loop iteration or the top of --resume: a
#           resume RESETS the no-progress counter instead of incrementing it
#           (a resume is a fresh start by definition, not a stalled
#           iteration) — otherwise the loop-then-resume pair of doctor calls
#           at start-up would put the counter at 2 before any work runs.
#
#   status  Read-only: report the run's live state in one shot — the RUN.md
#           run-level `status:`, the per-task phase table, the last
#           meaningful event from `orchestrator.log`, whether the recorded
#           orchestrator PID is actually live (guarding against a recycled
#           PID), the `--until` deadline, whether the done-sentinel
#           (written by `teardown --done-sentinel`, see below) is present, the
#           HEARTBEAT (fresh / stale against --task-ceiling, default 2700s = the
#           45m per-task ceiling), the declared EXIT REASON, and whether a
#           relaunch is therefore expected.
#           Never mutates anything. `--dir` defaults to $PWD; state is read
#           from <dir>/.auto-pilot/{RUN.md,orchestrator.log,orchestrator.done,heartbeat}.
#   teardown  Boot the launchd job out (`launchctl bootout`). With
#             `--done-sentinel <path>`, first atomically writes that file as
#             the durable completion marker, THEN boots the job out — so a
#             watcher polling the sentinel never observes "job gone, no
#             done-marker yet". This is the ONE completion mechanism `status`
#             also reads (see launch-runtime.md "Logs / observability").
#             `--reason <r>` records WHICH terminal exit reason the sentinel
#             stands for (`done` by default, but also `systemic`/`deadline`) —
#             the sentinel is the single file, so it carries the distinction
#             rather than a sibling marker being invented for it.
#
#   render-settings  Emit the ephemeral `claude -p --settings` JSON: layer-2
#                    network egress (sandbox.network.allowedDomains) narrowed to
#                    the resolved coders + source, deny-by-default. Fail-closed on
#                    an unresolvable required host (e.g. agy without --agy-host).
#
#   render-profile  Render the Seatbelt (.sb) profile from resolved paths into
#                   --out. Every path must be ABSOLUTE and EXIST; a relative or
#                   missing path fails closed and no file is written.
#     --rw    A read-write scope (run/worker worktrees, $TMPDIR). Repeatable.
#     --ro    A read-only scope (rest of repo, credential stores). Repeatable.
#     --cred-ro  A credential FILE kept read-only even when it lives inside an
#                --rw state dir (a tool's own token under ~/.codex/~/.claude). The
#                state dir stays writable; only the token is denied writes, via a
#                specific (deny file-write* (literal …)) emitted after the RW block.
#                Must be an existing file. Repeatable.
#     --exec  A binary permitted to exec (resolved coder + base toolchain).
#             Repeatable.
#     --exec-dir  A directory permitted to exec (subpath, coarser than --exec).
#                 Must exist and must not be "/". Repeatable.
#     --toolchain  Convenience: add each of a standard set of bin dirs (that
#                  exist on this host) to the exec-dir set.
#     --out   Destination path for the rendered profile. Required.
#     --template  Override the profile template (default: scripts/orchestrator.sb.tmpl).
#     Every render ALSO emits fixed, RENDERER-OWNED (never caller-supplied) write
#     scopes for Claude Code's own runtime surface: the $TMPDIR srt-mux-*.sock mux
#     socket (file-write* + network-bind), the /tmp/claude-<uid> scratch/cwd tree,
#     and ~/.claude/session-env. Without them the harness's inner sandbox can't
#     initialize and the Bash tool EPERMs on its own mkdir BEFORE running anything
#     — poisoning every exit code to 1. Renderer-owned because two of the three
#     live outside the run root and would otherwise trip --confine-under
#     (task 12 / finding #20; see orchestrator.sb.tmpl's @@HARNESS_RUNTIME@@).
#   check-profile  Confirm a rendered profile COMPILES via `sandbox-exec -f`,
#                  surfacing the Seatbelt parser error verbatim on failure.
#
# Exit status and structured final line:
#   0  spawn-orchestrator: profile OK <path>       render/check succeeded
#   2  usage, dependency, or fail-closed path error (nothing partial written)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DEFAULT="$ROOT/scripts/orchestrator.sb.tmpl"
# The done-sentinel filename `status` looks for under <dir>/.auto-pilot/. Callers
# pass the matching absolute path (<dir>/.auto-pilot/orchestrator.done) to
# `teardown --done-sentinel`; the two agree on this one name so it's a single
# completion mechanism, never a second independent marker (launch-runtime.md
# "Logs / observability").
DONE_SENTINEL_NAME="orchestrator.done"
# Local supervisor bookkeeping for the no-progress guard (task 10) — the
# consecutive-failure counter + last-seen run-state HEAD. Lives beside RUN.md
# but is NEVER committed to the run-state branch itself (it's wake-to-wake
# scratch state, not part of the run's durable record).
SUPERVISOR_STATE_NAME="supervisor-state"
# The heartbeat the orchestrator touches per loop iteration / per /deliver-task
# sub-step, and the launch wrapper touches at the top of each wake. Like
# supervisor-state it is NOT committed to the run-state branch (it beats far too
# often to belong in a durable record); unlike supervisor-state it is written by
# the AGENT, so it lives in its own file rather than racing the supervisor's
# rewrites of that one.
HEARTBEAT_NAME="heartbeat"
# The per-task wall-clock ceiling `status` measures the heartbeat's age against
# (run-budget.md "Per-task wall-clock bound"): 45 minutes. Older than this with
# no beat means WEDGED, not merely slow.
DEFAULT_TASK_CEILING=2700

die() { echo "spawn-orchestrator: $*" >&2; exit 2; }

# --- CONVENTION (task 26, the sweep after #191's `_alarm_safe`) ---------------
# `die` is `exit 2`, not `return 2`. An `exit` inside a same-shell function call
# terminates the WHOLE PROCESS regardless of `|| true` at the call site — `||
# true` only ever sees a nonzero RETURN, never an exit, so `some_fn ... || true`
# written to mean "best effort, never fatal" is NOT best-effort when `some_fn`
# can `die`. On a halt/teardown/cleanup path this converts a recoverable error
# into a silent, terminal one on exactly the paths that exist to handle errors
# (#191's `alarm` bug; task 26's `teardown`/`_write_supervisor_state` bugs).
#
# So: any function that can run from a halt/teardown/cleanup path must either
#   (a) reserve `die` for its OWN argument validation only, and `return`
#       non-zero for every post-validation (runtime) failure — the preferred
#       shape, used by e.g. `_verify_bootout`, `_write_done_sentinel`; or
#   (b) if it can't be changed to (a) — e.g. it's a shared function like
#       `teardown` whose die-on-runtime-failure IS its contract for OTHER
#       callers — every "best effort" call site must subshell it:
#       `( fn ... ) || warn "..."`. An `exit` inside `( ... )` only kills the
#       subshell, so `|| true`/`|| warn` downstream of the `)` is finally
#       telling the truth. This is the `_alarm_safe` pattern; see
#       `_supervisor_halt`'s and `supervisor_check`'s `teardown` calls for the
#       inline version of the same subshell.
# A bare `fn ... || true` around a die-capable function is ALWAYS wrong; the
# regression guard in test-spawn-orchestrator.sh pins the specific fixes down.
#
# NOT covered by this: a `%q ... || true` inside a `printf` that GENERATES the
# launch script (e.g. `supervisor-scan ... || true`, `heartbeat ... || true`).
# Those wrap a SEPARATE PROCESS invocation of this same script — an `exit`
# there only ends that child process, so `|| true` genuinely works. This
# convention is about same-shell function calls only.
# -------------------------------------------------------------------------------

# The exit-reason vocabulary (task 15). Exactly five values, and the split that
# matters is relaunch-vs-teardown:
#   continuing  work remains, context exhausted   → RELAUNCH
#   paused      rate window / paused_until set    → RELAUNCH (past the reset)
#   done        no ready tasks remain             → TEAR DOWN
#   systemic    circuit breaker / fatal auth      → TEAR DOWN + alarm
#   deadline    pre-dispatch guard, tasks ready   → TEAR DOWN (resume via --resume)
_is_exit_reason() {
  case "$1" in continuing|paused|done|systemic|deadline) return 0 ;; *) return 1 ;; esac
}
# The three that mean "do not relaunch me". `systemic` is terminal too, but it
# routes through the halt path (alarm + REPORT entry), not the plain teardown.
_is_terminal_reason() {
  case "$1" in done|systemic|deadline) return 0 ;; *) return 1 ;; esac
}

# Canonicalize an absolute, existing path. Prints the canonical path on success;
# on a fail-closed condition it writes the reason to stderr and RETURNS non-zero
# (never `exit` — this runs inside a $(command substitution), where `exit` would
# only kill the subshell and let the caller sail on). Callers must `|| exit 2`.
# Resolves via cd/pwd -P so no external `realpath` is required (not on all macOS).
canonicalize() {
  local p="$1"
  case "$p" in
    /*) ;;
    *) echo "spawn-orchestrator: path must be absolute (fail-closed): $p" >&2; return 1 ;;
  esac
  # A newline in a path would break the line-oriented SBPL emission (`(allow …)`
  # rules, the `;; @spawn-tmpdir:` stamp) — a crafted dir name could smuggle its
  # own rule or a fake stamp line. No legitimate path here contains one; reject
  # fail-closed, same posture as the egress-host newline guard.
  case "$p" in *$'\n'*) echo "spawn-orchestrator: path contains a newline (fail-closed)" >&2; return 1 ;; esac
  [ -e "$p" ] || { echo "spawn-orchestrator: path does not exist (fail-closed): $p" >&2; return 1; }
  if [ -d "$p" ]; then
    (cd "$p" && pwd -P)
  else
    # Resolve the file's symlink chain: Seatbelt matches process-exec (and file
    # rules) against the vnode's canonical target, not the symlink literal. Many
    # macOS binaries are symlinks (e.g. /opt/homebrew/bin/gh → Cellar/…), and
    # `command -v` hands back the symlink — so authorizing only the symlink path
    # would silently deny the exec. Follow links, re-canonicalizing the dir each
    # hop, with a loop guard.
    local d b target guard=0
    while [ -L "$p" ]; do
      guard=$((guard + 1))
      [ "$guard" -le 40 ] || { echo "spawn-orchestrator: symlink chain too deep (fail-closed): $1" >&2; return 1; }
      target="$(readlink "$p")" || { echo "spawn-orchestrator: cannot read link (fail-closed): $p" >&2; return 1; }
      case "$target" in
        /*) p="$target" ;;
        *)  p="$(cd "$(dirname "$p")" && pwd -P)/$target" ;;
      esac
    done
    d="$(cd "$(dirname "$p")" && pwd -P)" || { echo "spawn-orchestrator: cannot resolve directory of: $p" >&2; return 1; }
    b="$(basename "$p")"
    printf '%s/%s' "$d" "$b"
  fi
}

# Escape a path for use inside a Seatbelt "(subpath \"…\")" / "(literal \"…\")".
sbpl_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# Escape a canonicalized path for embedding inside an SBPL "(regex #\"…\")"
# string: backslash-prefix backslash/quote AND any literal regex metacharacter
# the path might contain (host temp-dir components are normally plain
# alnum/._-, but this must not silently mismatch if that's ever not true).
# Verified empirically against sandbox-exec: a SINGLE backslash in the profile
# source is enough for both the SBPL string reader and the regex engine to see
# a literal char — do NOT double it (that breaks the match).
sbpl_regex_escape() {
  local s="$1" out="" c i
  for (( i=0; i<${#s}; i++ )); do
    c="${s:$i:1}"
    case "$c" in
      '\'|'"'|'.'|'*'|'+'|'?'|'('|')'|'['|']'|'{'|'}'|'|'|'^'|'$') out+="\\$c" ;;
      *) out+="$c" ;;
    esac
  done
  printf '%s' "$out"
}

# Resolve a path whose LEAF may not exist yet. The harness creates its runtime
# dirs LAZILY (on first session), so canonicalize()'s existence check would
# fail-close a launch just because a dir hasn't been made yet — which is the
# wrong trade at 3am. Canonicalize the deepest EXISTING ancestor (so /tmp →
# /private/tmp still resolves through the symlink) and re-append the missing
# tail verbatim. Prints the resolved path; returns non-zero only if even the
# ancestor chain can't be resolved.
resolve_lazy() {
  local p="$1" tail=""
  case "$p" in /*) ;; *) echo "spawn-orchestrator: path must be absolute (fail-closed): $p" >&2; return 1 ;; esac
  while [ ! -e "$p" ] && [ "$p" != "/" ]; do
    tail="/$(basename "$p")$tail"
    p="$(dirname "$p")"
  done
  local c; c="$(canonicalize "$p")" || return 1
  printf '%s%s' "$c" "$tail"
}

# Emit the FIXED (never caller-supplied) write scopes for Claude Code's OWN
# runtime surface. These are host-resolved by the renderer, NOT passed as --rw,
# and that is load-bearing: two of the three live OUTSIDE the run root, so a
# caller-supplied --rw would either trip the --confine-under guard or force the
# launch path to weaken it. Renderer-owned means the containment guard keeps
# covering every caller scope while the harness still gets exactly what it needs.
# See orchestrator.sb.tmpl's @@HARNESS_RUNTIME@@ comment for why denying any of
# these poisons the harness itself (task 12 / finding #20).
# Args: <tmpdir-canonical> <tmp-root-canonical> <session-env-dir>.
emit_harness_runtime() {
  local tmpdir_c="$1" tmp_root="$2" session_env="$3"
  local sock_pattern claude_tmp_pattern
  sock_pattern="^$(sbpl_regex_escape "$tmpdir_c")"'/srt-mux-[0-9]+-[0-9]+\.sock$'
  # The harness's per-project/per-session scratch + cwd tree under /tmp. This is
  # a DIRECTORY TREE it mkdir's into, not a single file — a file-only grant
  # (e.g. just the claude-*-cwd literal) does not permit the enclosing mkdir, so
  # the Bash tool dies before it ever runs the command.
  #
  # Matched by PATTERN, not by a uid-resolved path: the id in claude-<id> is not
  # the uid (see render_profile) and a resolved path silently misses, restoring
  # the exit-code poisoning.
  #
  # TWO distinct shapes live here, and granting only one still poisons exit codes:
  #   /tmp/claude-<id>/<project>/<session>/…  the scratch TREE (mkdir'd)
  #   /tmp/claude-<hex>-cwd                   the cwd-tracking FILE, rewritten
  #                                           after EVERY Bash call, with a fresh
  #                                           hex id each time
  # The detached orchestrator uses the -cwd files; an interactive session was seen
  # using the numeric tree. Cover both, and no more: still never a blanket /tmp write.
  #
  # ACCEPTED RELAXATION: this pattern is deliberately broader than the old
  # `(subpath "/tmp/claude-$(id -u)")` — it grants any `claude-<id>` tree, not just
  # this uid's, because <id> is not the uid and can't be resolved ahead of time.
  # The widening is bounded by standard POSIX permissions, which SBPL grants do NOT
  # override: another operator's `claude-<id>` tree is only reachable if it is
  # independently writable to this uid (it isn't, under the single-operator host
  # this harness targets). So the relaxation is a real but low-severity trade of
  # tightness for the reliability that keeps finding #20 fixed.
  claude_tmp_pattern="^$(sbpl_regex_escape "$tmp_root")"'/claude-[A-Za-z0-9]+(-cwd)?(/|$)'
  printf '(allow file-write*\n'
  printf '  (regex #"%s")\n' "$sock_pattern"
  printf '  (regex #"%s")\n' "$claude_tmp_pattern"
  # The harness mkdir's a per-session dir under ~/.claude/session-env. Scoped to
  # session-env SPECIFICALLY — never a blanket ~/.claude write, which would put
  # the credential file inside a writable scope and undo the cred-RO/state-RW
  # split (§3) that --cred-ro exists to enforce.
  printf '  (subpath "%s")\n' "$(sbpl_escape "$session_env")"
  printf ')\n'
  # network-bind: a unix-domain socket bind/listen is gated on the socket's own
  # path (like Apple's own rpcbind.sb), not a free network class — this grant is
  # required IN ADDITION to the file-write* above, not instead of it. Without it
  # the harness's inner sandbox can't listen on its mux socket and silently
  # disables itself for the session.
  printf '(allow network-bind\n'
  printf '  (regex #"%s")\n' "$sock_pattern"
  printf ')\n'
}

# Emit an s-expression allowing `op` over the given canonical paths, or a
# placeholder comment when the list is empty. Args: <op> <path>...
emit_allow() {
  local op="$1"; shift
  if [ "$#" -eq 0 ]; then
    printf ';; (none)\n'
    return
  fi
  printf '(allow %s\n' "$op"
  local p
  for p in "$@"; do
    printf '  (subpath "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

# Emit a (deny file-write* (literal …)) form over credential files, or a
# placeholder comment when the list is empty. This is emitted AFTER the RW block
# so it overrides the state-dir write allow (Seatbelt = last matching rule wins),
# keeping a token read-only while its enclosing state dir stays writable. Args:
# <cred-path>...
emit_deny_write() {
  if [ "$#" -eq 0 ]; then
    printf ';; (no isolated credential files)\n'
    return
  fi
  printf '(deny file-write*\n'
  local p
  for p in "$@"; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

# Emit the process-exec allow form: literal binaries (--exec) and subpath bin
# dirs (--exec-dir/--toolchain). Args: <literal>... -- <subpath>...
emit_exec() {
  local -a lits=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do lits+=("$1"); shift; done
  shift || true   # drop the -- separator
  if [ "${#lits[@]}" -eq 0 ] && [ "$#" -eq 0 ]; then
    printf ';; (no coder/toolchain binaries resolved)\n'
    return
  fi
  printf '(allow process-exec\n'
  local p
  for p in ${lits[@]+"${lits[@]}"}; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  for p in "$@"; do
    printf '  (subpath "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

render_profile() {
  local out="" template="$TEMPLATE_DEFAULT" toolchain=0 tmpdir=""
  local -a rw=() ro=() cred=() ex=() exd=() confine=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --rw) [ $# -ge 2 ] || die "missing value for --rw"; rw+=("$2"); shift 2 ;;
      --ro) [ $# -ge 2 ] || die "missing value for --ro"; ro+=("$2"); shift 2 ;;
      --cred-ro) [ $# -ge 2 ] || die "missing value for --cred-ro"; cred+=("$2"); shift 2 ;;
      --tmpdir) [ $# -ge 2 ] || die "missing value for --tmpdir"; tmpdir="$2"; shift 2 ;;
      --exec) [ $# -ge 2 ] || die "missing value for --exec"; ex+=("$2"); shift 2 ;;
      --exec-dir) [ $# -ge 2 ] || die "missing value for --exec-dir"; exd+=("$2"); shift 2 ;;
      --toolchain) toolchain=1; shift ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; confine+=("$2"); shift 2 ;;
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      --template) [ $# -ge 2 ] || die "missing value for --template"; template="$2"; shift 2 ;;
      *) die "unknown render-profile argument: $1" ;;
    esac
  done

  [ -n "$out" ] || die "render-profile requires --out <file>"
  [ -f "$template" ] || die "profile template not found: $template"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # --toolchain: expand to the standard bin dirs, skipping any that don't exist
  # on this host (a missing standard dir is not fail-closed — it's just omitted).
  if [ "$toolchain" = 1 ]; then
    local d
    for d in /bin /usr/bin /usr/sbin /usr/libexec /opt/homebrew/bin \
      "$HOME/.local/bin" "$HOME/.local/share/claude" "$HOME/.codex" "$HOME/.nvm"; do
      [ -d "$d" ] && exd+=("$d")
    done
  fi

  # Canonicalize ALL inputs first — any bad path aborts before we write, so a
  # partial profile is never emitted.
  local -a rw_c=() ro_c=() cred_c=() ex_c=() exd_c=() confine_c=()
  local p c
  for p in ${confine[@]+"${confine[@]}"}; do c="$(canonicalize "$p")" || exit 2; confine_c+=("$c"); done
  for p in ${rw[@]+"${rw[@]}"}; do c="$(canonicalize "$p")" || exit 2; rw_c+=("$c"); done
  for p in ${ro[@]+"${ro[@]}"}; do c="$(canonicalize "$p")" || exit 2; ro_c+=("$c"); done
  # Credential files: must be an existing FILE (a token, not a dir). The literal
  # deny is emitted after the RW block so it overrides a co-located state-dir
  # write allow; a cred file outside every RW scope is harmless (already unwritable).
  # KNOWN LIMITATION (documented, not fail-closed): Seatbelt matches on the path,
  # not the inode, so a second HARD LINK to the same credential inode inside an RW
  # scope stays writable and would mutate the token through the alias. We do NOT
  # reject nlink>1 here — a benign multiply-linked file must not fail a launch
  # closed at 3am — but tool credential files are not hard-linked in practice.
  for p in ${cred[@]+"${cred[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    [ -f "$c" ] || die "cred-ro path is not a file (fail-closed): $c"
    cred_c+=("$c")
  done
  for p in ${ex[@]+"${ex[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    { [ -f "$c" ] && [ -x "$c" ]; } || die "exec path is not an executable file (fail-closed): $c"
    ex_c+=("$c")
  done
  for p in ${exd[@]+"${exd[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    [ -d "$c" ] || die "exec-dir is not a directory (fail-closed): $c"
    # Dedupe against what's already resolved (explicit --exec-dir + --toolchain
    # can overlap, e.g. /opt/homebrew/bin listed twice).
    case " ${exd_c[@]+"${exd_c[@]}"} " in *" $c "*) continue ;; esac
    exd_c+=("$c")
  done

  # Floor (ALWAYS, even without --confine-under): a write scope of "/" emits a
  # whole-filesystem write rule that defeats the jail. Refuse it unconditionally —
  # the containment guard below is opt-in and can't protect itself if a caller
  # forgets --confine-under, so this floor closes the worst case regardless.
  local w r under
  for w in ${rw_c[@]+"${rw_c[@]}"}; do
    [ "$w" = "/" ] && die "refusing --rw / (whole-filesystem write, fail-closed)"
  done
  # Same floor for exec breadth: an exec-dir of "/" would allow-list exec of
  # every binary on the filesystem, defeating the exec wall entirely.
  for w in ${exd_c[@]+"${exd_c[@]}"}; do
    [ "$w" = "/" ] && die "refusing --exec-dir / (whole-filesystem exec, fail-closed)"
  done
  # Containment: when --confine-under roots are given, every WRITE scope must
  # canonicalize inside one of them (so a worktree symlinked to /$HOME can't slip
  # a broad write through). Both sides are already pwd -P'd; the prefix test is
  # LITERAL (`${w#"$r"/}`, not a glob) so a root containing * / [ can't over-match.
  # RO/exec are intentionally exempt (creds and system binaries live outside the
  # run root). Fail-closed on any escape.
  if [ "${#confine_c[@]}" -gt 0 ]; then
    for w in ${rw_c[@]+"${rw_c[@]}"}; do
      under=0
      for r in "${confine_c[@]}"; do
        { [ "$w" = "$r" ] || [ "${w#"$r"/}" != "$w" ]; } && under=1
      done
      [ "$under" = 1 ] || die "write scope escapes --confine-under (fail-closed): $w"
    done
  fi

  # Build the token blocks.
  local ro_block rw_block cred_block ex_block harness_block
  ro_block="$(emit_allow "file-read*" ${ro_c[@]+"${ro_c[@]}"})"
  # RW scopes need both read and write.
  rw_block="$(emit_allow "file-read*" ${rw_c[@]+"${rw_c[@]}"})
$(emit_allow "file-write*" ${rw_c[@]+"${rw_c[@]}"})"
  cred_block="$(emit_deny_write ${cred_c[@]+"${cred_c[@]}"})"
  ex_block="$(emit_exec ${ex_c[@]+"${ex_c[@]}"} -- ${exd_c[@]+"${exd_c[@]}"})"
  # Harness's OWN runtime surface (task 12 / finding #20) — fixed, host-resolved
  # paths, never caller-supplied (and so never subject to the caller-scope
  # --confine-under check above; two of these legitimately live outside the run
  # root). $TMPDIR must exist; session-env is created lazily on first session, so
  # resolve_lazy tolerates its absence rather than fail-closing a launch over a
  # dir the harness is about to make itself.
  #
  # The harness's tmp tree is /tmp/claude-<N>, and N is NOT the uid — it varies
  # per harness instance. A detached launchd orchestrator was observed using
  # /tmp/claude-522 on a uid-501 host while the interactive session on the same
  # host used /tmp/claude-501. A uid-derived path therefore matches only by
  # coincidence: when it misses, the harness's mkdir is denied, the Bash tool
  # dies before running anything, and EVERY exit code is poisoned to 1 — finding
  # #20, the exact failure this grant exists to prevent, silently restored. So
  # match the tree by PATTERN over /tmp, never by a uid-resolved path.
  #
  # The detached launchd job's TMPDIR is authoritative here: the srt-mux socket
  # grant is anchored to it, and write-launch reads it back from the @spawn-tmpdir
  # stamp emitted below (so the two can never diverge — a divergence silently
  # re-breaks the inner sandbox). Prefer the explicit --tmpdir (the run-owned dir
  # the job will export); fall back to the env only when a caller renders a profile
  # for the ambient session. When --tmpdir is given AND the render is confined,
  # require it to sit inside a confinement root so the job's later `mkdir -p` is
  # bounded.
  local tmpdir_c tmp_root session_env
  if [ -n "$tmpdir" ]; then
    case "$tmpdir" in /*) ;; *) die "--tmpdir must be absolute (fail-closed): $tmpdir" ;; esac
    tmpdir_c="$(resolve_lazy "$tmpdir")" || exit 2
    if [ "${#confine_c[@]}" -gt 0 ]; then
      local _t_ok=0 r
      for r in "${confine_c[@]}"; do
        [ "$tmpdir_c" = "$r" ] || [ "${tmpdir_c#"$r"/}" != "$tmpdir_c" ] && { _t_ok=1; break; }
      done
      [ "$_t_ok" = 1 ] || die "--tmpdir escapes --confine-under (fail-closed): $tmpdir_c"
    fi
  else
    tmpdir_c="$(canonicalize "${TMPDIR:-/tmp}")" || exit 2
  fi
  tmp_root="$(canonicalize "/tmp")" || exit 2
  session_env="$(resolve_lazy "$HOME/.claude/session-env")" || exit 2
  harness_block="$(emit_harness_runtime "$tmpdir_c" "$tmp_root" "$session_env")"

  # Substitute tokens line-by-line into a temp file, then move into place so a
  # failure mid-render leaves no partial --out.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator.sb.XXXXXX")" || die "mktemp failed"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      *@@RO_PATHS@@*) printf '%s\n' "$ro_block" ;;
      *@@RW_PATHS@@*) printf '%s\n' "$rw_block" ;;
      *@@CRED_DENY@@*) printf '%s\n' "$cred_block" ;;
      *@@HARNESS_RUNTIME@@*) printf '%s\n' "$harness_block" ;;
      *@@EXEC_PATHS@@*) printf '%s\n' "$ex_block" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done <"$template" >"$tmp"

  # Stamp the canonical TMPDIR the srt-mux grant is anchored to, as an SBPL
  # comment (`;;` is ignored by the compiler). write-launch reads THIS back to set
  # the job's TMPDIR, so the socket the job creates always lands where the profile
  # grants — the render and the launch can't drift apart. Exactly one stamp.
  printf ';; @spawn-tmpdir: %s\n' "$tmpdir_c" >>"$tmp"

  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write profile: $out"; }
  echo "spawn-orchestrator: profile OK $out"
}

# Build the narrowed egress host set (sorted, unique), fail-closed. This is
# layer 2 of the jail: Seatbelt can't filter by hostname, so egress is enforced
# by the detached `claude -p`'s own sandbox.network allowlist. The set is
# narrowed to the run's resolved coders + source so a linear+codex run never
# opens devin's or agy's endpoints. Args are parsed by render_settings below.
render_network_allowlist() {
  local source="" agy_host="" npm=0
  local -a coders=() mcp=() add_task=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --coder) [ $# -ge 2 ] || die "missing value for --coder"; coders+=("$2"); shift 2 ;;
      --source) [ $# -ge 2 ] || die "missing value for --source"; source="$2"; shift 2 ;;
      --agy-host) [ $# -ge 2 ] || die "missing value for --agy-host"; agy_host="$2"; shift 2 ;;
      --mcp-host) [ $# -ge 2 ] || die "missing value for --mcp-host"; mcp+=("$2"); shift 2 ;;
      --add-task-host) [ $# -ge 2 ] || die "missing value for --add-task-host"; add_task+=("$2"); shift 2 ;;
      --npm) npm=1; shift ;;
      *) die "unknown allowlist argument: $1" ;;
    esac
  done
  case "$source" in
    linear|plan) ;;
    "") die "network allowlist requires --source <linear|plan>" ;;
    *) die "unknown --source (fail-closed): $source" ;;
  esac

  # Always needed: the orchestrator model + GitHub (PRs, git over HTTPS).
  local -a hosts=(api.anthropic.com api.github.com github.com codeload.github.com '*.githubusercontent.com')
  [ "$source" = linear ] && hosts+=(api.linear.app)
  [ "$npm" = 1 ] && hosts+=(registry.npmjs.org)

  local c
  for c in ${coders[@]+"${coders[@]}"}; do
    case "$c" in
      codex) hosts+=(api.openai.com) ;;
      devin) hosts+=(api.devin.ai server.codeium.com) ;;
      agy)
        [ -n "$agy_host" ] || die "coder 'agy' requires --agy-host <resolved Antigravity host> (fail-closed): install/re-route or resolve the host at pre-flight"
        case "$agy_host" in
          \**) die "agy host must be a concrete host, never a wildcard (fail-closed): $agy_host" ;;
        esac
        hosts+=("$agy_host") ;;
      *) die "unknown --coder (fail-closed): $c" ;;
    esac
  done
  hosts+=(${mcp[@]+"${mcp[@]}"})
  # The add-task destination host is added regardless of --source: a plan-source
  # run whose /add-task handler routes to Linear/Jira still needs egress there, or
  # the run's own settings would deny its own tracked-follow-up filing.
  hosts+=(${add_task[@]+"${add_task[@]}"})

  # Validate EVERY host, fail-closed. Host values from --mcp-host / --agy-host /
  # --add-task-host are resolved per-run from the (possibly adversary-influenced)
  # work source, so an unvalidated value could smuggle a bare `*` (nullifying the
  # allowlist) or inject JSON in hosts_to_json_array. Require a real hostname —
  # labels of [A-Za-z0-9-] separated by dots, with at most a single leading `*.` subdomain wildcard (which
  # `*.githubusercontent.com` legitimately uses; a bare `*` has no labels and is
  # rejected). The agy-specific guard above additionally bans ANY wildcard for agy.
  local h
  for h in "${hosts[@]}"; do
    # Reject an embedded newline FIRST: `grep -Eq` matches if ANY line matches,
    # so a multiline value could pass the anchored regex on one line while
    # hosts_to_json_array splits it into a second, unvalidated host — smuggling a
    # wildcard past the allowlist. A real hostname never contains a newline.
    case "$h" in *$'\n'*) die "invalid egress host (embedded newline, fail-closed): $h" ;; esac
    printf '%s' "$h" | grep -Eq '^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$' \
      || die "invalid egress host (fail-closed): $h"
  done

  printf '%s\n' "${hosts[@]}" | sort -u
}

# Emit a JSON array literal from the host lines on stdin.
hosts_to_json_array() {
  local first=1 out="["
  local h
  while IFS= read -r h; do
    [ -n "$h" ] || continue
    [ "$first" = 1 ] && first=0 || out+=","
    out+="\"$h\""
  done
  out+="]"
  printf '%s' "$out"
}

render_settings() {
  local out="" localbind=false
  local -a passthru=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      # Default OFF: a listen socket lets the inside process reach host-local
      # services (the Solo control port, local proxies, …) — a pivot the jail
      # shouldn't grant. Enable only if a run genuinely needs to bind loopback.
      --allow-local-binding) localbind=true; shift ;;
      *) passthru+=("$1"); shift ;;
    esac
  done
  [ -n "$out" ] || die "render-settings requires --out <file>"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # render_network_allowlist runs in a $() — its die() exits only the subshell,
  # so capture the status and abort here (fail-closed: no settings on any error).
  local hosts array
  hosts="$(render_network_allowlist ${passthru[@]+"${passthru[@]}"})" || exit 2
  array="$(printf '%s\n' "$hosts" | hosts_to_json_array)"

  # Ephemeral run settings for `claude -p --settings '<json>'`: deny-by-default
  # egress narrowed to $array, plus loopback for the enforcement proxy + local
  # tooling. Written to --out (audit trail); the caller passes it via --settings,
  # never mutating ~/.claude.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-settings.XXXXXX")" || die "mktemp failed"
  printf '{"sandbox":{"enabled":true,"network":{"allowedDomains":%s,"allowLocalBinding":%s}}}\n' "$array" "$localbind" >"$tmp" \
    || { rm -f "$tmp"; die "failed to write settings"; }
  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write settings: $out"; }
  echo "spawn-orchestrator: settings OK $out"
}

# ---------------------------------------------------------------------------
# Task 3 — spawn mechanics: launch script + relaunchable launchd supervisor,
# an auth smoke-test THROUGH the wrapper (before detaching), and the run handle.
# ---------------------------------------------------------------------------

PLIST_TEMPLATE_DEFAULT="$ROOT/scripts/orchestrator.plist.tmpl"

# Escape a value for inclusion inside a plist <string>…</string>. `&` MUST be
# replaced first. Without this, a value containing plist/XML metacharacters (legal
# in macOS paths, and a label is caller-set) could inject launchd keys — e.g. a
# second <key>ProgramArguments</key>, which launchd execs DIRECTLY, not under
# sandbox-exec, bypassing the whole jail. This is the plist analogue of sbpl_escape.
xml_escape() {
  local s="$1"
  s="${s//&/&amp;}"
  s="${s//</&lt;}"
  s="${s//>/&gt;}"
  s="${s//\"/&quot;}"
  printf '%s' "$s"
}

# Inline token replacement (bash, not sed) so arbitrary paths with regex-special
# chars can't corrupt the render; every substituted string value is XML-escaped.
render_plist() {
  local label launch_script workdir log
  label="$(xml_escape "$1")"; launch_script="$(xml_escape "$2")"
  workdir="$(xml_escape "$3")"; log="$(xml_escape "$4")"
  local interval="$5" throttle="$6" template="$7"   # integers, validated numeric upstream
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line//@@LABEL@@/$label}"
    line="${line//@@LAUNCH_SCRIPT@@/$launch_script}"
    line="${line//@@WORKDIR@@/$workdir}"
    line="${line//@@LOG@@/$log}"
    line="${line//@@INTERVAL@@/$interval}"
    line="${line//@@THROTTLE@@/$throttle}"
    printf '%s\n' "$line"
  done <"$template"
}

# Emit the self-contained launch script + the launchd plist from resolved inputs.
# The launch script is what the plist runs: it composes the jail
# (sandbox-exec -f <profile>) around `claude -p --permission-mode bypassPermissions`
# with the layer-2 --settings, reads the run prompt from a file, and redirects to
# the log. %q-quoting keeps every interpolated path/string shell-safe.
write_launch() {
  local profile="" settings="" workdir="" log="" prompt="" until="" \
        label="" interval="300" throttle="30" out_script="" out_plist="" \
        plist_template="$PLIST_TEMPLATE_DEFAULT" claude_bin="" path="" tmpdir="" \
        self="$ROOT/scripts/spawn-orchestrator.sh" no_progress_limit="3"
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profile="$2"; shift 2 ;;
      --settings) settings="$2"; shift 2 ;;
      --workdir) workdir="$2"; shift 2 ;;
      --log) log="$2"; shift 2 ;;
      --prompt-file) prompt="$2"; shift 2 ;;
      --until) until="$2"; shift 2 ;;
      --label) label="$2"; shift 2 ;;
      --interval) interval="$2"; shift 2 ;;
      --throttle) throttle="$2"; shift 2 ;;
      --claude-bin) claude_bin="$2"; shift 2 ;;
      --path) path="$2"; shift 2 ;;
      --tmpdir) tmpdir="$2"; shift 2 ;;
      --out-script) out_script="$2"; shift 2 ;;
      --out-plist) out_plist="$2"; shift 2 ;;
      --plist-template) plist_template="$2"; shift 2 ;;
      --self) self="$2"; shift 2 ;;
      --no-progress-limit) no_progress_limit="$2"; shift 2 ;;
      *) die "unknown write-launch argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-launch requires --out-script and --out-plist"
  [ -n "$path" ] || die "write-launch requires --path <PATH> (fail-closed): a launchd job has a minimal PATH; pass the fingerprint-resolved toolchain dirs"
  [ -n "$label" ] || die "write-launch requires --label"
  # Pin the label to a launchd reverse-DNS charset — belt-and-suspenders against
  # plist injection on top of xml_escape, and it's what launchd expects anyway.
  case "$label" in *[!A-Za-z0-9._-]*) die "--label must be [A-Za-z0-9._-] (fail-closed): $label" ;; esac
  local f
  for f in "$profile" "$settings" "$prompt"; do
    [ -n "$f" ] || die "write-launch requires --profile, --settings, and --prompt-file"
    [ -f "$f" ] || die "not found (fail-closed): $f"
  done
  # The launchd job's TMPDIR comes from the profile's @spawn-tmpdir stamp — the
  # SAME canonical dir the srt-mux socket grant is anchored to (render_profile
  # emits it). Deriving it from the profile makes a render/launch drift impossible:
  # the job exports exactly the dir whose mux socket the profile permits. A stale
  # profile with no stamp is fail-closed. A passed --tmpdir is an OPTIONAL
  # cross-check: it must resolve to the stamped dir, else the caller rendered and
  # launched with different dirs and the inner sandbox would silently degrade.
  local stamped
  # tail -1, NOT head -1: render_profile appends the real stamp LAST (after all
  # template substitution), and nothing user-controlled can land after it — so
  # the last match is authoritative. head -1 would let an earlier `;; @spawn-tmpdir:`
  # line (e.g. one smuggled through a scope path) override the real one.
  stamped="$(sed -n 's/^;; @spawn-tmpdir: //p' "$profile" | tail -1)"
  [ -n "$stamped" ] || die "profile has no @spawn-tmpdir stamp (fail-closed): re-render it with the current render-profile: $profile"
  if [ -n "$tmpdir" ]; then
    case "$tmpdir" in /*) ;; *) die "--tmpdir must be absolute (fail-closed): $tmpdir" ;; esac
    local tmpdir_check
    tmpdir_check="$(resolve_lazy "$tmpdir")" || exit 2
    [ "$tmpdir_check" = "$stamped" ] || die "--tmpdir ($tmpdir_check) does not match the profile's rendered TMPDIR ($stamped) (fail-closed) — render the profile and launch with the SAME --tmpdir"
  fi
  tmpdir="$stamped"
  [ -n "$workdir" ] && [ -d "$workdir" ] || die "write-launch requires an existing --workdir"
  [ -n "$log" ] || die "write-launch requires --log"
  case "$interval$throttle" in *[!0-9]*) die "--interval/--throttle must be integers" ;; esac
  case "$no_progress_limit" in *[!0-9]*|"") die "--no-progress-limit must be a positive integer" ;; esac
  [ "$no_progress_limit" -ge 1 ] || die "--no-progress-limit must be a positive integer"
  [ -f "$self" ] || die "spawn-orchestrator.sh not found (fail-closed): $self"

  local settings_json; settings_json="$(cat "$settings")"
  # Resolve claude to an ABSOLUTE path: a detached launchd job runs with a minimal
  # PATH (/usr/bin:/bin:/usr/sbin:/sbin) and would not find a Homebrew `claude`.
  # The caller should pass --claude-bin (the same path it gave render-profile's
  # --exec, so the launch invokes exactly the binary the profile permits); default
  # to `command -v claude` for convenience.
  [ -n "$claude_bin" ] || claude_bin="$(command -v claude 2>/dev/null)" || claude_bin=""
  [ -n "$claude_bin" ] || die "claude not found (fail-closed): pass --claude-bin or put claude on PATH"
  case "$claude_bin" in /*) ;; *) die "--claude-bin must be absolute (fail-closed): $claude_bin" ;; esac

  local state_file="$workdir/.auto-pilot/$SUPERVISOR_STATE_NAME"
  local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-launch.XXXXXX")" || die "mktemp failed"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Auto-pilot orchestrator launch script (generated — do not edit).\n'
    printf 'set -uo pipefail\n'
    printf 'export AUTO_PILOT_UNTIL=%q\n' "$until"
    printf 'export PATH=%q\n' "$path"
    # A launchd job inherits no usable TMPDIR, so without this the jailed process
    # has nowhere writable to put temp files: zsh dies with "can't create temp file
    # for here document" on any heredoc, and the harness's mux socket can't be
    # created. This is the profile's own @spawn-tmpdir (read above) — the exact dir
    # the srt-mux socket grant is anchored to — so the job's mux socket always
    # lands where the profile permits it; render and launch cannot drift apart.
    printf 'export TMPDIR=%q\n' "$tmpdir"
    printf 'mkdir -p %q\n' "$tmpdir"
    printf 'cd %q\n' "$workdir"
    # The supervisor's own bookkeeping — ABOVE the gate, so it runs on EVERY wake
    # including the ones the gate closes (task 16). The gate below short-circuits
    # the AGENT INVOCATION only; a gate-closed wake is precisely when a halted or
    # stalled run must still tell a human, so nothing that the supervisor owes the
    # human may sit under the gate's `exit 0`. Per-wake supervisor bookkeeping
    # added later goes HERE, on this side of the gate, for the same reason. Never
    # fails the wake: bookkeeping must not be able to prevent the agent from running.
    printf '%q supervisor-scan --dir %q --label %q >>%q 2>&1 || true\n' "$self" "$workdir" "$label" "$log"
    # Beat the heartbeat — ALSO above the gate, and for the same reason (task 15 +
    # task 16's seam). It is the wake's liveness signal, not the agent's: a claude
    # that wedges before its first loop iteration must still leave "this wake
    # happened at T", and so must a wake the gate CLOSES. A rate-window pause is
    # hours of legitimately gate-closed wakes; with the beat under the gate's
    # `exit 0`, `status` would age the last pre-pause beat past the 45m per-task
    # ceiling and report a healthy, paused run as a STALL — turning the one signal
    # that separates slow from wedged into a false alarm exactly when the run is
    # doing the right thing. Never fails the wake.
    printf '%q heartbeat --dir %q --note wake-start >>%q 2>&1 || true\n' "$self" "$workdir" "$log"
    # Pre-invoke gate (task 11 / finding #19): a pure shell timestamp check,
    # BEFORE claude ever starts, so a wake that lands mid-pause costs no
    # model call. Exit 20 is the gate's distinct "do not invoke" signal;
    # anything else (including a gate error) falls through to claude —
    # fail-safe, matching the gate's own posture.
    printf '%q supervisor-gate --dir %q --label %q >>%q 2>&1\n' "$self" "$workdir" "$label" "$log"
    printf 'gate=$?\n'
    printf 'if [ "$gate" -eq 20 ]; then exit 0; fi\n'
    # Record the log's size BEFORE this wake writes to it. The log is appended
    # to across every wake, so classify-exit must look only at the bytes THIS
    # process wrote — otherwise an old wake's 401 stays in the file forever and
    # would keep halting the run long after a human re-authenticated.
    printf 'off=$(wc -c <%q 2>/dev/null | tr -d " ") || off=0\n' "$log"
    printf ': "${off:=0}"\n'
    # Stamp this wake's start (epoch) BEFORE claude runs — and BELOW the gate, on
    # the agent side of the seam, because that is the only side that can produce a
    # declaration: `supervisor-check` compares this stamp against RUN.md's
    # `exit_reason_at` so only THIS wake's declaration counts (the reason lives on
    # the run-state branch and outlives the wake that wrote it, and a stale one must
    # never decide a later wake's fate). A gate-closed wake never invokes the agent
    # and never reaches supervisor-check, so it has nothing to date.
    printf 'wake=$(date +%%s)\n'
    # NOT `exec`'d (task 10): the wrapper must observe claude's exit to
    # classify it, so the launchd-tracked PID is this wrapper, not claude
    # itself. `set +e`/`set -e` bracket the one command allowed to fail.
    printf 'set +e\n'
    printf 'sandbox-exec -f %q \\\n' "$profile"
    printf '  %q -p "$(cat %q)" \\\n' "$claude_bin" "$prompt"
    printf '  --permission-mode bypassPermissions \\\n'
    printf '  --settings %q \\\n' "$settings_json"
    printf '  --verbose \\\n'
    printf '  --output-format stream-json \\\n'
    printf '  >>%q 2>&1\n' "$log"
    printf 'code=$?\n'
    printf 'set -e\n'
    # Supervisor-side classification (no model call, task 10 / finding #22):
    # a non-retryable auth failure halts the run instead of relaunching into
    # the same 401 forever; an unclassified repeated failure with no
    # run-state progress also halts (the general backstop). A retryable exit
    # (or a legitimate paused_until wait) just exits non-zero, and the
    # plist's StartInterval relaunches as before.
    printf '%q supervisor-check --exit-code "$code" --log %q --since-offset "$off" --wake-start "$wake" --dir %q --label %q --state %q --no-progress-limit %q\n' \
      "$self" "$log" "$workdir" "$label" "$state_file" "$no_progress_limit"
    printf 'exit $?\n'
  } >"$tmp" || { rm -f "$tmp"; die "failed to write launch script"; }
  mv "$tmp" "$out_script" || { rm -f "$tmp"; die "failed to write launch script: $out_script"; }
  chmod +x "$out_script"

  [ -f "$plist_template" ] || die "plist template not found: $plist_template"
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-plist.XXXXXX")" || die "mktemp failed"
  render_plist "$label" "$out_script" "$workdir" "$log" "$interval" "$throttle" "$plist_template" >"$tmp" \
    || { rm -f "$tmp"; die "failed to render plist"; }
  mv "$tmp" "$out_plist" || { rm -f "$tmp"; die "failed to write plist: $out_plist"; }
  echo "spawn-orchestrator: launch written $out_script $out_plist"
}

# Record the orchestrator's identity for stale/recycled-PID detection: the PID,
# its process start-time (guards against a recycled PID being mistaken for a live
# run), and the run's --until deadline.
record_handle() {
  local pid="" until="" out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pid) pid="$2"; shift 2 ;;
      --until) until="$2"; shift 2 ;;
      --out) out="$2"; shift 2 ;;
      *) die "unknown record-handle argument: $1" ;;
    esac
  done
  [ -n "$pid" ] && [ -n "$out" ] || die "record-handle requires --pid and --out"
  case "$pid" in *[!0-9]*|"") die "--pid must be numeric: $pid" ;; esac
  local started; started="$(ps -p "$pid" -o lstart= 2>/dev/null)" || true
  [ -n "$started" ] || die "no live process at pid $pid (cannot record a dead handle)"
  {
    printf 'orchestrator_pid: %s\n' "$pid"
    printf 'orchestrator_started_at: %s\n' "$started"
    printf 'until: %s\n' "$until"
  } >"$out" || die "failed to write handle: $out"
  echo "spawn-orchestrator: handle recorded $out"
}

# Auth smoke-test THROUGH the exact wrapper + settings, before detaching, so a
# dead credential fails loudly now rather than silently at 3am. Executes claude.
smoke_test() {
  local profile="" settings=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profile="$2"; shift 2 ;;
      --settings) settings="$2"; shift 2 ;;
      *) die "unknown smoke-test argument: $1" ;;
    esac
  done
  [ -f "$profile" ] && [ -f "$settings" ] || die "smoke-test requires --profile and --settings files"
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available (macOS only)"
  local json; json="$(cat "$settings")"
  local claude_bin; claude_bin="$(command -v claude 2>/dev/null)" || die "claude not found on PATH"
  # Match the REAL launch's posture (bypassPermissions + the wrapper + settings) so
  # the smoke validates the same invocation the detached job will run. Assert on
  # observable output, not just $?, so a degraded claude that exits 0 without a live
  # credential can't false-pass the auth gate.
  local out
  out="$(sandbox-exec -f "$profile" "$claude_bin" -p 'ok' --max-turns 1 \
    --permission-mode bypassPermissions --settings "$json" \
    --verbose --output-format stream-json 2>/dev/null)" \
    || die "auth smoke-test failed THROUGH the wrapper — a credential or the jail is wrong; not detaching"
  [ -n "$out" ] || die "auth smoke-test produced no output THROUGH the wrapper — credential/jail suspect; not detaching"
  # Assert the output is actually parseable stream-json (a line beginning with
  # `{` and containing "type"), not just non-empty: a degraded claude could print
  # a plain-text error to stdout and false-pass the emptiness check above.
  printf '%s\n' "$out" | grep -Eq '^\{.*"type"' \
    || die "auth smoke-test produced non-stream-json output THROUGH the wrapper — flag/jail suspect; not detaching"
  echo "spawn-orchestrator: smoke-test OK"
}

detach() {
  local plist=""
  while [ $# -gt 0 ]; do
    case "$1" in --plist) plist="$2"; shift 2 ;; *) die "unknown detach argument: $1" ;; esac
  done
  [ -f "$plist" ] || die "detach requires an existing --plist"
  command -v launchctl >/dev/null 2>&1 || die "launchctl not available (macOS only)"
  launchctl bootstrap "gui/$(id -u)" "$plist" || die "launchctl bootstrap failed for $plist"
  echo "spawn-orchestrator: detached (launchd) $plist"
}

teardown() {
  local label="" done_sentinel="" reason="done"
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --done-sentinel) [ $# -ge 2 ] || die "missing value for --done-sentinel"; done_sentinel="$2"; shift 2 ;;
      --reason) [ $# -ge 2 ] || die "missing value for --reason"; reason="$2"; shift 2 ;;
      *) die "unknown teardown argument: $1" ;;
    esac
  done
  [ -n "$label" ] || die "teardown requires --label"
  _is_terminal_reason "$reason" \
    || die "teardown --reason must be a TERMINAL exit reason (done|systemic|deadline), never one that expects a relaunch (fail-closed): $reason"

  # Write the done-sentinel FIRST (atomically: tmp + mv), THEN boot the job
  # out — so a watcher polling the sentinel never sees "gone but no
  # done-marker" (the ordering is load-bearing, same reasoning as launch()'s
  # smoke-test-before-detach). This is the single completion mechanism;
  # `status` reads the same file, and it carries WHICH terminal reason it stands
  # for (task 15) so `done` and `systemic` stay distinguishable in one file.
  # A sentinel write that FAILS (disk full, read-only run dir) must be LOUD, never
  # a silent skip: `_write_done_sentinel` reports the reason on stderr and returns
  # non-zero, and we fail-closed here rather than boot the job out with no
  # completion marker — the ordering above is the whole point.
  if [ -n "$done_sentinel" ]; then
    _write_done_sentinel "$done_sentinel" "$label" "$reason" \
      || die "teardown ($label): FAILED to write the done-sentinel $done_sentinel — NOT booting the job out (a torn-down job with no completion marker is exactly the 'gone but not done' state the sentinel exists to prevent); fix the run dir and re-run"
  fi

  # launchctl may genuinely be absent (non-macOS, or the in-jail test harness) —
  # that must not skip the sentinel write above; the sentinel is authoritative.
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  fi
  echo "spawn-orchestrator: torn down $label"
}

# VERIFY a teardown actually took, and make a still-loaded job LOUD.
#
# `teardown` swallows bootout failure (`|| true`), and it must — the sentinel is
# authoritative and a missing launchctl is legitimate. But a bootout that FAILS on
# a real macOS host leaves the job loaded, so StartInterval keeps waking a FINISHED
# run: each wake exits 0, re-attempts the same failing teardown, does zero work and
# raises zero signal, while `status` says `relaunch=no`. That is finding #22's
# 52-relaunch loop with a different trigger. So every teardown the SUPERVISOR
# performs (the declared done/deadline path and the halt path alike) re-checks with
# `launchctl print`, retries once, and warns loudly if the job is still there.
# Returns non-zero iff the job is still loaded afterwards.
_verify_bootout() {
  local label="$1"
  command -v launchctl >/dev/null 2>&1 || return 0
  launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 || return 0   # gone: bootout took
  # Subshelled, not just `|| true`: `teardown` `die`s (an `exit`) if a future edit
  # ever hands it a --done-sentinel here, and an `exit` from a same-shell function
  # call escapes `|| true` — it would take the retry, and the STILL-LOADED warning
  # below, down with it. The subshell is what makes `|| true` actually mean
  # best-effort (the `_alarm_safe` pattern, task 16 / #191). Today this call passes
  # no --done-sentinel, so `teardown` cannot yet die here — the subshell is the
  # guard against that ceasing to be true without anyone noticing.
  ( teardown --label "$label" >/dev/null ) || true
  if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    echo "spawn-orchestrator: teardown ($label): WARNING — launchctl bootout FAILED, the job is STILL LOADED and StartInterval will keep waking a finished run (zero work, zero alarm); remove it by hand: launchctl bootout gui/$(id -u)/$label" >&2
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Task 10 — supervisor exit classification (finding #22): an expired OAuth
# credential made the supervisor relaunch into the same 401 ~52 times over
# 4.3 hours, doing zero work and raising zero signal. The supervisor already
# knew "retry later" (the rate-limit backstop, run-budget.md); it needs a
# third bucket — "stop, a human must act" — plus a general backstop for any
# OTHER unclassified repeated failure. All of this is SHELL-level, no model
# call: a rate-limited or auth-dead agent cannot run its own bookkeeping.
# ---------------------------------------------------------------------------

# Unambiguous auth-failure phrases. These are distinctive enough that a literal
# `grep -F` is safe: no transcript says "OAuth token has expired" incidentally.
_AUTH_FAIL_SIGNALS=('authentication_failed' 'Invalid authentication credentials' 'OAuth token has expired')
# A bare `401` is NOT one of them. The classified bytes are a full stream-json
# transcript — model output, tool results, diffs — where `401` shows up in line
# numbers (`foo.py:401`), byte counts, and SHAs. Matching it as a bare substring
# would halt a healthy run with a WRONG diagnosis ("re-authenticate"), which is
# the expensive kind of false positive. So 401 only counts in an auth-ish
# CONTEXT — an HTTP status field or a status line. The motivating run-#2 failure
# line (`401 Invalid authentication credentials`) still matches, via both the
# literal above and the status-line alternative here.
_AUTH_401_RE='("status"[[:space:]]*:[[:space:]]*401([^0-9]|$)|[Ss]tatus[[:space:]]*:[[:space:]]*401([^0-9]|$)|[Ee]rror[[:space:]]*:[[:space:]]*401([^0-9]|$)|(^|[^0-9])401[[:space:]]+(Invalid authentication credentials|Unauthorized))'
_RATE_LIMIT_SIGNALS=('429' 'rate_limit_error' 'overloaded')

# Classify an exit from its code + the bytes the process wrote THIS WAKE, with
# no model call. Prints exactly one of `done` / `fatal: <reason>` /
# `retry: <reason>` and always exits 0 (the classification is the payload).
# Auth is checked BEFORE rate-limit: it is the non-retryable case, and a
# transcript that happens to mention both must still halt, not retry.
#
# --since-offset is load-bearing, not an optimization. The launch script appends
# to one orchestrator.log across every wake, so classifying the WHOLE file makes
# an auth failure STICKY: once any wake emits a 401 the string never leaves the
# log, so every later non-zero exit — including ones after a human has
# re-authenticated and resumed — would re-classify as `fatal` and halt again,
# blaming a credential that is now fine. Reading only this wake's bytes is what
# makes the classification about the CURRENT process. A missing/invalid offset
# falls back to the whole file: over-halting is the safe direction, silently
# relaunching forever is not (that is the bug this task exists to fix).
classify_exit() {
  local code="" outfile="" offset=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --exit-code) [ $# -ge 2 ] || die "missing value for --exit-code"; code="$2"; shift 2 ;;
      --output) [ $# -ge 2 ] || die "missing value for --output"; outfile="$2"; shift 2 ;;
      --since-offset) [ $# -ge 2 ] || die "missing value for --since-offset"; offset="$2"; shift 2 ;;
      *) die "unknown classify-exit argument: $1" ;;
    esac
  done
  [ -n "$code" ] || die "classify-exit requires --exit-code"
  case "$code" in *[!0-9]*) die "--exit-code must be a non-negative integer: $code" ;; esac
  [ -n "$outfile" ] || die "classify-exit requires --output <file>"
  [ -f "$outfile" ] || die "classify-exit --output not found: $outfile"

  if [ "$code" = 0 ]; then
    echo "done"
    return 0
  fi

  # Slice to this wake's bytes. A non-numeric offset is NOT fail-closed — it
  # degrades to the whole file (see above), because refusing to classify at all
  # would leave the supervisor relaunching blind.
  local content
  case "$offset" in
    ''|*[!0-9]*) content="$(cat "$outfile")" ;;
    *) content="$(tail -c "+$((offset + 1))" "$outfile" 2>/dev/null)" || content="$(cat "$outfile")" ;;
  esac

  # Fatal auth signals are matched ONLY on the orchestrator's own error surface,
  # never on transcript CONTENT. In `--output-format stream-json`, stdout is one
  # JSON object per line (every event line starts with `{`), and the process's
  # stderr — merged into the log — is plain prose. So the error surface is: any
  # NON-`{` line (the CLI's own stderr: `API Error: …`, `OAuth token has expired
  # · …`), a stream-json `"type":"error"` event, or a structural JSON
  # `"status":<code>` field. An auth phrase sitting INSIDE a `{`-prefixed event
  # (a tool_result, an assistant message, or the run's own REPORT.md re-read
  # after --resume) is CONTENT — it never qualifies, so a task about auth, or the
  # halt's own reason, can't revive finding #22's loop. (JSON escapes nested
  # quotes, so a `"status"` written into event content arrives as `\"status\"`
  # and matches neither the status nor the content path.)
  local err_lines
  err_lines="$(grep -E '^[^{]|"type"[[:space:]]*:[[:space:]]*"error"|"status"[[:space:]]*:[[:space:]]*[0-9]' <<<"$content" 2>/dev/null)"
  local p
  for p in "${_AUTH_FAIL_SIGNALS[@]}"; do
    if grep -qF -- "$p" <<<"$err_lines"; then
      echo "fatal: non-retryable auth failure ($p)"
      return 0
    fi
  done
  if grep -Eq -- "$_AUTH_401_RE" <<<"$err_lines"; then
    echo "fatal: non-retryable auth failure (401 in an auth context)"
    return 0
  fi
  for p in "${_RATE_LIMIT_SIGNALS[@]}"; do
    if grep -qF -- "$p" <<<"$content"; then
      echo "retry: rate-limit signal ($p)"
      return 0
    fi
  done
  echo "retry: unclassified non-zero exit ($code)"
}

# Pull a bare "key: value" line out of the small supervisor-state file (below).
_supervisor_state_field() {
  local f="$1" key="$2"
  [ -f "$f" ] || return 0
  grep -E "^${key}:" "$f" | head -1 | sed -e "s/^${key}: *//"
}

# Atomically persist the no-progress counter + last-seen run-state HEAD.
_write_supervisor_state() {
  local f="$1" count="$2" head="$3" d; d="$(dirname "$f")"
  mkdir -p "$d" || die "cannot create supervisor-state directory: $d"
  local tmp; tmp="$(mktemp "$d/.supstate.XXXXXX")" || die "mktemp failed"
  { printf 'count: %s\n' "$count"; printf 'head: %s\n' "$head"; } >"$tmp" \
    || { rm -f "$tmp"; die "failed to write supervisor state"; }
  mv "$tmp" "$f" || { rm -f "$tmp"; die "failed to write supervisor state: $f"; }
}

# The run-state branch's current tip, or empty if --dir isn't (yet) a git
# checkout — best-effort, never fail-closed (a missing HEAD just means every
# wake looks like "no progress," which is the safe direction for the guard).
_run_head() {
  ( cd "$1" 2>/dev/null && git rev-parse HEAD 2>/dev/null ) || true
}

# True (exit 0) iff RUN.md's own run-level `status:` is `paused` — a wake
# that lands mid-pause makes no progress BY DESIGN (task 11: the orchestrator
# gates on `paused_until` before invoking claude and exits early), so it must
# never count against the no-progress guard.
_run_is_paused() {
  local run_md="$1/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || return 1
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
  local st; st="$(printf '%s\n' "$front" | grep -E '^status:' | head -1 \
    | sed -e 's/^status: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  [ "$st" = "paused" ]
}

# Set a single front-matter key's value in RUN.md (between the first two
# `---` lines). Fail-closed if the key isn't already declared — every field
# this is used for (`status`, `pause_reason`) is always present per
# run-state.md's `RUN.md` template, so a missing key means the file is not
# the shape this expects, and silently no-op-ing would hide that.
_set_front_field() {
  local f="$1" key="$2" value="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.runmd.XXXXXX")" || die "mktemp failed"
  awk -v key="$key" -v val="$value" '
    /^---$/ { dashes++; print; next }
    dashes==1 && $0 ~ "^" key ":" { print key ": " val; next }
    { print }
  ' "$f" >"$tmp" || { rm -f "$tmp"; die "failed to render $key update for $f"; }
  grep -qE "^${key}: " "$tmp" || { rm -f "$tmp"; die "front-matter key not found (fail-closed): $key in $f"; }
  mv "$tmp" "$f" || { rm -f "$tmp"; die "failed to write $f"; }
}

# ---------------------------------------------------------------------------
# Task 16 — the ALARM (finding #22's real bug): a halted or stalled run must
# ACTIVELY tell a human. Every guard in this system already wrote its finding
# to REPORT.md — a file on a branch nobody reads at 3am — so run #2 relaunched
# into the same non-retryable 401 for 4h14m in complete silence, and was caught
# only because a human happened to ask "how we doing?". The 401 was not the
# bug; the silence was.
#
# WHERE THIS FIRES FROM IS THE WHOLE DESIGN. The jail DENIES exec of
# /usr/bin/osascript, /usr/bin/open and /bin/launchctl (orchestrator.sb.tmpl),
# plus mach-lookup of the launchd/LaunchServices brokers — a deliberate
# sandbox-escape fix that must NOT be relaxed. So an alarm raised from inside
# the agent would be silently denied: the exact failure this exists to prevent,
# wearing a disguise. `write_launch` wraps only `claude` in sandbox-exec, so the
# wrapper shell launchd runs is OUTSIDE the jail — the supervisor is the only
# place a notification can actually reach a human, and it is pure shell (a
# rate-limited or auth-dead orchestrator cannot make a model call to alert
# anyone).
#
# Two entry points, matching the two sides of the jail:
#   alarm          (un-jailed supervisor) notify + mark, idempotently.
#   alarm-request  (jailed agent) drop a request file the supervisor drains on
#                  its next wake — the seam for in-agent detectors (the
#                  invariant doctor, task 14) that CANNOT notify themselves.
# ---------------------------------------------------------------------------

# The one-per-run marker a human (or `status`, or a shell one-liner) can see
# without a model call. Deliberately NOT committed to the run-state branch: it
# is a local, durable "this run screamed" flag, and committing it would feed the
# alarm's own text back into a later wake's classified log.
ALARM_SENTINEL_NAME="ALARM"
# Where a JAILED producer drops an alarm it cannot itself deliver.
ALARM_REQUEST_DIR_NAME="alarm-requests"
# ≥N parked tasks is a park storm: the run is producing a graveyard, not PRs.
PARK_STORM_LIMIT_DEFAULT=3

# The single next action a human must take, per condition. This is the payload
# that matters: #22's fix was 20 seconds of human action gated behind 4 hours of
# silence, so a notification that says only "something broke" reproduces the bug
# at lower latency. Every string names the COMMAND to run.
_alarm_action() {
  case "$1" in
    fatal-auth)
      printf 're-authenticate: run `claude /login`, then `/auto-pilot <source> --resume`' ;;
    no-progress)
      printf 'the run is STALLED (no forward progress, supervisor torn down): check `.auto-pilot/REPORT.md`, fix the cause (usually re-authenticate: `claude /login`), then `/auto-pilot <source> --resume`' ;;
    systemic)
      printf 'the circuit breaker halted the run: read `.auto-pilot/REPORT.md` for the failing tasks, fix the systemic cause, then `/auto-pilot <source> --resume`' ;;
    invariant)
      printf 'a run invariant FAILED: read `.auto-pilot/REPORT.md`, repair the run state by hand, then `/auto-pilot <source> --resume`' ;;
    park-storm)
      printf 'tasks are parking in bulk: read `.auto-pilot/REPORT.md`, unblock them (a broken base, dead `gh` auth, a failing verify), then `/auto-pilot <source> --resume`' ;;
    deadline)
      printf 'the run hit its `--until` deadline with work unfinished: re-launch with a later `--until` (`/auto-pilot <source> --resume`) or accept what landed' ;;
    *)
      printf 'a human must act: read `.auto-pilot/REPORT.md`, fix the condition, then `/auto-pilot <source> --resume`' ;;
  esac
}

# Escape a value for embedding in an AppleScript double-quoted string.
_applescript_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# Emit an OS-level notification. BEST-EFFORT BY CONTRACT: a missing, failing, or
# (in-jail) exec-DENIED notifier must never abort the halt that is calling us —
# the durable ALARM marker + REPORT.md line are written BEFORE this runs, so the
# alarm survives a dead notifier. Always returns 0.
_alarm_notify() {
  local title="$1" msg="$2"
  if command -v osascript >/dev/null 2>&1; then
    if osascript -e "display notification \"$(_applescript_escape "$msg")\" with title \"$(_applescript_escape "$title")\"" >/dev/null 2>&1; then
      return 0
    fi
  fi
  if command -v terminal-notifier >/dev/null 2>&1; then
    if terminal-notifier -title "$title" -message "$msg" >/dev/null 2>&1; then
      return 0
    fi
  fi
  echo "spawn-orchestrator: alarm NOTIFY FAILED (no osascript/terminal-notifier, or it was denied) — the ALARM marker and REPORT.md line are still written" >&2
  return 0
}

# Raise the alarm for one condition, ONCE per run. Writes the ALARM sentinel and
# a one-line reason at the VERY TOP of REPORT.md, then notifies. Idempotency
# lives in the sentinel file itself (not in memory, not in the supervisor-state
# scratch): the supervisor is a fresh process on every 300s wake, so a
# process-local guard would re-notify every wake and make the alarm the new noise.
alarm() {
  local dir="" label="" condition="" reason="" action=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --condition) [ $# -ge 2 ] || die "missing value for --condition"; condition="$2"; shift 2 ;;
      --reason) [ $# -ge 2 ] || die "missing value for --reason"; reason="$2"; shift 2 ;;
      --action) [ $# -ge 2 ] || die "missing value for --action"; action="$2"; shift 2 ;;
      *) die "unknown alarm argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$condition" ] && [ -n "$reason" ] \
    || die "alarm requires --dir, --condition, and --reason"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  [ -d "$dir" ] || die "alarm --dir not found: $dir"
  # The condition id is the idempotency key AND a grep anchor — pin its charset.
  case "$condition" in *[!A-Za-z0-9._-]*) die "--condition must be [A-Za-z0-9._-] (fail-closed): $condition" ;; esac

  local ap="$dir/.auto-pilot"
  # Past argument validation, NOTHING in here may `die`: `die` is an `exit`, and
  # an exit from the alarm would take its CALLER — the halt — down with it, before
  # the run-state commit and before the launchd teardown (the one thing that MUST
  # happen). A broken durable channel degrades to the remaining ones; it never
  # aborts the halt. The call sites subshell us as well, for the validation `die`s.
  mkdir -p "$ap" 2>/dev/null \
    || echo "spawn-orchestrator: alarm: cannot create $ap — the notification still fires, the durable record may not" >&2
  local sentinel="$ap/$ALARM_SENTINEL_NAME"

  if [ -f "$sentinel" ] && grep -qxF "condition: $condition" "$sentinel"; then
    echo "spawn-orchestrator: alarm $condition already raised for this run (idempotent no-op)"
    return 0
  fi

  [ -n "$action" ] || action="$(_alarm_action "$condition")"
  local ts; ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  # 1. The sentinel — the shell-visible marker. Append: a run can raise more than
  #    one distinct condition, and each must stay greppable.
  {
    printf 'condition: %s\n' "$condition"
    printf 'at: %s\n' "$ts"
    printf 'run: %s\n' "${label:-(unlabelled)}"
    printf 'reason: %s\n' "$reason"
    printf 'action: %s\n' "$action"
    printf '\n'
  } >>"$sentinel" 2>/dev/null \
    || echo "spawn-orchestrator: alarm: cannot write the ALARM sentinel ($sentinel) — the REPORT.md line and the notification still fire, but this alarm is NOT idempotent without it" >&2

  # 2. REPORT.md's VERY FIRST LINE. The human wakes up to this file; an alarm
  #    appended at the bottom, under a night of task sections, is not an alarm.
  local report="$ap/REPORT.md"
  local line
  line="$(printf '> **ALARM (%s) — %s:** %s **ACTION: %s**' "$ts" "$condition" "$reason" "$action")"
  # The `if` (not `[ -f … ] && cat`) is deliberate: a missing REPORT.md would
  # make the group exit non-zero and abort an otherwise-successful prepend.
  local rtmp
  if rtmp="$(mktemp "$ap/.report.XXXXXX" 2>/dev/null)"; then
    {
      printf '%s\n\n' "$line"
      if [ -f "$report" ]; then cat "$report"; fi
    } >"$rtmp"
    if ! mv "$rtmp" "$report"; then
      rm -f "$rtmp"
      echo "spawn-orchestrator: alarm: failed to prepend the ALARM line to $report" >&2
    fi
  else
    echo "spawn-orchestrator: alarm: mktemp failed, could not prepend the ALARM line to $report" >&2
  fi

  # 3. The active notification — LAST, so a dead/denied notifier can never cost
  #    us the durable record above.
  _alarm_notify "auto-pilot ALARM — ${label:-run}" "$condition: $reason ACTION: $action"

  echo "spawn-orchestrator: ALARM $condition — $reason"
}

# (JAILED side) Drop an alarm the agent cannot deliver itself. The in-jail
# orchestrator can write files but CANNOT exec osascript, so a detector that runs
# inside the agent (the invariant doctor, the circuit breaker) records the
# condition here and the un-jailed supervisor delivers it on its next wake.
# One file per condition: a re-request before the drain overwrites, never piles up.
alarm_request() {
  local dir="" condition="" reason="" action=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --condition) [ $# -ge 2 ] || die "missing value for --condition"; condition="$2"; shift 2 ;;
      --reason) [ $# -ge 2 ] || die "missing value for --reason"; reason="$2"; shift 2 ;;
      --action) [ $# -ge 2 ] || die "missing value for --action"; action="$2"; shift 2 ;;
      *) die "unknown alarm-request argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$condition" ] && [ -n "$reason" ] \
    || die "alarm-request requires --dir, --condition, and --reason"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  [ -d "$dir" ] || die "alarm-request --dir not found: $dir"
  case "$condition" in *[!A-Za-z0-9._-]*) die "--condition must be [A-Za-z0-9._-] (fail-closed): $condition" ;; esac
  # A newline would forge extra key lines the drain reads back as its own fields.
  case "$reason$action" in *$'\n'*) die "alarm-request --reason/--action must not contain a newline (fail-closed)" ;; esac

  local rd="$dir/.auto-pilot/$ALARM_REQUEST_DIR_NAME"
  mkdir -p "$rd" || die "cannot create the alarm-request dir: $rd"
  local tmp; tmp="$(mktemp "$rd/.req.XXXXXX")" || die "mktemp failed"
  {
    printf 'condition: %s\n' "$condition"
    printf 'reason: %s\n' "$reason"
    printf 'action: %s\n' "$action"
  } >"$tmp" || { rm -f "$tmp"; die "cannot write the alarm request"; }
  mv "$tmp" "$rd/$condition.alarm" || { rm -f "$tmp"; die "cannot place the alarm request: $rd/$condition.alarm"; }
  echo "spawn-orchestrator: alarm-request $condition (the supervisor will deliver it on its next wake)"
}

# Retire this run's alarms — `--resume` calls it, FIRST, before it reconciles
# anything (SKILL.md "Resume phase"). The sentinel is the per-run idempotency key,
# and every alarm's own ACTION text ends "…then `/auto-pilot <source> --resume`":
# so a sentinel that survives the resume SUPPRESSES the next alarm for the same
# condition — a token that expires again, a base that breaks again — and the
# resumed run halts in exactly the silence this whole mechanism exists to end.
# The alarms describe the run the human just repaired; they do not carry forward.
# Undelivered in-jail requests go with them, for the same reason.
alarm_clear() {
  local dir=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      *) die "unknown alarm-clear argument: $1" ;;
    esac
  done
  [ -n "$dir" ] || die "alarm-clear requires --dir"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  [ -d "$dir" ] || die "alarm-clear --dir not found: $dir"

  local ap="$dir/.auto-pilot"
  rm -f "$ap/$ALARM_SENTINEL_NAME"
  rm -rf "${ap:?}/$ALARM_REQUEST_DIR_NAME"
  # The REPORT.md alarm lines STAY: they are the history of what went wrong, which
  # is exactly what a human reads after a resume. Only the idempotency key resets.
  echo "spawn-orchestrator: alarms cleared for $dir (REPORT.md history kept)"
}

# EVERY internal caller raises the alarm through this, never `alarm` directly.
# `alarm`'s argument validation is `die`, i.e. an `exit`, and `alarm foo || true`
# does NOT contain an `exit` — it would take the whole supervisor process down
# mid-halt, before the run-state commit and before the launchd teardown, leaving a
# `systemic` RUN.md next to a still-loaded job (finding #22's relaunch loop, with
# the halt message as camouflage). The subshell contains it: a malformed alarm
# costs us the notification, never the halt. Callers that WANT the exit (the CLI
# subcommand, a fail-closed operator invocation) still call `alarm` directly.
_alarm_safe() {
  ( alarm "$@" ) || echo "spawn-orchestrator: alarm failed (the halt/teardown continues regardless)" >&2
  return 0
}

# (UN-JAILED side) Deliver every pending in-jail alarm request. Each delivered
# request is removed; `alarm` itself is idempotent, so a request re-dropped after
# delivery re-notifies nothing.
_alarm_drain_requests() {
  local dir="$1" label="$2"
  local rd="$dir/.auto-pilot/$ALARM_REQUEST_DIR_NAME"
  [ -d "$rd" ] || return 0
  local f
  for f in "$rd"/*.alarm; do
    [ -e "$f" ] || continue
    local condition reason action
    condition="$(sed -n 's/^condition: //p' "$f" | head -1)"
    reason="$(sed -n 's/^reason: //p' "$f" | head -1)"
    action="$(sed -n 's/^action: //p' "$f" | head -1)"
    rm -f "$f"
    [ -n "$condition" ] && [ -n "$reason" ] || continue
    # _alarm_safe, not `alarm`: the request's fields come from INSIDE the jail, so
    # a malformed condition id is an agent-side bug, not a reason for the
    # supervisor to exit mid-wake.
    if [ -n "$action" ]; then
      _alarm_safe --dir "$dir" --label "$label" --condition "$condition" --reason "$reason" --action "$action"
    else
      _alarm_safe --dir "$dir" --label "$label" --condition "$condition" --reason "$reason"
    fi
  done
}

# True (0) iff this run has ALREADY raised any alarm.
_alarm_raised() {
  local sentinel="$1/.auto-pilot/$ALARM_SENTINEL_NAME"
  [ -f "$sentinel" ] || return 1
  grep -q '^condition: ' "$sentinel"
}

# Read one front-matter field out of a RUN.md (same shape as status()'s
# _front_field, but takes the file — the alarm scan runs where no `front` global
# has been captured).
_run_md_field() {
  local f="$1" key="$2"
  [ -f "$f" ] || return 0
  awk '/^---$/{c++; next} c==1{print}' "$f" | grep -E "^${key}:" | head -1 \
    | sed -e "s/^${key}: *//" -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//' \
          -e "s/^'\(.*\)'\$/\1/" -e 's/^"\(.*\)"$/\1/'
}

# How many tasks are in the terminal `parked` phase (column 2 of RUN.md's table).
_run_md_parked_count() {
  local f="$1"
  [ -f "$f" ] || { echo 0; return 0; }
  awk -F'|' '/^\|/ { p=$3; gsub(/^[[:space:]]+|[[:space:]]+$/, "", p); if (p == "parked") n++ } END { print n+0 }' "$f"
}

# True (0) iff the run's `--until` deadline is in the past. `until` is a local
# ISO-8601 timestamp (run-state.md), and so is `date +%Y-%m-%dT%H:%M:%S`, so a
# LEXICOGRAPHIC compare is a correct time compare — no date-parsing dependency
# (`date -d` / `date -j` differ across platforms, and a supervisor that can't
# parse a date must not be the reason nobody gets told). A value that isn't
# ISO-shaped is not "blown", it's unreadable — never alarm on garbage.
_deadline_blown() {
  local u="${1%Z}"
  case "$u" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]*) ;;
    *) return 1 ;;
  esac
  local now; now="$(date +%Y-%m-%dT%H:%M:%S)"
  [ "$now" \> "$u" ]
}

# Set by _supervisor_alarm_scan when the condition it found is TERMINAL (the run
# must stop, not just be reported). Read by supervisor_check.
_ALARM_HALT_REASON=""
_ALARM_HALT_CONDITION=""

# Every alarm condition the SUPERVISOR can detect on its own, in shell, with no
# model call — run on EVERY wake, before the exit is even classified. This is the
# "escalate on a STALL, not only on a halt" half of the task: #22 never reached a
# halt state at all; RUN.md looked healthy and the run did nothing.
#
#   in-jail requests  a detector inside the agent (invariant doctor) asking us to
#                     deliver an alarm it is sandbox-denied from delivering.
#   systemic          the agent's own circuit breaker halted the run.       (terminal)
#   deadline          the run blew its --until without finishing.           (terminal)
#   park-storm        ≥N tasks parked — a graveyard, not a set of PRs.      (report only:
#                     the remaining tasks may still deliver; the circuit breaker
#                     owns the decision to stop.)
_supervisor_alarm_scan() {
  local dir="$1" label="$2" park_limit="$3"
  _ALARM_HALT_REASON=""; _ALARM_HALT_CONDITION=""

  _alarm_drain_requests "$dir" "$label"

  local ap="$dir/.auto-pilot" run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || return 0

  local st reason until_val
  st="$(_run_md_field "$run_md" status)"
  reason="$(_run_md_field "$run_md" pause_reason)"
  until_val="$(_run_md_field "$run_md" until)"

  # A finished run is not an alarm — neither its (past) deadline nor its parks.
  [ "$st" = "done" ] && return 0
  [ -f "$ap/$DONE_SENTINEL_NAME" ] && return 0

  if [ "$st" = "systemic" ]; then
    # `status: systemic` has TWO writers: the agent's circuit breaker (a genuine,
    # unreported condition) and OUR OWN halt, one wake earlier (which already
    # notified under its real name — fatal-auth, no-progress, deadline). Only the
    # first deserves a notification: re-alarming a run we already screamed about,
    # under a second name, is exactly the per-wake noise that makes the next real
    # alarm ignorable. So if this run has already alarmed, halt again SILENTLY
    # (an empty halt condition skips the alarm) — the teardown still re-runs,
    # which is the point of reaching this path at all (a bootout that didn't take).
    if _alarm_raised "$dir"; then
      _ALARM_HALT_CONDITION=""
      # Verbatim, NOT re-wrapped: the halt writes this back to `pause_reason`, so
      # re-decorating the string we ourselves wrote would nest it one layer deeper
      # on every wake. Passing it through makes the re-halt a fixed point.
      _ALARM_HALT_REASON="${reason:-status: systemic}"
      return 0
    fi
    _alarm_safe --dir "$dir" --label "$label" --condition systemic \
      --reason "the run's circuit breaker halted it (${reason:-status: systemic})"
    _ALARM_HALT_CONDITION="systemic"
    _ALARM_HALT_REASON="circuit breaker halt (${reason:-status: systemic})"
    return 0
  fi

  if [ -n "$until_val" ] && _deadline_blown "$until_val"; then
    _alarm_safe --dir "$dir" --label "$label" --condition deadline \
      --reason "the run blew its --until deadline ($until_val) without finishing"
    _ALARM_HALT_CONDITION="deadline"
    _ALARM_HALT_REASON="blew the --until deadline ($until_val) without finishing"
    return 0
  fi

  local parked; parked="$(_run_md_parked_count "$run_md")"
  if [ "$parked" -ge "$park_limit" ]; then
    _alarm_safe --dir "$dir" --label "$label" --condition park-storm \
      --reason "$parked tasks are parked (limit $park_limit) — the run is filling a graveyard, not landing PRs"
  fi
}

# Same, but INSERTS the key (just before the front matter's closing `---`) when
# it isn't declared yet. The exit-reason fields are new (task 15), so a run whose
# RUN.md was written by an older launch has no `exit_reason:` line — and refusing
# to declare a reason there is the wrong trade: a run that cannot say why it
# stopped is exactly the state this task exists to abolish. A file with no front
# matter at all is still fail-closed (that is a malformed RUN.md, not an old one).
_upsert_front_field() {
  local f="$1" key="$2" value="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.runmd.XXXXXX")" || die "mktemp failed"
  awk -v key="$key" -v val="$value" '
    /^---$/ {
      dashes++
      if (dashes == 2 && !written) { print key ": " val; written = 1 }
      print; next
    }
    dashes==1 && $0 ~ "^" key ":" { print key ": " val; written = 1; next }
    { print }
  ' "$f" >"$tmp" || { rm -f "$tmp"; die "failed to render $key update for $f"; }
  grep -qE "^${key}: " "$tmp" || { rm -f "$tmp"; die "no front matter to write $key into (fail-closed): $f"; }
  mv "$tmp" "$f" || { rm -f "$tmp"; die "failed to write $f"; }
}

# ---------------------------------------------------------------------------
# Task 15 — the exit contract. "I finished the run" and "I ran out of context
# mid-task" were the same observable event (exit 0, terminal_reason: completed),
# so every downstream consumer was guessing: the supervisor relaunched on a blind
# timer because it could not tell them apart, and a human reading `exit code = 0`
# could not tell whether the run was done or dead. The orchestrator now DECLARES
# its reason on the run-state branch before exiting, and the supervisor reads it.
# ---------------------------------------------------------------------------

# Atomically write the done-sentinel. ONE file (`orchestrator.done`) for all three
# terminal reasons — the plan requires the done-sentinel and the launchd relaunch
# sentinel be the SAME file, so a `systemic` halt must drop it too, or a
# KeepAlive/PathState supervisor would happily relaunch a halted run. The reason
# it carries is what keeps that single file from flattening `done` and `systemic`
# back into one indistinguishable state. Args: <path> <label> <reason>
#
# It RETURNS non-zero (loudly, on stderr) rather than `die`ing: it is called from
# `teardown`, which is a plain function call inside `supervisor_check` — a `die`
# here would `exit 2` out of the supervisor BEFORE the bootout, so a full disk or a
# read-only run dir would silently produce no teardown at all. The caller decides.
_write_done_sentinel() {
  local f="$1" label="$2" reason="$3"
  case "$f" in
    /*) ;;
    *) echo "spawn-orchestrator: done-sentinel path must be absolute (fail-closed): $f" >&2; return 1 ;;
  esac
  local sdir; sdir="$(dirname "$f")"
  mkdir -p "$sdir" || { echo "spawn-orchestrator: failed to create sentinel directory: $sdir" >&2; return 1; }
  # The temp file goes in the sentinel's OWN directory so the `mv` is a
  # same-filesystem atomic rename — a $TMPDIR temp could be on another filesystem,
  # making `mv` a non-atomic copy a watcher could observe half-written.
  local tmp; tmp="$(mktemp "$sdir/.orchestrator-done.XXXXXX")" \
    || { echo "spawn-orchestrator: mktemp failed under $sdir (disk full? read-only?)" >&2; return 1; }
  { printf '%s %s %s\n' "$label" "$reason" "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'reason: %s\n' "$reason"
  } >"$tmp" || { rm -f "$tmp"; echo "spawn-orchestrator: failed to write done sentinel" >&2; return 1; }
  mv "$tmp" "$f" || { rm -f "$tmp"; echo "spawn-orchestrator: failed to write done sentinel: $f" >&2; return 1; }
}

# (agent side, in-jail) Declare WHY this orchestrator is exiting, before it exits.
# Writes `exit_reason` + `exit_reason_at` (epoch) into RUN.md's front matter and
# commits them to the run-state branch — durable, on the branch, per the task's
# requirement that the reason outlive the process. For a TERMINAL reason it also
# drops the done-sentinel, so a watcher polling that file sees the run stop even
# before the supervisor's next wake.
#
# It never calls launchctl: the orchestrator is inside the Seatbelt jail, where
# exec of launchctl is denied by construction (that deny is the jail's whole
# escape wall). Booting the job out is the SUPERVISOR's job — it runs outside the
# jail, reads this declaration, and acts (supervisor_check below).
exit_reason() {
  local dir="" reason="" label="auto-pilot" detail=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --reason) [ $# -ge 2 ] || die "missing value for --reason"; reason="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --detail) [ $# -ge 2 ] || die "missing value for --detail"; detail="$2"; shift 2 ;;
      *) die "unknown exit-reason argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$reason" ] || die "exit-reason requires --dir and --reason"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  _is_exit_reason "$reason" \
    || die "unknown exit reason (fail-closed): $reason — must be one of continuing|paused|done|systemic|deadline"
  local run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"

  # Front matter is line-oriented: a multi-line detail would corrupt it (and could
  # smuggle its own key). Flatten to one line rather than fail a legitimate exit.
  detail="$(printf '%s' "$detail" | tr '\n' ' ')"

  _upsert_front_field "$run_md" exit_reason "$reason"
  _upsert_front_field "$run_md" exit_reason_at "$(date +%s)"
  # Written UNCONDITIONALLY (empty when no --detail), so a previous exit's detail is
  # overwritten rather than left to be read back as this one's diagnosis.
  _upsert_front_field "$run_md" exit_reason_detail "$detail"

  # Best-effort commit, same posture as the supervisor halt: a broken git checkout
  # must not stop the orchestrator from exiting, and the on-disk RUN.md is still
  # what the supervisor reads on this wake.
  ( cd "$dir" \
    && git add -- .auto-pilot/RUN.md 2>/dev/null \
    && git -c user.name="auto-pilot" -c user.email="auto-pilot@localhost" \
           commit -q -m "auto-pilot: exit reason $reason" \
  ) 2>/dev/null || echo "spawn-orchestrator: exit-reason: run-state commit failed (not a git checkout, or nothing to commit)" >&2

  local sentinel="$dir/.auto-pilot/$DONE_SENTINEL_NAME"
  if _is_terminal_reason "$reason"; then
    _write_done_sentinel "$sentinel" "$label" "$reason" \
      || die "exit-reason: declared $reason but FAILED to write the done-sentinel $sentinel — the run would read back as still running; fix the run dir and re-declare"
  else
    # A RELAUNCHABLE reason must CLEAR any pre-existing done-sentinel. The sentinel
    # is durable and nothing else in the run removes it, so a run that once declared
    # `done`/`deadline` (or was torn down and then --resume'd) would otherwise carry
    # "the run is over" on disk while actively declaring "relaunch me": `status`
    # would report `relaunch=no` for a live run, and the PathState watcher
    # launch-runtime.md blesses would treat it as complete.
    rm -f "$sentinel" \
      || die "exit-reason: declared $reason but could not remove the stale done-sentinel $sentinel — a live run must not carry a completion marker"
  fi
  echo "spawn-orchestrator: exit reason $reason${detail:+ ($detail)}"
}

# (resume side) Clear the terminal exit state so a resumed run does not start life
# already marked finished. `--resume` calls this BEFORE the first wake.
#
# The exit contract is durable by design — the reason is committed to the run-state
# branch and the done-sentinel is a file — and `deadline` is BY DEFINITION the
# reason whose recovery is an explicit `--resume`. Nothing else in the repo clears
# either, so without this a resumed run keeps reading back as `done`/`deadline`:
# `status` says `relaunch=no`, and a supervisor wake could tear the live run down.
clear_exit_state() {
  local dir=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      *) die "unknown clear-exit-state argument: $1" ;;
    esac
  done
  [ -n "$dir" ] || die "clear-exit-state requires --dir"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  local run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"

  local sentinel="$dir/.auto-pilot/$DONE_SENTINEL_NAME"
  rm -f "$sentinel" || die "clear-exit-state: could not remove the done-sentinel (fail-closed): $sentinel"
  _upsert_front_field "$run_md" exit_reason ""
  _upsert_front_field "$run_md" exit_reason_at ""
  _upsert_front_field "$run_md" exit_reason_detail ""

  ( cd "$dir" \
    && git add -- .auto-pilot/RUN.md 2>/dev/null \
    && git -c user.name="auto-pilot" -c user.email="auto-pilot@localhost" \
           commit -q -m "auto-pilot: clear exit state (resume)" \
  ) 2>/dev/null || echo "spawn-orchestrator: clear-exit-state: run-state commit failed (not a git checkout, or nothing to commit)" >&2

  echo "spawn-orchestrator: exit state cleared (done-sentinel removed, exit_reason blanked) $dir"
}

# (agent side) Touch the heartbeat. Called at each loop iteration and each
# /deliver-task sub-step boundary, and by the launch wrapper at the top of every
# wake. This is the only signal that can separate SLOW from WEDGED: without it a
# 40-minute silence and a hung process are the same observation.
heartbeat() {
  local dir="" note=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --note) [ $# -ge 2 ] || die "missing value for --note"; note="$2"; shift 2 ;;
      *) die "unknown heartbeat argument: $1" ;;
    esac
  done
  [ -n "$dir" ] || die "heartbeat requires --dir"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  local d="$dir/.auto-pilot"
  mkdir -p "$d" || die "cannot create run-state directory: $d"
  note="$(printf '%s' "${note:-beat}" | tr '\n' ' ')"
  local now iso; now="$(date +%s)"; iso="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  local tmp; tmp="$(mktemp "$d/.heartbeat.XXXXXX")" || die "mktemp failed"
  { printf 'at: %s\n' "$now"; printf 'iso: %s\n' "$iso"; printf 'note: %s\n' "$note"; } >"$tmp" \
    || { rm -f "$tmp"; die "failed to write heartbeat"; }
  mv "$tmp" "$d/$HEARTBEAT_NAME" || { rm -f "$tmp"; die "failed to write heartbeat: $d/$HEARTBEAT_NAME"; }
  echo "spawn-orchestrator: heartbeat $iso ($note)"
}

# (supervisor side) The exit reason THIS wake declared, or empty.
#
# Empty is the fail-SAFE answer and it is deliberate: an unknown, missing, or
# garbage reason must never tear a live run down, so it falls back to the existing
# inference path (classify-exit) rather than inventing a decision.
#
# Freshness is load-bearing, not hygiene. The declaration lives on the run-state
# branch, so it OUTLIVES the wake that wrote it. Without the timestamp check, a
# wake that declared `continuing` and was then hard-killed on the next wake by a
# dead credential would have its stale `continuing` out-vote the fatal auth
# classification — reviving finding #22's 52-relaunch loop through a durable file.
# So a declaration counts only if it was written at or after this wake started.
#
# And "I cannot tell when this wake started" is NOT a licence to trust the
# declaration — it is the same unknowable as an undatable declaration, and it fails
# CLOSED (honor no declaration, fall back to inference). The trigger is real: the
# generated wrapper computes `wake=$(date +%s)` under the plist's NARROWED PATH, so
# a `date` it cannot reach leaves `wake` empty; trusting the declaration then would
# let a RUN.md carrying `exit_reason: done` from 1970 tear down a live run.
# Args: <run-dir> <wake-start-epoch>
_declared_exit_reason() {
  local dir="$1" wake="${2:-}"
  case "$wake" in ''|*[!0-9]*) return 0 ;; esac
  local run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || return 0
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
  local r; r="$(printf '%s\n' "$front" | grep -E '^exit_reason:' | head -1 \
    | sed -e 's/^exit_reason: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  _is_exit_reason "$r" || return 0
  local at; at="$(printf '%s\n' "$front" | grep -E '^exit_reason_at:' | head -1 \
    | sed -e 's/^exit_reason_at: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  # An undatable declaration can't be attributed to a wake, so it isn't trusted.
  case "$at" in ''|*[!0-9]*) return 0 ;; esac
  [ "$at" -ge "$wake" ] || return 0
  printf '%s' "$r"
}

# Read one front-matter field out of a run dir's RUN.md (supervisor side). The
# `status` reader's own _front_field closes over a pre-extracted `front` variable;
# this one takes the dir. Trailing whitespace is trimmed, but a `#` is NOT treated
# as a comment: `pause_reason` / `exit_reason_detail` are free prose and may contain
# one, and truncating an operator's diagnosis at a `#` would be its own data loss.
_run_front_field() {
  local run_md="$1/.auto-pilot/RUN.md" key="$2"
  [ -f "$run_md" ] || return 0
  awk '/^---$/{c++; next} c==1{print}' "$run_md" | grep -E "^${key}:" | head -1 \
    | sed -e "s/^${key}: *//" -e 's/[[:space:]]*$//'
}

# The halt itself: write run-level `status: systemic` + `pause_reason` to
# RUN.md, append one alarm entry to REPORT.md, raise the ALARM (task 16 —
# notify a human, once per condition), commit both to the run-state
# branch (best-effort — a broken git checkout must not block tearing the
# supervisor down, which is the one thing that MUST happen), then boot the
# launchd job out so it never relaunches into the same condition.
#
# `--label` is OPTIONAL (task 14 / doctor): `doctor` can run from a bare
# `--resume` before any launchd job is registered (no label to tear down yet).
# When `--label` is absent, every other halt step still runs — RUN.md +
# REPORT.md are still written and committed, the run is still `systemic` — the
# teardown step is simply skipped, since there is nothing loaded to boot out.
_supervisor_halt() {
  local dir="" label="" reason="" condition="halt" preserve=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) dir="$2"; shift 2 ;;
      --label) label="$2"; shift 2 ;;
      --reason) reason="$2"; shift 2 ;;
      # The alarm's idempotency key. A halt that FOLLOWS a scan-raised alarm
      # passes the SAME condition, so `alarm` no-ops instead of double-notifying
      # — the idempotency is the de-dupe, no second flag needed.
      --condition) condition="$2"; shift 2 ;;
      # ONLY the declared-`systemic` caller passes this. See below.
      --preserve-pause-reason) preserve=1; shift ;;
      *) die "unknown supervisor-halt argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$reason" ] \
    || die "supervisor-halt requires --dir and --reason"

  local run_md="$dir/.auto-pilot/RUN.md" report_md="$dir/.auto-pilot/REPORT.md" ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if [ -f "$run_md" ]; then
    # Every front-matter write below runs in a SUBSHELL so a `die` from a MISSING
    # or malformed front matter only aborts that ONE write (canonicalize()'s
    # $()-subshell pattern) — best-effort, matching this function's own "a broken
    # … must not block tearing the supervisor down" comment below. This is
    # load-bearing for doctor's invariant 2 (task 14): that halt fires PRECISELY
    # when RUN.md's front matter doesn't parse, so `status`/`pause_reason`/
    # `exit_reason` may not even be declared keys to write — and _set_front_field /
    # _upsert_front_field both `die` (i.e. `exit`) in that case, which would take
    # the halt down with them, before the ALARM and before the teardown. The
    # REPORT.md alarm + the notification below are then the records that must not
    # be lost.
    ( _set_front_field "$run_md" status systemic ) \
      || echo "spawn-orchestrator: supervisor halt: could not write status: systemic (RUN.md front matter missing/malformed) — relying on the REPORT.md alarm" >&2
    # `pause_reason` must name the TRUE cause of THIS halt. On the declared-`systemic`
    # path only, the orchestrator has already recorded its OWN concrete diagnosis
    # there (and the halt reason is derived FROM it), so overwriting it with the
    # supervisor's summary would destroy the only description of the actual cause,
    # leaving a human woken by an alarm that says "see RUN.md pause_reason" while
    # pause_reason says the same thing back — hence --preserve-pause-reason, passed
    # from that ONE caller.
    #
    # The other two halt paths (fatal auth, no-progress) must NOT preserve: they have
    # their own, true reason, and whatever sits in `pause_reason` is someone else's —
    # a stale rate-window pause, or a previous halt (`--resume` clears `status` and
    # `paused_until`, but not `pause_reason`). Preserving it would make RUN.md assert
    # a FALSE cause: "halted systemic — rate window until 03:00" on a run that
    # actually died on a dead credential, pointing the operator at the wrong thing.
    local existing_pause; existing_pause=""
    [ "$preserve" = 1 ] && existing_pause="$(_run_front_field "$dir" pause_reason)"
    # A `pause_reason` that is only the template's inline `# …` doc comment is not a
    # diagnosis to preserve — keeping it would leave RUN.md asserting "why the run
    # paused/halted" as the cause of the halt. Write the real reason instead.
    case "$existing_pause" in '#'*) existing_pause="" ;; esac
    if [ -n "$existing_pause" ]; then
      echo "spawn-orchestrator: supervisor halt: preserving the run's own pause_reason: $existing_pause" >&2
    else
      ( _set_front_field "$run_md" pause_reason "$reason" ) \
        || echo "spawn-orchestrator: supervisor halt: could not write pause_reason (RUN.md front matter missing/malformed)" >&2
    fi
    # The halt IS an exit reason (task 15): a run that halted must read back as
    # `systemic`, not as a run whose last declaration happened to be `continuing`.
    ( _upsert_front_field "$run_md" exit_reason systemic ) \
      || echo "spawn-orchestrator: supervisor halt: could not write exit_reason: systemic (RUN.md front matter missing/malformed)" >&2
    ( _upsert_front_field "$run_md" exit_reason_at "$(date +%s)" ) \
      || echo "spawn-orchestrator: supervisor halt: could not write exit_reason_at (RUN.md front matter missing/malformed)" >&2
  else
    echo "spawn-orchestrator: supervisor halt: no RUN.md at $run_md, skipping run-state write" >&2
  fi

  {
    printf '\n## ALARM — supervisor halt (%s)\n\n' "$ts"
    printf -- '- **Reason:** %s\n' "$reason"
    printf -- '- **Action required:** a human must resolve this before the run can continue; the supervisor has torn itself down and will NOT relaunch on its own. Re-authenticate (or fix the underlying condition), then `--resume`.\n'
  } >>"$report_md" 2>/dev/null || echo "spawn-orchestrator: supervisor halt: failed to append $report_md" >&2

  # ACTIVELY tell a human (task 16). This runs BEFORE the commit and the
  # teardown: the notification is the point of the halt, and a broken git
  # checkout or a wedged `launchctl` must never be the reason nobody is told.
  # `alarm` is idempotent per condition, so a relaunch into the same condition
  # (a bootout that didn't take) re-halts silently instead of re-notifying.
  # An EMPTY --condition means "this halt was already announced under its real
  # name" (see _supervisor_alarm_scan's systemic branch) — halt, don't re-notify.
  # _alarm_safe, never `alarm`: an alarm that cannot write its own sentinel must
  # not be the reason the job is left loaded and relaunching (see _alarm_safe).
  if [ -n "$condition" ]; then
    _alarm_safe --dir "$dir" --label "$label" --condition "$condition" --reason "$reason"
  fi

  if [ -f "$run_md" ] || [ -f "$report_md" ]; then
    ( cd "$dir" \
      && git add -- .auto-pilot/RUN.md .auto-pilot/REPORT.md 2>/dev/null \
      && git -c user.name="auto-pilot-supervisor" -c user.email="auto-pilot@localhost" \
             commit -q -m "auto-pilot: supervisor halt — $reason" \
    ) 2>/dev/null || echo "spawn-orchestrator: supervisor halt: run-state commit failed (not a git checkout, or nothing to commit)" >&2
  fi

  # The done-sentinel is ALSO the launchd relaunch sentinel (one file, by design —
  # launch-runtime.md "Logs / observability"), so a halted run must drop it too:
  # a KeepAlive/PathState supervisor gating on its absence would otherwise relaunch
  # straight back into the condition we just halted for.
  # stderr is NOT suppressed: a sentinel write that fails here is the difference
  # between a halted run and a run that only LOOKS halted.
  #
  # `--label` is OPTIONAL (task 14 / doctor), and the guard is load-bearing:
  # `teardown` REQUIRES --label and `die`s (i.e. `exit`s) without one, so calling
  # it unconditionally would take a label-less doctor halt down with exit 2 —
  # losing doctor's own exit 30 (HALT) contract — right after we told the human.
  # A label-less halt has no launchd job registered yet (a bare `--resume`), so
  # there is nothing loaded to boot out and no relaunch to sentinel against; every
  # other halt step above (RUN.md, REPORT.md, the ALARM, the commit) already ran.
  if [ -n "$label" ]; then
    # Subshelled: `teardown` `die`s (an `exit`) if `_write_done_sentinel` fails —
    # e.g. an unwritable run dir — and that `exit` is in THIS shell, so a bare
    # `teardown ... || true` would NOT contain it (`|| true` never sees an `exit`,
    # it only sees a nonzero RETURN). Unguarded, that exit would abort the whole
    # halt right here, before `_verify_bootout` below ever runs — losing the loud
    # "bootout FAILED, job STILL LOADED" warning, which is precisely the signal
    # that prevents finding #22's relaunch loop. Subshelling (the `_alarm_safe`
    # pattern, task 16 / #191) confines the exit to the subshell so the halt keeps
    # going regardless.
    if ! ( teardown --label "$label" --done-sentinel "$dir/.auto-pilot/$DONE_SENTINEL_NAME" --reason systemic >/dev/null ); then
      echo "spawn-orchestrator: supervisor halt: teardown FAILED (see above) — verifying bootout anyway" >&2
    fi
    # Verify the bootout actually took (shared with the declared done/deadline
    # teardown): a failed teardown leaves the job loaded and StartInterval relaunches
    # straight back into this condition (finding #22's loop, masked by the halt
    # message). This must run EVEN IF the teardown call above died — see above.
    _verify_bootout "$label" || true
  fi
  echo "spawn-orchestrator: supervisor halt${label:+ ($label)}: $reason"
}

# The supervisor's OWN per-wake bookkeeping, run from the generated launch script
# ABOVE the pre-invoke gate — and this placement is the whole point. The gate
# (task 11) short-circuits THE AGENT INVOCATION, i.e. a model call it would be a
# waste to make; it must never short-circuit the supervisor, because a
# gate-closed wake is exactly when a halted or stalled run most needs to tell a
# human. Both of the gate's closed paths would otherwise be silent:
#   status: done|systemic  the gate boots the job out and exits 0 — so an agent's
#                          circuit-breaker `systemic` that was written but not yet
#                          announced (the wake that would have announced it was
#                          cut short: sleep, reboot, power loss) is torn down
#                          FOREVER, unnotified. Finding #22's silence, restored.
#   paused_until in future the gate skips the wake — so a blown --until, a park
#                          storm, or a pending in-jail alarm-request sits
#                          undelivered for the whole (multi-hour) pause.
# The scan decides the conditions; a gate-closed wake is NOT itself a condition,
# so a healthy paused run still raises nothing. Any future work the supervisor
# must do on EVERY wake regardless of the gate belongs on THIS side of it.
supervisor_scan() {
  local dir="" label="" park_limit="$PARK_STORM_LIMIT_DEFAULT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --park-limit) [ $# -ge 2 ] || die "missing value for --park-limit"; park_limit="$2"; shift 2 ;;
      *) die "unknown supervisor-scan argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$label" ] || die "supervisor-scan requires --dir and --label"
  [ -d "$dir" ] || die "supervisor-scan --dir not found: $dir"
  case "$park_limit" in *[!0-9]*|"") die "--park-limit must be a positive integer: $park_limit" ;; esac
  [ "$park_limit" -ge 1 ] || die "--park-limit must be a positive integer: $park_limit"

  _supervisor_alarm_scan "$dir" "$label" "$park_limit"
  if [ -n "$_ALARM_HALT_REASON" ]; then
    # A terminal condition found BEFORE the agent runs: halt (which writes
    # `status: systemic` and tears the job down) rather than spend a model call
    # relaunching into it. The gate, next, then finds `systemic` and skips the
    # invocation — the alarm is already delivered by the time it does.
    _supervisor_halt --dir "$dir" --label "$label" \
      --condition "$_ALARM_HALT_CONDITION" --reason "$_ALARM_HALT_REASON"
  fi
  # Always 0: this is bookkeeping, not a gate. The wrapper falls through to the
  # real gate, which owns the decision to invoke the agent or not.
  return 0
}

# The per-wake entry point the generated launch script calls after `claude -p`
# exits. See the file-header comment above for the full decision.
supervisor_check() {
  local code="" log="" dir="" label="" state="" limit=3 offset="" wake="" \
        park_limit="$PARK_STORM_LIMIT_DEFAULT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --exit-code) [ $# -ge 2 ] || die "missing value for --exit-code"; code="$2"; shift 2 ;;
      --log) [ $# -ge 2 ] || die "missing value for --log"; log="$2"; shift 2 ;;
      --since-offset) [ $# -ge 2 ] || die "missing value for --since-offset"; offset="$2"; shift 2 ;;
      --wake-start) [ $# -ge 2 ] || die "missing value for --wake-start"; wake="$2"; shift 2 ;;
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --state) [ $# -ge 2 ] || die "missing value for --state"; state="$2"; shift 2 ;;
      --no-progress-limit) [ $# -ge 2 ] || die "missing value for --no-progress-limit"; limit="$2"; shift 2 ;;
      --park-limit) [ $# -ge 2 ] || die "missing value for --park-limit"; park_limit="$2"; shift 2 ;;
      *) die "unknown supervisor-check argument: $1" ;;
    esac
  done
  [ -n "$code" ] && [ -n "$log" ] && [ -n "$dir" ] && [ -n "$label" ] && [ -n "$state" ] \
    || die "supervisor-check requires --exit-code, --log, --dir, --label, and --state"
  case "$code" in *[!0-9]*) die "--exit-code must be a non-negative integer: $code" ;; esac
  # --wake-start dates THIS wake, which is what makes a declaration attributable to
  # it. A missing or garbage value DEGRADES the supervisor — it must never DISABLE
  # it. `_declared_exit_reason` already fails CLOSED on an empty/garbage wake (it
  # honors no declaration at all, exactly as required), so `die`ing here would buy
  # nothing for the stale-declaration bug while throwing away every OTHER duty of
  # this wake: the fatal-auth classification, the no-progress counter, the state
  # write, the halt, the teardown. And the trigger is not hypothetical — `launch.sh`
  # is generated ONCE and persisted in the run dir while `spawn-orchestrator.sh` is
  # updated under a live run, so a wrapper predating `--wake-start` passes none, and
  # a `date` the plist's narrowed PATH cannot reach leaves it empty. Either way the
  # supervisor would exit 2 before classifying anything, on every wake, forever:
  # claude keeps burning quota, launchd keeps relaunching, nothing ever alarms —
  # finding #22's loop, restored and now unkillable because the backstop never runs.
  # So: warn LOUDLY, drop the wake to empty (no declaration is honored), continue.
  case "$wake" in
    '')
      echo "spawn-orchestrator: WARNING: supervisor-check got no --wake-start (stale launch.sh, or \`date\` unreachable on the job's PATH) — NO declared exit reason will be honored this wake (fail-closed); classification, the no-progress guard, and the halt still apply" >&2
      ;;
    *[!0-9]*)
      echo "spawn-orchestrator: WARNING: --wake-start is not a non-negative integer (epoch seconds): $wake — NO declared exit reason will be honored this wake (fail-closed); classification, the no-progress guard, and the halt still apply" >&2
      wake=""
      ;;
  esac
  [ -f "$log" ] || die "supervisor-check --log not found: $log"
  [ -d "$dir" ] || die "supervisor-check --dir not found: $dir"
  case "$limit" in *[!0-9]*|"") die "--no-progress-limit must be a positive integer: $limit" ;; esac
  case "$park_limit" in *[!0-9]*|"") die "--park-limit must be a positive integer: $park_limit" ;; esac
  [ "$park_limit" -ge 1 ] || die "--park-limit must be a positive integer: $park_limit"

  # Alarm conditions the supervisor can see WITHOUT classifying an exit (task
  # 16): an in-jail detector's request, the agent's own circuit-breaker
  # `systemic` terminal, a blown --until, a park storm. These run first and on
  # EVERY wake, including a wake that exited 0 — a stalled run looks healthy from
  # the exit code alone, which is precisely how #22 stayed silent for 4h14m.
  _supervisor_alarm_scan "$dir" "$label" "$park_limit"
  if [ -n "$_ALARM_HALT_REASON" ]; then
    _supervisor_halt --dir "$dir" --label "$label" \
      --condition "$_ALARM_HALT_CONDITION" --reason "$_ALARM_HALT_REASON"
    return "$code"
  fi

  local class; class="$(classify_exit --exit-code "$code" --output "$log" --since-offset "$offset")"

  # A fatal classification halts BEFORE the declared reason is even read. This is
  # the one place inference outranks declaration, and deliberately so: a
  # non-retryable auth failure is not something the agent is in a position to
  # contradict (it could not run at all), and over-halting is the safe direction —
  # under-halting is finding #22, 52 relaunches into the same 401.
  case "$class" in
    fatal:*)
      _supervisor_halt --dir "$dir" --label "$label" \
        --condition fatal-auth --reason "${class#fatal: }"
      return "$code"
      ;;
  esac

  # THE EXIT CONTRACT (task 15): the agent's own declaration, when this wake made
  # one, decides relaunch-vs-teardown. This is what replaces the blind timer — the
  # supervisor stops inferring what the agent already knew.
  local declared; declared="$(_declared_exit_reason "$dir" "$wake")"
  case "$declared" in
    done|deadline)
      # Subshelled: `_write_supervisor_state` `die`s on a write failure (mktemp,
      # disk full, read-only run dir), and the run has ALREADY declared it is
      # finished — a wedged bookkeeping cache must not cost us the teardown below,
      # any more than a wedged halt may (see _supervisor_halt's identical
      # `teardown` reasoning). Same `_alarm_safe`-style subshell.
      ( _write_supervisor_state "$state" 0 "$(_run_head "$dir")" ) \
        || echo "spawn-orchestrator: supervisor-check: could not persist supervisor-state (tearing down regardless — the run already declared $declared)" >&2
      # stderr NOT suppressed, and the bootout VERIFIED (the same check the halt
      # path does): `teardown` swallows a failed `launchctl bootout`, and an
      # unverified failure here leaves the job loaded — StartInterval then wakes a
      # FINISHED run forever, each wake exiting 0 with `status` reporting
      # `relaunch=no`. Zero work, zero alarm: finding #22 by another route.
      # `teardown` itself is subshelled for the same reason as `_supervisor_halt`'s
      # identical call: it `die`s (an `exit`) if the done-sentinel write fails, and
      # an unguarded exit here would abort BEFORE `_verify_bootout` below ever
      # runs — the exact defect class this file exists to close.
      if ! ( teardown --label "$label" --done-sentinel "$dir/.auto-pilot/$DONE_SENTINEL_NAME" --reason "$declared" >/dev/null ); then
        echo "spawn-orchestrator: supervisor-check: teardown FAILED (see above) — verifying bootout anyway" >&2
      fi
      _verify_bootout "$label" || true
      echo "spawn-orchestrator: supervisor-check declared $declared — tearing down, NO relaunch"
      return "$code"
      ;;
    systemic)
      # Carry the ORCHESTRATOR'S OWN diagnosis into the alarm. A fixed string here
      # ("see RUN.md pause_reason") is what _supervisor_halt would write INTO
      # pause_reason — the operator wakes to an alarm pointing at itself, with the
      # concrete cause gone. `exit_reason_detail` exists for exactly this; the
      # already-recorded `pause_reason` is the fallback.
      local sys_detail; sys_detail="$(_run_front_field "$dir" exit_reason_detail)"
      [ -n "$sys_detail" ] || sys_detail="$(_run_front_field "$dir" pause_reason)"
      # run-state.md's RUN.md TEMPLATE declares these keys with an inline `# …` doc
      # comment, and this reader deliberately does not strip `#` (they are free prose
      # and truncating an operator's diagnosis at a `#` would be its own data loss).
      # A value that is ONLY that comment is documentation, not a diagnosis — putting
      # it in the alarm tells the human "why the run paused/halted" instead of why.
      case "$sys_detail" in '#'*) sys_detail="" ;; esac
      [ -n "$sys_detail" ] || sys_detail="no detail recorded — see REPORT.md and orchestrator.log"
      _supervisor_halt --dir "$dir" --label "$label" --preserve-pause-reason \
        --condition systemic \
        --reason "the orchestrator declared a systemic exit (circuit breaker / failed invariant): $sys_detail"
      return "$code"
      ;;
    paused)
      # A rate-window pause makes no progress BY DESIGN, so it never counts against
      # the no-progress guard — but ONLY when the run state CORROBORATES the
      # declaration. The corroborating fact is `status: paused` and nothing else
      # (_run_is_paused, the same carve-out the retry path below already used):
      # run-budget.md's agent pause writes it on every legitimate pause, and it is
      # CLEARED on resume. `paused_until` is NOT usable as corroboration — it is
      # declared with an inline `# comment` in run-state.md's own RUN.md template
      # (so a reader that must not truncate free prose at a `#` reads the comment
      # back as a value, and every run corroborates unconditionally), and it is
      # durable across a resume (so a run that paused once is exempt forever).
      # Without real corroboration a prompt/logic bug that declares `paused` on every
      # wake while dying non-zero would reset the counter forever — infinite
      # relaunch, zero progress, no alarm.
      if _run_is_paused "$dir"; then
        _write_supervisor_state "$state" 0 "$(_run_head "$dir")"
        echo "spawn-orchestrator: supervisor-check declared paused — relaunch expected past the reset"
        return "$code"
      fi
      echo "spawn-orchestrator: supervisor-check declared paused but the run state does NOT corroborate it (RUN.md has no status: paused) — relaunching, but the no-progress guard still applies" >&2
      ;;
    continuing)
      if [ "$code" = 0 ]; then
        _write_supervisor_state "$state" 0 "$(_run_head "$dir")"
        echo "spawn-orchestrator: supervisor-check declared continuing — work remains, relaunch expected"
        return 0
      fi
      # Declared `continuing` but exited non-zero: relaunch, yes — but this wake
      # still fell over, so the no-progress backstop below must keep counting it.
      # Otherwise an agent that declares `continuing` and then crashes forever is
      # exempt from the very guard that exists for a run making no progress.
      echo "spawn-orchestrator: supervisor-check declared continuing on a non-zero exit ($code) — relaunching, but the no-progress guard still applies"
      ;;
    *)
      # No declaration THIS wake (hard-killed agent, a pre-exit-contract prompt, or
      # a garbage value): fall back to inference. Fail-SAFE — never a teardown.
      case "$class" in
        done)
          _write_supervisor_state "$state" 0 "$(_run_head "$dir")"
          echo "spawn-orchestrator: supervisor-check done (inferred: exit 0, no declared reason)"
          return 0
          ;;
      esac
      ;;
  esac

  # retry: a legitimate paused wake never counts against the guard.
  if _run_is_paused "$dir"; then
    _write_supervisor_state "$state" 0 "$(_run_head "$dir")"
    echo "spawn-orchestrator: supervisor-check retry (paused wake, no-progress guard skipped): ${class#retry: }"
    return "$code"
  fi

  local prev_head prev_count head count
  prev_head="$(_supervisor_state_field "$state" head)"
  prev_count="$(_supervisor_state_field "$state" count)"
  # A corrupt (non-numeric) count must not brick the guard via an arithmetic
  # error under `set -u` — treat it as a fresh start.
  case "$prev_count" in ''|*[!0-9]*) prev_count=0 ;; esac
  # An empty HEAD (non-git dir, git absent from the launchd PATH, an unborn
  # branch) must count AS no-progress, not silently reset the guard to 1 every
  # wake — a broken environment is exactly when the relaunch loop this guard
  # backstops is most likely. Sentinel it so empty == empty advances the counter.
  head="$(_run_head "$dir")"; head="${head:-unknown}"

  if [ -n "$prev_head" ] && [ "$prev_head" = "$head" ]; then
    count=$((prev_count + 1))
  else
    count=1
  fi
  # Subshelled, for the same reason as the declared-done branch above: this is
  # BOOKKEEPING, and the halt below is the POINT. `_write_supervisor_state` `die`s
  # (an `exit`) on a write failure — an unwritable run dir, a full disk — and an
  # unguarded `exit` here aborts the whole wake BEFORE the no-progress halt, so a
  # crashing agent under a wedged run dir never halts, never alarms and never tears
  # down: StartInterval relaunches it forever. Zero work, zero alarm — finding #22's
  # loop, reached through the very guard that exists to backstop it.
  ( _write_supervisor_state "$state" "$count" "$head" ) \
    || echo "spawn-orchestrator: supervisor-check: could not persist supervisor-state (the no-progress counter cannot advance across wakes while this is broken — the halt below still fires if THIS wake already reached the limit)" >&2

  if [ "$count" -ge "$limit" ]; then
    _supervisor_halt --dir "$dir" --label "$label" --condition no-progress \
      --reason "no forward progress after $count consecutive supervisor wakes (${class#retry: })"
  else
    echo "spawn-orchestrator: supervisor-check retry ($count/$limit consecutive, no progress): ${class#retry: }"
  fi
  return "$code"
}

# ---------------------------------------------------------------------------
# Task 11 — gate the relaunch on paused_until in shell (finding #19): a
# launchd StartInterval wake during a rate-window pause used to boot a full
# `claude -p` orchestrator just to re-read RUN.md and conclude "not yet
# time" — a model call spent at the exact moment tokens are scarcest (a
# multi-hour rate-window pause could wake, and pay for, dozens of times).
# The check is a pure timestamp comparison; supervisor_gate runs it in the
# generated launch script BEFORE claude is invoked at all. The agent-side
# wake guard stays as defense in depth (load-bearing for --resume).
# ---------------------------------------------------------------------------

# Parse an ISO-8601 UTC timestamp in RUN.md's form (e.g.
# 2026-07-12T07:52:04Z) to epoch seconds. Tries BSD `date` (macOS, what the
# launchd job actually runs under) first, then GNU `date` (Linux/CI), so the
# same script works on both. Prints nothing and returns non-zero if BOTH
# parses fail — the caller treats that as unparseable and fails open.
_parse_iso8601_utc() {
  local v="$1" epoch
  epoch="$(date -j -u -f '%Y-%m-%dT%H:%M:%SZ' "$v" +%s 2>/dev/null)" && { printf '%s' "$epoch"; return 0; }
  epoch="$(date -u -d "$v" +%s 2>/dev/null)" && { printf '%s' "$epoch"; return 0; }
  return 1
}

# The pre-invoke gate the generated launch script calls BEFORE `claude -p`
# runs at all. Exit 20 is the distinct "gate closed, do not invoke the
# agent" signal the launch script checks for; any other exit (including 0)
# means "proceed as normal." This is FAIL-SAFE, not fail-closed: a missing
# RUN.md, an unparseable/garbage paused_until, or an empty paused_until all
# proceed to invoke the agent rather than risk silently skipping every wake
# forever — over-running the agent once is recoverable, a run permanently
# stuck unable to relaunch is not. The agent-side wake guard is the second
# line of defense for anything this gate lets slip through.
supervisor_gate() {
  local dir="" label=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      *) die "unknown supervisor-gate argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$label" ] || die "supervisor-gate requires --dir and --label"

  local run_md="$dir/.auto-pilot/RUN.md"
  if [ ! -f "$run_md" ]; then
    echo "spawn-orchestrator: supervisor-gate: no RUN.md at $run_md, proceeding (fail-safe)"
    return 0
  fi

  # _front_field (above) reads the global $front — same convention status()
  # uses, reused here rather than re-deriving a front-matter parser.
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"

  local run_status; run_status="$(_front_field status)"
  case "$run_status" in
    done|systemic)
      # A done/systemic run must never relaunch (task 10's fatal-halt
      # teardown path, reused rather than reimplemented — see
      # _supervisor_halt's identical `teardown --label` call).
      echo "spawn-orchestrator: supervisor-gate: run status is '$run_status', tearing down $label instead of relaunching"
      teardown --label "$label"
      return 20
      ;;
  esac

  local paused_until; paused_until="$(_front_field paused_until)"
  [ -n "$paused_until" ] || return 0

  local until_epoch
  if ! until_epoch="$(_parse_iso8601_utc "$paused_until")"; then
    echo "spawn-orchestrator: supervisor-gate: unparseable paused_until '$paused_until', proceeding (fail-safe)"
    return 0
  fi

  local now_epoch; now_epoch="$(date -u +%s)"
  if [ "$now_epoch" -lt "$until_epoch" ]; then
    echo "spawn-orchestrator: supervisor-gate: paused until $paused_until, skipping this wake without invoking the agent"
    return 20
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Task 18 — post-merge restack of stacked PRs (finding #25). Squash-merging a
# parent orphans a child two ways: LOUD (GitHub deletes the parent's branch,
# closing a child still based on it) and QUIET (the child still targets the
# parent's *branch*, so merging it lands on that branch, never on
# base_branch, and the PR looks perfectly healthy while doing nothing — worse
# than the loud failure because nothing alarms). `restack` mechanizes the
# four-command incantation a human would otherwise have to remember at the
# right moment, per child, in dependency order.
#
# IMPORTANT actor distinction (skills/auto-pilot/references/run-state.md
# "base_sha"): a HUMAN merging/reviewing a parent is the EXPECTED trigger for
# restack, never an error — the frozen `base_sha` diverging from the parent's
# post-review tip is exactly what review is supposed to do. The existing
# base_sha freeze/park guard exists to catch the ORCHESTRATOR moving a base
# mid-run; it does not apply here.
#
# `gh` is mockable: --gh <path> (default: resolved `gh` on PATH) lets the test
# suite point at a local stub script, so the git mechanics (rebase/push)
# exercise a real temp repo while every GitHub call is fully offline.
# ---------------------------------------------------------------------------

# Parse RUN.md's front matter + task table into the globals _RS_BASE_BRANCH and
# the index-aligned arrays _RS_TASK/_RS_PHASE/_RS_BRANCH/_RS_BASE/_RS_BASE_SHA/_RS_PR.
# Reuses status()'s front-matter/table extraction (RUN.md's format is defined
# once in run-state.md; both readers walk the same `| ... |` rows). _RS_PHASE
# (the `phase` column) is captured for `doctor` (task 14) — restack itself never
# reads it, but this stays the ONE parser for RUN.md's table rather than a
# second, divergent reader.
_restack_read_run_md() {
  local run_md="$1"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
  _RS_BASE_BRANCH="$(printf '%s\n' "$front" | grep -E '^base_branch:' | head -1 \
    | sed -e 's/^base_branch: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  [ -n "$_RS_BASE_BRANCH" ] || _RS_BASE_BRANCH="main"

  # _RS_NEWTIP[i] is filled in only by restack(): the rewritten tip a branch got
  # when THIS pass force-pushed it. A child of a branch we just rewrote must be
  # cascaded onto that new tip (see restack's "cascade" mode), or force-pushing
  # a parent silently orphans its own child inside the same run.
  _RS_TASK=(); _RS_PHASE=(); _RS_BRANCH=(); _RS_BASE=(); _RS_BASE_SHA=(); _RS_PR=(); _RS_NEWTIP=()
  local line
  while IFS= read -r line; do
    # skip the header separator row (only pipes/colons/dashes/spaces)
    case "$line" in *[!'|'' ':-]*) ;; *) continue ;; esac
    local cols t ph b base bsha pr
    IFS='|' read -ra cols <<<"$line"
    [ "${#cols[@]}" -ge 7 ] || continue
    t="$(printf '%s' "${cols[1]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ "$t" != "task" ] || continue   # header row
    ph="$(printf '%s' "${cols[2]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    b="$(printf '%s' "${cols[3]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    base="$(printf '%s' "${cols[4]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    bsha="$(printf '%s' "${cols[5]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    pr="$(printf '%s' "${cols[6]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    _RS_TASK+=("$t"); _RS_PHASE+=("$ph"); _RS_BRANCH+=("$b"); _RS_BASE+=("$base"); _RS_BASE_SHA+=("$bsha"); _RS_PR+=("$pr")
    _RS_NEWTIP+=("")
  done < <(awk '/^\|/{print}' "$run_md")
}

# True (0) when a RUN.md cell is one of its "empty" spellings (the table uses
# both a bare hyphen and an em-dash depending on which doc wrote it).
_restack_empty() { case "$1" in ''|-|'—') return 0 ;; *) return 1 ;; esac; }

# Extract the bare PR number from any shape RUN.md's `pr` column actually
# holds: a bare number (`188`), a `#`-prefixed number (`#188`), or the
# markdown link RUN.md's own writer emits (`[#188](https://github.com/x/y/pull/188)`,
# per skills/auto-pilot/references/run-state.md). Prints nothing for an
# empty/`-`/`—` cell, or for a cell with no digits at all (D3): a naive
# `${pr#\#}` strip only handles the bare-`#188` shape and silently fails to
# strip the markdown-link form, so `gh pr view` gets handed the whole link
# and fails — the exact bug that parked a healthy handed-off task on doctor's
# first real run. ONE implementation, shared by doctor (I3/I6) and restack
# (which only no-ops on the mismatch today, but must not diverge from this).
_pr_number() {
  local cell="$1"
  _restack_empty "$cell" && return 0
  case "$cell" in
    \[*\]\(*\))
      # markdown link: the PR number is the trailing digits of the URL
      # inside the (...) — take the text between the LAST '(' and ')'.
      cell="${cell##*(}"; cell="${cell%)*}"
      printf '%s' "$cell" | grep -oE '[0-9]+$'
      ;;
    *)
      printf '%s' "$cell" | grep -oE '[0-9]+' | head -1
      ;;
  esac
}

# Rebase <branch>'s commits (those after <base_sha>) onto <onto_ref> in a
# DEDICATED SCRATCH WORKTREE, never in the caller's tree, and print the
# resulting tip SHA on success.
#
# Why a scratch worktree is load-bearing (not just tidy): `git rebase --onto X
# Y <branch>` CHECKS OUT <branch> and leaves HEAD there. Run against the run
# worktree, restack would park the orchestrator's HEAD on a task branch — which
# is finding #23 exactly, the invariant task 13's `assert-run-head` guard
# exists to enforce — and a mid-rebase crash would strand the run worktree on a
# task branch, so `--resume` would read RUN.md from the wrong tree. The scratch
# worktree is created DETACHED at the branch's REMOTE tip (authoritative, and a
# detached checkout can't collide with the branch being checked out elsewhere),
# and is removed on every exit path: success, conflict, and push rejection.
#
# Args: <repo> <remote> <branch> <base_sha> <onto_ref> <lease_sha>
# Exit: 0 rebased+pushed (tip on stdout) | 3 rebase conflict | 4 push rejected
_restack_rebase_push() {
  local repo="$1" remote="$2" branch="$3" base_sha="$4" onto_ref="$5" lease_sha="$6"
  local sw; sw="$(mktemp -d "${TMPDIR:-/tmp}/restack-wt.XXXXXX")" || return 4
  rm -rf "$sw"   # `git worktree add` requires the path NOT to exist yet

  git -C "$repo" worktree add --detach "$sw" "$remote/$branch" >/dev/null 2>&1 || {
    rm -rf "$sw"; return 4
  }
  # Cleanup is unconditional from here on — every `return` below goes through it.
  local rc=0 tip=""
  if git -C "$sw" rebase --onto "$onto_ref" "$base_sha" >/dev/null 2>&1; then
    tip="$(git -C "$sw" rev-parse HEAD)"
    # Explicit lease (branch:sha we actually fetched), not the bare
    # --force-with-lease default: the bare form trusts the remote-tracking ref,
    # which a concurrent fetch could have already advanced — silently turning
    # the lease into a plain --force.
    git -C "$sw" push --force-with-lease="$branch:$lease_sha" "$remote" "HEAD:$branch" >/dev/null 2>&1 \
      || rc=4
  else
    git -C "$sw" rebase --abort >/dev/null 2>&1
    rc=3
  fi
  git -C "$repo" worktree remove --force "$sw" >/dev/null 2>&1
  rm -rf "$sw"
  [ "$rc" = 0 ] || return "$rc"
  printf '%s' "$tip"
}

# ---------------------------------------------------------------------------
# Task 21 — enforce the restack's re-verification. Task 18 shipped the
# mechanism (rebase, force-push, retarget) and only ANNOUNCED the
# re-verification into REPORT.md; nothing checked it happened. These helpers
# make it a code-enforced re-verification TRIGGER instead.
# ---------------------------------------------------------------------------

# Look up a parent PR's FINAL pre-merge tip (its head just before the human
# merged it) — the upper end of its post-hand-off review delta. The lower end
# is already known: the child's own recorded `base_sha` IS the parent's
# hand-off tip. Two things had to be verified against the REAL `gh`/GitHub
# before this could be trusted (checked 2026-07-12, see test comments for the
# exact commands run):
#   1. `gh pr view <n> --json headRefOid` returns the PR's head SHA directly —
#      it does NOT require the branch to still exist.
#   2. `refs/pull/<n>/head` stays fetchable even after a squash-merge deletes
#      the branch, and its SHA equals `headRefOid` — confirmed across 4 real
#      PRs in this repo (one with its branch already deleted). So the API
#      gives the SHA and this ref is what actually gets the OBJECTS onto disk;
#      cross-checking one against the other is what stops a stale/wrong
#      answer from either source alone.
#
# A NONEXISTENT PR is NOT the same as an UNDETERMINED read: real `gh pr view
# 999999 --json state` EXITS NON-ZERO with stderr containing "Could not
# resolve to a PullRequest" — a positive, mechanical "this PR is gone" signal.
# Any OTHER non-zero (network, auth, rate limit, gh missing) is UNDETERMINED
# and must never be treated as "no review delta" — that would silently skip
# the very audit this task exists to enforce.
#
# Exit: 0 tip printed on stdout | 1 PR positively NOT_FOUND | 2 UNDETERMINED
# (gh/network failure, empty answer, or the API/ref cross-check disagreeing —
# callers MUST treat 1 and 2 identically: never proceed as if there were no
# delta to audit).
_restack_parent_final_tip() {
  local repo="$1" remote="$2" gh_bin="$3" pr_num="$4"
  local errf; errf="$(mktemp "${TMPDIR:-/tmp}/restack-gherr.XXXXXX")" || return 2
  local api_tip rc
  api_tip="$("$gh_bin" pr view "$pr_num" --json headRefOid --jq .headRefOid 2>"$errf")"
  rc=$?
  if [ "$rc" -ne 0 ]; then
    if grep -qiE 'could not resolve to a pull ?request' "$errf"; then
      rm -f "$errf"; return 1
    fi
    rm -f "$errf"; return 2
  fi
  rm -f "$errf"
  [ -n "$api_tip" ] || return 2

  git -C "$repo" fetch "$remote" "refs/pull/$pr_num/head" >/dev/null 2>&1 || return 2
  local ref_tip; ref_tip="$(git -C "$repo" rev-parse FETCH_HEAD 2>/dev/null)"
  [ -n "$ref_tip" ] || return 2
  [ "$ref_tip" = "$api_tip" ] || return 2   # disagreement: trust neither, fail closed

  printf '%s' "$api_tip"
}

# The mechanizable half of "a clean rebase proves nothing" (run-state.md
# "restacked child is stale"): for every line the parent's post-hand-off
# review commits ADDED to a file, assert that EXACT line still exists in the
# child's post-rebase tree. Content-based (not line-number-based), so a
# child's own unrelated edits to the same file never trip a false positive —
# only an actually-missing line does. Deeper semantic contradiction (a line
# that survives textually but is negated elsewhere) is NOT decidable here by
# design; that is delegated to the mandatory co-review re-run.
#
# Args: <repo> <base_sha> <parent_final_tip> <child_post_rebase_tip>
# Prints one "DEFECT <file>: <line>" per dropped line to stdout.
# Exit: 0 clean | 1 one or more review-added lines are missing from the child.
_restack_diff_audit() {
  local repo="$1" base_sha="$2" parent_tip="$3" child_tip="$4"
  local files; files="$(git -C "$repo" diff --name-only "$base_sha" "$parent_tip" -- 2>/dev/null)"
  local rc=0 f
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    local added
    added="$(git -C "$repo" diff "$base_sha" "$parent_tip" -- "$f" 2>/dev/null \
      | grep -E '^\+' | grep -vE '^\+\+\+' | sed 's/^\+//')"
    [ -n "$added" ] || continue
    local child_content; child_content="$(git -C "$repo" show "$child_tip:$f" 2>/dev/null)"
    local line
    while IFS= read -r line; do
      [ -n "$line" ] || continue
      if ! printf '%s\n' "$child_content" | grep -qxF "$line"; then
        echo "DEFECT $f: $line"
        rc=1
      fi
    done <<<"$added"
  done <<<"$files"
  return "$rc"
}

# Files the parent's post-hand-off review touched, intersected with files the
# CHILD'S OWN commits touch. Non-empty means the child's existing co-review
# approved code the parent's review has since changed underneath it — its
# approval is now STALE and must be re-earned, not assumed to still hold.
_restack_file_overlap() {
  local repo="$1" parent_base="$2" parent_tip="$3" child_base="$4" child_tip="$5"
  local pf cf
  pf="$(git -C "$repo" diff --name-only "$parent_base" "$parent_tip" 2>/dev/null | sort -u)"
  cf="$(git -C "$repo" diff --name-only "$child_base" "$child_tip" 2>/dev/null | sort -u)"
  comm -12 <(printf '%s\n' "$pf") <(printf '%s\n' "$cf")
}

# Re-run the run's OWN verify_command against the restacked child's NEW base —
# its previous green ran against the OLD one and carries no evidence forward.
# Runs in a fresh throwaway worktree checked out at <tip> (already pushed, so
# this is safe to add/remove independent of the rebase worktree above).
# Exit: verify_cmd's own exit code (2 if the scratch worktree itself couldn't
# be created — never silently "passes" on a setup failure).
_restack_verify_at_tip() {
  local repo="$1" verify_cmd="$2" tip="$3" logf="$4"
  local sw; sw="$(mktemp -d "${TMPDIR:-/tmp}/restack-verify-wt.XXXXXX")" || return 2
  rm -rf "$sw"
  git -C "$repo" worktree add --detach "$sw" "$tip" >/dev/null 2>&1 || { rm -rf "$sw"; return 2; }
  ( cd "$sw" && eval "$verify_cmd" ) >"$logf" 2>&1
  local rc=$?
  git -C "$repo" worktree remove --force "$sw" >/dev/null 2>&1
  rm -rf "$sw"
  return "$rc"
}

# Park a task (RUN.md phase -> parked) and raise the task-16 alarm channel —
# reused, not reimplemented, per this task's explicit instruction. Subshelled
# per the die-capable-callee convention above: `alarm`'s own argument
# validation can still `die`, and this must never take a restack pass with it.
_restack_park_and_alarm() {
  local run_md="$1" run_dir="$2" task="$3" condition="$4" reason="$5"
  _set_task_phase "$run_md" "$task" "parked" 2>/dev/null \
    || echo "spawn-orchestrator: restack WARNING — could not set $task's phase to parked in $run_md" >&2
  ( alarm --dir "$run_dir" --condition "$condition" --reason "$reason" \
      --action "inspect $task, resolve by hand, then re-run restack" ) \
    || echo "spawn-orchestrator: restack WARNING — alarm call failed for $condition (park already recorded)" >&2
}

restack() {
  local run_dir="" repo="" remote="origin" gh_bin="" dry=0 verify_cmd=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --run-dir) [ $# -ge 2 ] || die "missing value for --run-dir"; run_dir="$2"; shift 2 ;;
      --repo) [ $# -ge 2 ] || die "missing value for --repo"; repo="$2"; shift 2 ;;
      --remote) [ $# -ge 2 ] || die "missing value for --remote"; remote="$2"; shift 2 ;;
      --gh) [ $# -ge 2 ] || die "missing value for --gh"; gh_bin="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; verify_cmd="$2"; shift 2 ;;
      --dry-run) dry=1; shift ;;
      *) die "unknown restack argument: $1" ;;
    esac
  done
  [ -n "$run_dir" ] || die "restack requires --run-dir <dir containing .auto-pilot/RUN.md>"
  case "$run_dir" in /*) ;; *) die "--run-dir must be absolute (fail-closed): $run_dir" ;; esac
  [ -n "$repo" ] || repo="$run_dir"
  case "$repo" in /*) ;; *) die "--repo must be absolute (fail-closed): $repo" ;; esac
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "--repo is not a git worktree (fail-closed): $repo"
  [ -n "$gh_bin" ] || gh_bin="$(command -v gh 2>/dev/null)" || true
  [ -n "$gh_bin" ] || die "gh not found (fail-closed): pass --gh <path> (mockable in tests) or put gh on PATH"
  # Task 21: re-verification is no longer optional — a caller that omits
  # --verify-cmd is exactly the "rule a human must remember" pattern this task
  # exists to close. Required up front, like --run-dir/--repo/--gh, not
  # discovered lazily mid-loop (a `--dry-run` plan is exempt: nothing executes).
  [ -n "$verify_cmd" ] || [ "$dry" = 1 ] \
    || die "restack requires --verify-cmd <cmd> (the run's own verify_command) — a restacked child's previous green does not carry over to its new base"

  local run_md="$run_dir/.auto-pilot/RUN.md"
  _restack_read_run_md "$run_md"
  local base_branch="$_RS_BASE_BRANCH"

  # HEAD-invariant guard (finding #23 / task 13's `assert-run-head`): restack
  # must NEVER move the caller's HEAD. The rebases below run in throwaway
  # worktrees, so this records HEAD to ASSERT it at every exit path rather than
  # to restore it — a restack that somehow moved the run worktree's HEAD is a
  # bug, not something to paper over.
  _RS_HEAD_BEFORE="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
  _RS_REF_BEFORE="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  [ -n "$_RS_HEAD_BEFORE" ] || die "cannot read HEAD (fail-closed): $repo"
  # Fail closed on a dirty or mid-operation tree: `git worktree add` off a repo
  # that is itself mid-rebase/mid-merge, or carrying uncommitted work, is how a
  # "helpful" automated recovery destroys a human's in-progress state.
  [ -z "$(git -C "$repo" status --porcelain 2>/dev/null)" ] \
    || die "--repo worktree is dirty (fail-closed): commit or stash before restacking: $repo"
  local gitdir; gitdir="$(git -C "$repo" rev-parse --git-dir 2>/dev/null)"
  case "$gitdir" in /*) ;; *) gitdir="$repo/$gitdir" ;; esac
  { [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] || [ -e "$gitdir/MERGE_HEAD" ]; } \
    && die "--repo worktree is mid-rebase/mid-merge (fail-closed): finish or abort it first: $repo"

  # Fetch ALL refs once, up front: every rebase below must see the SAME remote
  # view. A stale local <remote>/<base_branch> is exactly how a "clean" rebase
  # could silently miss the parent's post-hand-off review commits
  # (run-state.md's "restacked child is stale" note), and the per-branch
  # remote-tracking refs are what the push leases are computed against.
  git -C "$repo" fetch "$remote" >/dev/null 2>&1 \
    || die "fetch failed (fail-closed): $remote"

  local n="${#_RS_TASK[@]}" pass=0 progressed=1 restacked=0 failed=0 flagged=0
  local -a report=()
  # Fixed-point over the table, because a chain resolves one link per pass: a
  # grandchild only becomes actionable once its own parent has been rewritten
  # (cascade) or retargeted (onto-base) by an earlier pass.
  while [ "$progressed" = 1 ] && [ "$pass" -le "$n" ]; do
    progressed=0; pass=$((pass + 1))
    local i
    for ((i = 0; i < n; i++)); do
      local task="${_RS_TASK[$i]}" branch="${_RS_BRANCH[$i]}" base="${_RS_BASE[$i]}" \
            base_sha="${_RS_BASE_SHA[$i]}" pr="${_RS_PR[$i]}"
      [ "$base" != "$base_branch" ] || continue   # independent task — nothing to restack
      _restack_empty "$pr" && continue            # no PR yet — nothing to retarget
      local pr_num; pr_num="$(_pr_number "$pr")"

      # Idempotent: trust the PR's OWN live base over our (possibly stale)
      # RUN.md column — a child already retargeted to base_branch is a no-op,
      # never a re-rebase/re-push/re-edit.
      local live_base
      live_base="$("$gh_bin" pr view "$pr_num" --json baseRefName --jq .baseRefName 2>/dev/null)"
      if [ "$live_base" = "$base_branch" ]; then
        echo "spawn-orchestrator: restack $task already based on $base_branch (no-op)"
        continue
      fi

      # Never force-push a child whose OWN PR is no longer open: `gh pr edit
      # --base` only works on an open PR, so a MERGED child is already done and a
      # CLOSED one (LOUD-orphaned when its parent branch was deleted) needs a
      # human to reopen/recreate it — rewriting either's branch here would be a
      # destructive no-win. Check state BEFORE any rebase/push.
      local child_state
      child_state="$("$gh_bin" pr view "$pr_num" --json state --jq .state 2>/dev/null)"
      if [ "$child_state" = "MERGED" ]; then
        echo "spawn-orchestrator: restack $task PR #$pr_num already MERGED (no-op)"
        continue
      fi
      if [ "$child_state" = "CLOSED" ]; then
        echo "spawn-orchestrator: restack $task DEFECT — PR #$pr_num is CLOSED (LOUD-orphaned by a deleted parent branch); a human must reopen or recreate it — NOT force-pushing"
        report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): the PR is CLOSED — LOUD-orphaned when its parent branch was deleted on merge. A human must reopen or recreate it; restack will not force-push a closed PR's branch (finding #25, LOUD case).")
        flagged=$((flagged + 1))
        continue
      fi

      local parent_pr="" j pidx=-1
      for ((j = 0; j < n; j++)); do
        if [ "${_RS_BRANCH[$j]}" = "$base" ]; then pidx=$j; parent_pr="$(_pr_number "${_RS_PR[$j]}")"; break; fi
      done
      [ "$pidx" -ge 0 ] || continue   # parent not tracked in this run

      # Two distinct reasons a child needs rewriting, and they are NOT the same op:
      #
      #   onto-base  the parent's PR MERGED (squashed onto base_branch) → drop the
      #              parent's now-squashed commits and retarget the PR to base_branch.
      #   cascade    the parent was itself force-pushed by THIS run (onto-base or an
      #              earlier cascade) → the child still carries the parent's OLD,
      #              rewritten commits. Rebase it onto the parent's NEW tip. Its PR
      #              base stays the parent branch (still a live branch, now correctly
      #              targeting base_branch) — retargeting it to base_branch here would
      #              re-propose the parent's whole changeset.
      #
      # Without cascade, force-pushing a parent silently orphans its own child inside
      # the very run that was supposed to fix orphaned children.
      local newtip="${_RS_NEWTIP[$pidx]}" mode="" onto_ref="" retarget=""
      if [ -n "$newtip" ] && ! _restack_empty "$base_sha" && [ "$base_sha" != "$newtip" ]; then
        mode="cascade"; onto_ref="$newtip"
      else
        _restack_empty "$parent_pr" && continue   # parent has no PR — can't know if it merged
        local parent_state
        parent_state="$("$gh_bin" pr view "$parent_pr" --json state --jq .state 2>/dev/null)"
        if [ "$parent_state" = "MERGED" ]; then
          if _restack_empty "$base_sha"; then
            echo "spawn-orchestrator: restack $task FAILED — parent PR #$parent_pr merged but no recorded base_sha (fail-closed, needs a human)"
            failed=$((failed + 1))
            continue
          fi
          mode="onto-base"; onto_ref="$remote/$base_branch"; retarget="$base_branch"
        else
          # RESUMABLE cascade: the parent's PR is still OPEN, but a PRIOR restack
          # run may have already rewritten it (retargeted to base_branch and
          # force-pushed). This run's in-memory _RS_NEWTIP is empty for it, so
          # detect the rewrite from the REMOTE: if the parent is already based on
          # base_branch and its remote tip has moved off this child's recorded
          # base_sha, cascade the child onto that tip. Without this, a partial
          # earlier run silently strands the grandchild while reporting success.
          local parent_live_base parent_remote_tip
          parent_live_base="$("$gh_bin" pr view "$parent_pr" --json baseRefName --jq .baseRefName 2>/dev/null)"
          parent_remote_tip="$(git -C "$repo" rev-parse "$remote/$base" 2>/dev/null)"
          if [ "$parent_live_base" = "$base_branch" ] && ! _restack_empty "$base_sha" \
             && [ -n "$parent_remote_tip" ] && [ "$parent_remote_tip" != "$base_sha" ]; then
            # Idempotent: a cascade-mode child keeps its PR base on the parent
            # branch, so the top-of-loop live-base no-op check can't catch an
            # already-cascaded child. If it already sits on the parent's current
            # tip, there is nothing to do — skip, rather than re-run a no-op
            # rebase that churns REPORT.md and re-flags a child nothing touched.
            if git -C "$repo" merge-base --is-ancestor "$parent_remote_tip" "$remote/$branch" 2>/dev/null; then
              echo "spawn-orchestrator: restack $task already cascaded onto $base's tip (no-op)"
              continue
            fi
            mode="cascade"; onto_ref="$parent_remote_tip"
          else
            # A human reviewing/merging the parent is the EXPECTED trigger; an
            # unmerged, un-rewritten parent just means this child isn't ready yet.
            continue
          fi
        fi
      fi

      # The exact incantation, copy-pasteable — printed BEFORE execution, so it
      # reaches the log (and REPORT.md below) even if restack never runs: without
      # the automation the human is still one paste from correct.
      echo "spawn-orchestrator: restack $task ($mode) commands:"
      echo "  git fetch $remote"
      echo "  git rebase --onto $onto_ref $base_sha $branch"
      echo "  git push --force-with-lease $remote $branch"
      [ -n "$retarget" ] && echo "  gh pr edit $pr_num --base $retarget"
      [ "$dry" = 1 ] && continue

      local lease_sha; lease_sha="$(git -C "$repo" rev-parse "$remote/$branch" 2>/dev/null)"
      if [ -z "$lease_sha" ]; then
        echo "spawn-orchestrator: restack $task FAILED — no $remote/$branch to lease against (fail-closed; branch never pushed?)"
        failed=$((failed + 1))
        continue
      fi

      local tip rc
      tip="$(_restack_rebase_push "$repo" "$remote" "$branch" "$base_sha" "$onto_ref" "$lease_sha")"; rc=$?
      case "$rc" in
        3)
          echo "spawn-orchestrator: restack $task FAILED — rebase conflict (fail-closed, NOT force-pushing; a human must resolve)"
          failed=$((failed + 1)); continue ;;
        4)
          echo "spawn-orchestrator: restack $task FAILED — force-with-lease push rejected (fail-closed, remote moved since fetch; re-run restack)"
          failed=$((failed + 1)); continue ;;
      esac

      # The rebase + force-push already happened, so a child's downstream cascade
      # must see the new tip regardless of what the retarget does below.
      _RS_BASE_SHA[$i]="$onto_ref"   # this child's parent-tip is now the ref it sits on
      _RS_NEWTIP[$i]="$tip"          # so ITS children cascade onto the tip we just pushed
      restacked=$((restacked + 1)); progressed=1

      local retarget_ok=1
      if [ -n "$retarget" ]; then
        if "$gh_bin" pr edit "$pr_num" --base "$retarget" >/dev/null 2>&1; then
          _RS_BASE[$i]="$base_branch"
        else
          # Pushed but NOT retargeted: the PR still points at the merged parent
          # branch and reaches nothing — the exact QUIET orphan restack exists to
          # catch. Do NOT mark _RS_BASE as base_branch (that would hide it from
          # the orphan detector) and record it as a DEFECT, not a success.
          retarget_ok=0
          echo "spawn-orchestrator: restack $task DEFECT — rebased and force-pushed $branch, but 'gh pr edit --base $retarget' failed; PR #$pr_num still targets the merged parent branch (retarget by hand: gh pr edit $pr_num --base $retarget)"
          report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): rebased and force-pushed onto \`$onto_ref\`, but retargeting its PR base to \`$retarget\` FAILED — it still points at the merged parent branch and reaches nothing until a human runs \`gh pr edit $pr_num --base $retarget\` (finding #25, QUIET case).")
          flagged=$((flagged + 1))
        fi
      fi
      [ "$retarget_ok" = 1 ] || continue

      # --- Task 21: ENFORCE the re-verification — a clean rebase proves
      # nothing (run-state.md "Restack"). Everything below can still PARK this
      # child; only past all of it does it stay `handed-off`.
      local reverify_failed=0

      # 1. Re-run the run's OWN verify command against the new base. The
      # child's previous green ran against the OLD base and does not carry
      # over — this is mandatory for every restacked child, `onto-base` or
      # `cascade` alike.
      if [ -n "$verify_cmd" ]; then
        local vlog="$run_dir/.auto-pilot/restack-verify-$task.log"
        mkdir -p "$run_dir/.auto-pilot" 2>/dev/null
        if ! _restack_verify_at_tip "$repo" "$verify_cmd" "$tip" "$vlog"; then
          echo "spawn-orchestrator: restack $task DEFECT — re-verify FAILED against the new base $onto_ref (see $vlog); PARKING, not silently keeping a PR that no longer builds"
          _restack_park_and_alarm "$run_md" "$run_dir" "$task" "restack-reverify-verify" \
            "restack $task (PR #$pr_num): verify_command FAILED against the restacked base $onto_ref — its previous green ran against the OLD base and did not carry over"
          report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): restacked ($mode) onto \`$onto_ref\`, but the **re-run verify command FAILED** — PARKED and alarmed, not silently left \`handed-off\` on a build that no longer passes.")
          flagged=$((flagged + 1)); reverify_failed=1
        fi
      fi

      # 2/3. For an `onto-base` restack (the parent PR actually merged — a
      # human review happened in between), diff-audit the child against the
      # parent's post-hand-off review delta, and stale-flag co-review on file
      # overlap. `cascade` has no human review delta of its own to audit (the
      # parent's rewrite is mechanical, already audited when IT restacked) —
      # verify above is still mandatory for it, but the audit below is scoped
      # to `onto-base` only.
      if [ "$reverify_failed" = 0 ] && [ "$mode" = "onto-base" ] && ! _restack_empty "$parent_pr"; then
        local parent_final_tip pft_rc=0
        parent_final_tip="$(_restack_parent_final_tip "$repo" "$remote" "$gh_bin" "$parent_pr")"; pft_rc=$?
        if [ "$pft_rc" != 0 ]; then
          echo "spawn-orchestrator: restack $task DEFECT — could not determine parent PR #$parent_pr's final pre-merge tip (gh/network UNDETERMINED, rc=$pft_rc); PARKING rather than skipping the review-delta audit"
          _restack_park_and_alarm "$run_md" "$run_dir" "$task" "restack-reverify-undetermined" \
            "restack $task (PR #$pr_num): parent PR #$parent_pr's final pre-merge tip could not be positively determined — cannot audit its post-hand-off review delta"
          report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): restacked ($mode), but the parent's post-hand-off review delta could not be read (gh UNDETERMINED) — PARKED rather than trusting an unaudited rebase.")
          flagged=$((flagged + 1)); reverify_failed=1
        else
          local audit_out
          if ! audit_out="$(_restack_diff_audit "$repo" "$base_sha" "$parent_final_tip" "$tip")"; then
            echo "spawn-orchestrator: restack $task DEFECT — the rebase applied cleanly but DROPPED a line the parent's post-hand-off review added:"
            echo "$audit_out"
            _restack_park_and_alarm "$run_md" "$run_dir" "$task" "restack-reverify-audit" \
              "restack $task (PR #$pr_num): the rebase applied cleanly but silently dropped a line the parent's review commits added — $(printf '%s' "$audit_out" | head -1)"
            report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): restacked ($mode) — the rebase applied cleanly but **silently dropped** a line the parent's post-hand-off review added:\n\`\`\`\n$audit_out\n\`\`\`\nPARKED; a human must reconcile by hand.")
            flagged=$((flagged + 1)); reverify_failed=1
          else
            local overlap; overlap="$(_restack_file_overlap "$repo" "$base_sha" "$parent_final_tip" "$base_sha" "$lease_sha")"
            if [ -n "$overlap" ]; then
              mkdir -p "$run_dir/.auto-pilot/co-review-stale" 2>/dev/null
              printf '%s\n' "$overlap" >"$run_dir/.auto-pilot/co-review-stale/$task" 2>/dev/null
              _set_task_phase "$run_md" "$task" "iterating" 2>/dev/null \
                || echo "spawn-orchestrator: restack $task WARNING — could not move phase to iterating in $run_md" >&2
              echo "spawn-orchestrator: restack $task co-review STALE (parent's review touched: $(printf '%s' "$overlap" | tr '\n' ' ')) — phase handed-off -> iterating, hand-off BLOCKED until co-review-stale-clear"
              report+=("- **$task** (PR #$pr_num, \`$branch\`) co-review marked **STALE** — the parent's post-hand-off review touched files this child also touches (\`$(printf '%s' "$overlap" | tr '\n' ' ')\`). Phase moved \`handed-off\` → \`iterating\`; re-run \`/co-review --non-interactive\` and call \`co-review-stale-clear\` before hand-off (task 21).")
            fi
          fi
        fi
      fi
      if [ "$reverify_failed" = 1 ]; then
        # Do NOT let a grandchild cascade onto a tip that just failed its own
        # re-verification — the rebase+push already happened at the git level
        # (idempotency below is keyed off the PR's live base, not this), but a
        # PARKED parent must not look like a valid rewrite point for THIS pass.
        _RS_NEWTIP[$i]=""
        continue
      fi

      echo "spawn-orchestrator: restack $task done ($mode) — force-pushed $branch, PR #$pr_num${retarget:+ retargeted to $retarget}, re-verified against the new base"
      report+=("- **$task** (PR #$pr_num, \`$branch\`) restacked ($mode) onto \`$onto_ref\` and **re-verified**: verify_command passed against the new base$( [ "$mode" = "onto-base" ] && printf '%s' ", and the parent'"'"'s post-hand-off review delta was line-audited against the child'"'"'s post-rebase tree with no drop found" ).")
    done
  done

  # Orphan detection (ties to task 16's alarm channel): a chained PR reaching
  # here still pointed somewhere other than base_branch after this pass — flag
  # it as a defect rather than waiting for a human to notice by diffing the
  # open-PR list against RUN.md by hand (the only reason finding #25 was
  # caught at all).
  local i
  for ((i = 0; i < n; i++)); do
    local task="${_RS_TASK[$i]}" base="${_RS_BASE[$i]}" pr="${_RS_PR[$i]}"
    [ "$base" != "$base_branch" ] || continue
    _restack_empty "$pr" && continue
    local pr_num; pr_num="$(_pr_number "$pr")"
    local live_base live_state
    live_base="$("$gh_bin" pr view "$pr_num" --json baseRefName --jq .baseRefName 2>/dev/null)"
    live_state="$("$gh_bin" pr view "$pr_num" --json state --jq .state 2>/dev/null)"
    if [ -z "$live_base" ]; then
      echo "spawn-orchestrator: DEFECT $task PR #$pr_num — base ref deleted or unreadable (orphaned by a merged/closed parent, finding #25 LOUD case)"
      report+=("- **DEFECT — $task** (PR #$pr_num): base ref deleted or unreadable — orphaned by a merged/closed parent (finding #25, LOUD case). Needs a human.")
      flagged=$((flagged + 1))
      continue
    fi
    [ "$live_base" != "$base_branch" ] || continue
    local base_pr="" k
    for ((k = 0; k < n; k++)); do
      if [ "${_RS_BRANCH[$k]}" = "$live_base" ]; then base_pr="$(_pr_number "${_RS_PR[$k]}")"; break; fi
    done
    local base_state=""
    [ -n "$base_pr" ] && ! _restack_empty "$base_pr" \
      && base_state="$("$gh_bin" pr view "$base_pr" --json state --jq .state 2>/dev/null)"
    if [ "$live_state" = "CLOSED" ] || [ "$base_state" = "MERGED" ]; then
      echo "spawn-orchestrator: DEFECT $task PR #$pr_num — base=$live_base is a merged/closed branch; this PR never reaches $base_branch (finding #25 QUIET case)"
      report+=("- **DEFECT — $task** (PR #$pr_num): base \`$live_base\` is a merged/closed branch, so this PR never reaches \`$base_branch\` — it looks healthy while doing nothing (finding #25, QUIET case). Needs a human.")
      flagged=$((flagged + 1))
    fi
  done

  # Surface the restack outcome where a HUMAN actually reads it. stdout only
  # reaches orchestrator.log; REPORT.md is the file the human wakes up to, and
  # the stale-co-review warning is worthless if it lands somewhere they never
  # look. Append-only (never rewrites the report), and skipped entirely when
  # there is nothing to say — so an idempotent no-op restack does not churn it.
  if [ "${#report[@]}" -gt 0 ]; then
    local report_md="$run_dir/.auto-pilot/REPORT.md"
    {
      printf '\n## Restack — %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf '%s\n' "${report[@]}"
    } >>"$report_md" 2>/dev/null \
      || echo "spawn-orchestrator: restack WARNING — could not append to $report_md (findings are on stdout only)"
  fi

  # Assert the HEAD invariant on EVERY exit path (finding #23 / task 13): the
  # run worktree's HEAD must be exactly where restack found it.
  local head_after ref_after
  head_after="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
  ref_after="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  { [ "$head_after" = "$_RS_HEAD_BEFORE" ] && [ "$ref_after" = "$_RS_REF_BEFORE" ]; } \
    || die "restack moved the caller's HEAD ($_RS_REF_BEFORE@$_RS_HEAD_BEFORE -> $ref_after@$head_after) — this is a bug, not a recoverable state"

  echo "spawn-orchestrator: restack summary — restacked=$restacked failed=$failed defects=$flagged"
  # Non-zero on EITHER a hard failure or a flagged defect (a LOUD/QUIET orphan, a
  # failed retarget): a supervisor checking `$?` must see that a human is needed,
  # not read a defects run as a clean success.
  { [ "$failed" -eq 0 ] && [ "$flagged" -eq 0 ]; } || return 2
}

# The stale-marker path a restacked child's overlap check writes to
# (`restack`, above) and these two verbs gate/clear.
_restack_stale_marker() { printf '%s/.auto-pilot/co-review-stale/%s' "$1" "$2"; }

# Read-only gate: a hand-off step calls this BEFORE writing `needs_review`.
# Exit 0 (clear) when no stale marker exists for --task; exit 3 (BLOCKED) when
# `restack` flagged it and it has not been cleared. Never mutates anything —
# task 21's "hand-off stays blocked until the refreshed review passes" is
# enforced by the CALLER checking this exit code, not by this verb acting.
co_review_stale_check() {
  local run_dir="" task=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --run-dir) [ $# -ge 2 ] || die "missing value for --run-dir"; run_dir="$2"; shift 2 ;;
      --task) [ $# -ge 2 ] || die "missing value for --task"; task="$2"; shift 2 ;;
      *) die "unknown co-review-stale-check argument: $1" ;;
    esac
  done
  [ -n "$run_dir" ] && [ -n "$task" ] || die "co-review-stale-check requires --run-dir and --task"
  local marker; marker="$(_restack_stale_marker "$run_dir" "$task")"
  if [ -f "$marker" ]; then
    echo "spawn-orchestrator: co-review-stale-check: $task is STALE — files: $(tr '\n' ' ' <"$marker")"
    return 3
  fi
  echo "spawn-orchestrator: co-review-stale-check: $task is clear"
}

# Clear a task's stale marker and move its phase back to `handed-off` — called
# AFTER `/co-review --non-interactive` has been re-run on the restacked child
# and passed (skills/deliver-task/SKILL.md "Co-review"). This is task 21's
# legal transition BACK OUT of `iterating`, matching the one restack put it
# into (run-state.md "Restack" / "Task lifecycle phases").
co_review_stale_clear() {
  local run_dir="" task="" questions=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --run-dir) [ $# -ge 2 ] || die "missing value for --run-dir"; run_dir="$2"; shift 2 ;;
      --task) [ $# -ge 2 ] || die "missing value for --task"; task="$2"; shift 2 ;;
      --questions) [ $# -ge 2 ] || die "missing value for --questions"; questions="$2"; shift 2 ;;
      *) die "unknown co-review-stale-clear argument: $1" ;;
    esac
  done
  [ -n "$run_dir" ] && [ -n "$task" ] || die "co-review-stale-clear requires --run-dir and --task"
  local marker; marker="$(_restack_stale_marker "$run_dir" "$task")"
  [ -f "$marker" ] || die "co-review-stale-clear: $task has no stale marker (fail-closed — nothing to clear): $marker"
  rm -f "$marker" || die "co-review-stale-clear: could not remove $marker"
  _set_task_phase "$run_dir/.auto-pilot/RUN.md" "$task" "handed-off" \
    || die "co-review-stale-clear: could not move $task's phase back to handed-off"
  if [ -n "$questions" ]; then
    case "$questions" in /*) ;; *) questions="$run_dir/$questions" ;; esac
    _doctor_questions_entry "$questions" "$task — restacked child's co-review re-run" \
      "re-run co-review | trust the stale approval" \
      "re-run co-review — the parent's post-hand-off review touched files this child also touches" \
      "the child's restacked tip was re-verified and its diff-audit against the parent's review delta found no dropped line, then /co-review --non-interactive was re-run and passed" \
      "no — this re-review is what un-staled it"
  fi
  echo "spawn-orchestrator: co-review-stale-clear: $task cleared — phase iterating -> handed-off"
}

# Orchestrate the spawn in the ONE safe order: build the launch artifacts, then
# smoke-test auth THROUGH the wrapper, and only if that passes, detach + record.
# The ordering is load-bearing — detaching before auth is verified is exactly the
# "fails silently at 3am" mode the pre-flight exists to prevent. `--dry-run` prints
# the plan without executing (so the order is testable offline).
launch() {
  local dry=0
  local -a wl=() sm=() dt=() rh=()
  local plist="" out_script="" out_plist="" handle="" until="" label=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --profile) wl+=(--profile "$2"); sm+=(--profile "$2"); shift 2 ;;
      --settings) wl+=(--settings "$2"); sm+=(--settings "$2"); shift 2 ;;
      --workdir|--log|--prompt-file|--interval|--throttle|--plist-template|--claude-bin|--path|--tmpdir) wl+=("$1" "$2"); shift 2 ;;
      --until) wl+=(--until "$2"); until="$2"; shift 2 ;;
      --label) wl+=(--label "$2"); label="$2"; shift 2 ;;
      --out-script) wl+=(--out-script "$2"); out_script="$2"; shift 2 ;;
      --out-plist) wl+=(--out-plist "$2"); out_plist="$2"; plist="$2"; shift 2 ;;
      --handle) handle="$2"; shift 2 ;;
      *) die "unknown launch argument: $1" ;;
    esac
  done
  [ -n "$out_plist" ] && [ -n "$label" ] && [ -n "$handle" ] || die "launch requires --out-plist, --label, and --handle"

  if [ "$dry" = 1 ]; then
    printf 'launch plan (order is load-bearing — auth is verified BEFORE we spawn):\n'
    printf '  1. write-launch  -> %s %s\n' "$out_script" "$out_plist"
    printf '  2. smoke-test    (claude -p through the sandbox wrapper + settings)\n'
    printf '  3. detach        (launchctl bootstrap %s)\n' "$plist"
    printf '  4. record-handle -> %s\n' "$handle"
    return 0
  fi

  write_launch "${wl[@]}"
  smoke_test "${sm[@]}"            # BEFORE detach — a dead credential stops here.
  teardown --label "$label" >/dev/null 2>&1   # clear any stale registration; bootstrap fails on a dup label.
  detach --plist "$plist"
  # `launchctl bootstrap` is asynchronous — the pid may not be reported for a beat,
  # so poll briefly rather than reading once (a single read fails closed spuriously).
  local pid="" i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pid="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk -F'= ' '/[^a-z]pid = /{print $2; exit}')"
    case "$pid" in ''|*[!0-9]*) pid="" ;; *) break ;; esac
    sleep 0.2
  done
  if [ -z "$pid" ]; then
    teardown --label "$label" >/dev/null 2>&1   # don't leave an orphaned job loaded
    die "detached but could not read the orchestrator PID from launchctl (job booted out)"
  fi
  record_handle --pid "$pid" --until "$until" --out "$handle"
  echo "spawn-orchestrator: launched pid=$pid label=$label"
}

# ---------------------------------------------------------------------------
# Task 4 — verify broker: run the run's verify_command OUTSIDE the jail.
#
# A sandbox-exec-confined process's children INHERIT the profile, so the jailed
# orchestrator cannot spawn an un-jailed verifier — and the run's verify
# (`bash scripts/check.sh`, which runs `bash scripts/test-*.sh`) execve-denies
# in-jail (finding #4: `bad interpreter: Operation not permitted`, exit 126,
# regardless of the diff). Verify therefore runs in a SEPARATE, UN-JAILED launchd
# job (write-verify-broker) that polls a sentinel dir and runs a COMMAND-PINNED
# verify in the requested worktree:
#
#   jailed orchestrator             un-jailed broker (launchd, NO sandbox-exec)
#   -------------------             ------------------------------------------
#   verify-request  ── writes ──▶   <dir>/<id>.request {worktree, cmd_hash}
#                                   verify-broker: refuse unless the request's
#                                   cmd_hash == the broker's OWN pinned hash AND
#                                   the worktree is under the run root, then run
#                                   its OWN pinned command there and write
#   verify-await   ◀── reads ───    <dir>/<id>.result  {code, output}
#
# Trust boundary (launch-runtime.md "Sandbox profile"): verify runs un-jailed, so
# it executes the diff-under-test with full privilege + network — the SAME trust
# the human extends re-running check.sh before merge, moved earlier. It is bounded
# by (a) command PINNING — the broker runs a FIXED string baked in at install,
# NEVER a command from the request; the request carries only a hash both sides must
# agree on — and (b) worktree-only execution — the broker refuses a worktree
# outside the run root. Neither the request nor an agent ever supplies the command.
# ---------------------------------------------------------------------------

# Pin hash of a verify command string (sha256 hex). Request and broker compute it
# identically, so a stale request or a re-pinned broker is caught before any run.
verify_hash() {
  command -v shasum >/dev/null 2>&1 || die "shasum not available (needed for verify pin)"
  printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
}

# Atomically write a result file: `code:` on line 1, then the raw output after a
# fixed marker so verify-await can split it back out losslessly.
_write_verify_result() {
  local f="$1" code="$2" out="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.res.XXXXXX")" || return 1
  { printf 'code: %s\n' "$code"; printf -- '--- output ---\n'; printf '%s\n' "$out"; } >"$tmp" \
    || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$f" || { rm -f "$tmp"; return 1; }
}

# (jailed side) Drop a verify request the un-jailed broker will pick up. The
# request names the worktree + a hash of the pinned command — never the command.
verify_request() {
  local dir="" worktree="" cmd_hash="" id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --worktree) [ $# -ge 2 ] || die "missing value for --worktree"; worktree="$2"; shift 2 ;;
      --cmd-hash) [ $# -ge 2 ] || die "missing value for --cmd-hash"; cmd_hash="$2"; shift 2 ;;
      --id) [ $# -ge 2 ] || die "missing value for --id"; id="$2"; shift 2 ;;
      *) die "unknown verify-request argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$worktree" ] && [ -n "$cmd_hash" ] \
    || die "verify-request requires --sentinel-dir, --worktree, and --cmd-hash"
  case "$cmd_hash" in *[!0-9a-f]*|"") die "--cmd-hash must be lowercase hex (fail-closed): $cmd_hash" ;; esac
  local wt; wt="$(canonicalize "$worktree")" || exit 2
  [ -d "$wt" ] || die "verify-request --worktree is not a directory (fail-closed): $wt"
  mkdir -p "$dir" || die "cannot create sentinel dir: $dir"
  local d; d="$(cd "$dir" && pwd -P)" || die "cannot resolve sentinel dir: $dir"
  if [ -n "$id" ]; then
    case "$id" in *[!A-Za-z0-9._-]*) die "--id must be [A-Za-z0-9._-] (fail-closed): $id" ;; esac
  else
    local t; t="$(mktemp "$d/req.XXXXXX")" || die "mktemp failed"; id="$(basename "$t")"; rm -f "$t"
  fi
  local reqfile="$d/$id.request" tmp
  tmp="$(mktemp "$d/.req.XXXXXX")" || die "mktemp failed"
  { printf 'worktree: %s\n' "$wt"; printf 'cmd_hash: %s\n' "$cmd_hash"; } >"$tmp" \
    || { rm -f "$tmp"; die "cannot write request"; }
  mv "$tmp" "$reqfile" || { rm -f "$tmp"; die "cannot place request: $reqfile"; }
  echo "spawn-orchestrator: verify-request $id"
}

# (UN-JAILED side, run by the broker launchd job) One scan: for each pending
# request, refuse unless its cmd_hash matches the broker's OWN pinned hash and the
# worktree is under the run root, then run the PINNED command there and write the
# result. Scans once and exits — the launchd StartInterval drives the cadence.
verify_broker() {
  local dir="" cmd="" cmd_hash="" root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; cmd="$2"; shift 2 ;;
      --cmd-hash) [ $# -ge 2 ] || die "missing value for --cmd-hash"; cmd_hash="$2"; shift 2 ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; root="$2"; shift 2 ;;
      *) die "unknown verify-broker argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$cmd" ] && [ -n "$root" ] \
    || die "verify-broker requires --sentinel-dir, --verify-cmd, and --confine-under"
  # The broker runs its OWN pinned command; --cmd-hash (if given) must match it —
  # this catches an install/args mismatch, never authorizes a request's command.
  local pin; pin="$(verify_hash "$cmd")"
  if [ -n "$cmd_hash" ] && [ "$cmd_hash" != "$pin" ]; then
    die "verify-broker --cmd-hash does not match --verify-cmd (fail-closed)"
  fi
  local rootc; rootc="$(canonicalize "$root")" || exit 2
  local d; d="$(cd "$dir" 2>/dev/null && pwd -P)" || die "sentinel dir not found: $dir"

  local req handled=0
  for req in "$d"/*.request; do
    [ -e "$req" ] || continue
    handled=$((handled + 1))
    local id; id="$(basename "$req" .request)"
    local resultf="$d/$id.result"
    local wt rh
    wt="$(sed -n 's/^worktree: //p' "$req" | head -1)"
    rh="$(sed -n 's/^cmd_hash: //p' "$req" | head -1)"
    rm -f "$req"    # claim it — a handled request never re-runs
    if [ "$rh" != "$pin" ]; then
      _write_verify_result "$resultf" 2 "verify-broker: request cmd_hash mismatch (pinned=$pin request=$rh) — refused"
      continue
    fi
    local wtc; wtc="$(cd "$wt" 2>/dev/null && pwd -P)"
    if [ -z "$wtc" ] || { [ "$wtc" != "$rootc" ] && [ "${wtc#"$rootc"/}" = "$wtc" ]; }; then
      _write_verify_result "$resultf" 2 "verify-broker: worktree escapes --confine-under (worktree=$wt root=$rootc) — refused"
      continue
    fi
    # Run the PINNED command in the worktree. `bash -c "$cmd"` on a trusted, pinned
    # string is the same execution the human runs re-invoking check.sh pre-merge.
    local out code
    out="$(cd "$wtc" && bash -c "$cmd" 2>&1)"; code=$?
    _write_verify_result "$resultf" "$code" "$out"
  done
  echo "spawn-orchestrator: verify-broker scanned $d (handled=$handled, pin=$pin)"
}

# (jailed side) Block until the broker writes the result for <id>, then print its
# code + output and exit with the verify command's code (0 = verify passed).
verify_await() {
  local dir="" id="" timeout=600 interval=2
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --id) [ $# -ge 2 ] || die "missing value for --id"; id="$2"; shift 2 ;;
      --timeout) [ $# -ge 2 ] || die "missing value for --timeout"; timeout="$2"; shift 2 ;;
      --interval) [ $# -ge 2 ] || die "missing value for --interval"; interval="$2"; shift 2 ;;
      *) die "unknown verify-await argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$id" ] || die "verify-await requires --sentinel-dir and --id"
  case "$timeout$interval" in *[!0-9]*) die "--timeout/--interval must be integers" ;; esac
  [ "$interval" -gt 0 ] || die "--interval must be > 0 (else the wait loop never advances)"
  local d; d="$(cd "$dir" 2>/dev/null && pwd -P)" || die "sentinel dir not found: $dir"
  local resultf="$d/$id.result" waited=0
  while [ ! -e "$resultf" ]; do
    [ "$waited" -lt "$timeout" ] || die "verify-await timed out after ${timeout}s waiting for $id (broker down?)"
    sleep "$interval"; waited=$((waited + interval))
  done
  local code; code="$(sed -n 's/^code: //p' "$resultf" | head -1)"
  echo "spawn-orchestrator: verify-await $id code=$code"
  sed -n '/^--- output ---$/,$p' "$resultf" | sed '1d'
  case "$code" in ''|*[!0-9]*) return 2 ;; *) return "$code" ;; esac
}

# Render an UN-JAILED broker launch script + its launchd plist. The verify command
# is PINNED here (baked into the script from the run's resolved verify_command), so
# the broker never runs anything a request or an agent supplies. The broker job runs
# `/bin/bash <script>` directly — NO sandbox-exec — which is exactly how it escapes
# the orchestrator's jail to reach a working execve.
write_verify_broker() {
  local sentinel="" verify_cmd="" root="" label="" workdir="" log="" path="" \
        self="$ROOT/scripts/spawn-orchestrator.sh" interval="10" throttle="10" \
        out_script="" out_plist="" plist_template="$PLIST_TEMPLATE_DEFAULT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; sentinel="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; verify_cmd="$2"; shift 2 ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; root="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --workdir) [ $# -ge 2 ] || die "missing value for --workdir"; workdir="$2"; shift 2 ;;
      --log) [ $# -ge 2 ] || die "missing value for --log"; log="$2"; shift 2 ;;
      --path) [ $# -ge 2 ] || die "missing value for --path"; path="$2"; shift 2 ;;
      --self) [ $# -ge 2 ] || die "missing value for --self"; self="$2"; shift 2 ;;
      --interval) [ $# -ge 2 ] || die "missing value for --interval"; interval="$2"; shift 2 ;;
      --throttle) [ $# -ge 2 ] || die "missing value for --throttle"; throttle="$2"; shift 2 ;;
      --out-script) [ $# -ge 2 ] || die "missing value for --out-script"; out_script="$2"; shift 2 ;;
      --out-plist) [ $# -ge 2 ] || die "missing value for --out-plist"; out_plist="$2"; shift 2 ;;
      --plist-template) [ $# -ge 2 ] || die "missing value for --plist-template"; plist_template="$2"; shift 2 ;;
      *) die "unknown write-verify-broker argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-verify-broker requires --out-script and --out-plist"
  [ -n "$sentinel" ] && [ -n "$verify_cmd" ] && [ -n "$root" ] \
    || die "write-verify-broker requires --sentinel-dir, --verify-cmd, and --confine-under"
  [ -n "$label" ] || die "write-verify-broker requires --label"
  case "$label" in *[!A-Za-z0-9._-]*) die "--label must be [A-Za-z0-9._-] (fail-closed): $label" ;; esac
  [ -n "$path" ] || die "write-verify-broker requires --path <PATH> (a launchd job has a minimal PATH)"
  [ -n "$workdir" ] && [ -d "$workdir" ] || die "write-verify-broker requires an existing --workdir"
  [ -n "$log" ] || die "write-verify-broker requires --log"
  [ -f "$self" ] || die "spawn-orchestrator.sh not found (fail-closed): $self"
  [ -f "$plist_template" ] || die "plist template not found: $plist_template"
  case "$interval$throttle" in *[!0-9]*) die "--interval/--throttle must be integers" ;; esac

  local pin; pin="$(verify_hash "$verify_cmd")"
  local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/verify-broker-launch.XXXXXX")" || die "mktemp failed"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Auto-pilot verify BROKER (generated — do not edit). Runs UN-JAILED so\n'
    printf '# the run verify (bash scripts/check.sh) reaches a working execve. The\n'
    printf '# verify command below is PINNED (pin=%s); the broker never runs a\n' "$pin"
    printf '# command supplied by a request or an agent.\n'
    printf 'set -uo pipefail\n'
    printf 'export PATH=%q\n' "$path"
    printf 'cd %q\n' "$workdir"
    printf 'exec /bin/bash %q verify-broker \\\n' "$self"
    printf '  --sentinel-dir %q \\\n' "$sentinel"
    printf '  --verify-cmd %q \\\n' "$verify_cmd"
    printf '  --cmd-hash %q \\\n' "$pin"
    printf '  --confine-under %q \\\n' "$root"
    printf '  >>%q 2>&1\n' "$log"
  } >"$tmp" || { rm -f "$tmp"; die "failed to write broker launch script"; }
  mv "$tmp" "$out_script" || { rm -f "$tmp"; die "failed to write broker launch script: $out_script"; }
  chmod +x "$out_script"

  tmp="$(mktemp "${TMPDIR:-/tmp}/verify-broker-plist.XXXXXX")" || die "mktemp failed"
  render_plist "$label" "$out_script" "$workdir" "$log" "$interval" "$throttle" "$plist_template" >"$tmp" \
    || { rm -f "$tmp"; die "failed to render broker plist"; }
  mv "$tmp" "$out_plist" || { rm -f "$tmp"; die "failed to write broker plist: $out_plist"; }
  echo "spawn-orchestrator: verify-broker written $out_script $out_plist (pin $pin)"
}

# Normalize whitespace (collapse runs, trim ends) so a recorded lstart string
# and a freshly-read one compare equal regardless of incidental spacing.
_norm_ws() { printf '%s' "$1" | awk '{$1=$1; print}'; }

# A file's mtime as an epoch, or empty. BSD (`stat -f %m`) then GNU (`stat -c %Y`)
# so the reader works on macOS and Linux CI alike — the result is VALIDATED numeric
# rather than trusted, because GNU `stat -f %m` does not fail: it reads `-f` as
# "filesystem status" and happily prints a mount point. Used to age the
# done-sentinel against RUN.md's `exit_reason_at` — a sentinel older than the live
# declaration is a leftover, not the run's current verdict.
_file_mtime() {
  local m; m="$(stat -f %m "$1" 2>/dev/null)" || m=""
  case "$m" in ''|*[!0-9]*) m="$(stat -c %Y "$1" 2>/dev/null)" || m="" ;; esac
  case "$m" in ''|*[!0-9]*) m="" ;; esac
  printf '%s' "$m"
}

# Pull a single top-level "key: value" field out of a captured front-matter
# block (global $front, set by status() before calling this). Anchors on
# `^key:` so e.g. "until:" never matches "paused_until:". Strips a trailing
# ` # comment`, trailing whitespace, then a wrapping pair of single or double
# quotes.
_front_field() {
  local key="$1"
  printf '%s\n' "$front" | grep -E "^${key}:" | head -1 \
    | sed -e "s/^${key}: *//" -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//' \
          -e "s/^'\(.*\)'\$/\1/" -e 's/^"\(.*\)"$/\1/'
}

# The stale-orchestrator check (launch-runtime.md "Orphan / stale detection"):
# given RUN.md's recorded `orchestrator_pid` / `orchestrator_started_at`, print
# exactly one of live | dead | mismatch | none. LIVE only if the PID is alive
# AND its process start-time matches the recorded one — the start-time is what
# tells a live orchestrator apart from a RECYCLED pid (`mismatch`, i.e. the
# recorded process is gone). `none` = nothing recorded, so liveness is
# UNDETERMINED, not "dead". ONE implementation, shared by `status` and doctor's
# invariant 5 — a second, divergent liveness check is exactly how the two would
# drift apart on the one question a destructive prune depends on.
_pid_state() {
  local pid="$1" started="$2"
  [ -n "$pid" ] || { printf 'none'; return 0; }
  if kill -0 "$pid" 2>/dev/null; then
    local actual; actual="$(ps -o lstart= -p "$pid" 2>/dev/null)"
    if [ -n "$actual" ] && [ "$(_norm_ws "$actual")" = "$(_norm_ws "$started")" ]; then
      printf 'live'
    else
      printf 'mismatch'
    fi
  else
    printf 'dead'
  fi
}

# Read-only: report the run's live state in one shot. Never writes anything.
status() {
  local label="" dir="$PWD" ceiling="$DEFAULT_TASK_CEILING"
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --task-ceiling) [ $# -ge 2 ] || die "missing value for --task-ceiling"; ceiling="$2"; shift 2 ;;
      *) die "unknown status argument: $1" ;;
    esac
  done
  [ -n "$label" ] || die "status requires --label"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  case "$ceiling" in ''|*[!0-9]*) die "--task-ceiling must be a positive integer (seconds): $ceiling" ;; esac
  [ "$ceiling" -ge 1 ] || die "--task-ceiling must be a positive integer (seconds): $ceiling"

  local run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"
  local log="$dir/.auto-pilot/orchestrator.log"
  local sentinel="$dir/.auto-pilot/$DONE_SENTINEL_NAME"
  local hb="$dir/.auto-pilot/$HEARTBEAT_NAME"

  # Front matter is the block between the first two `---` lines. No YAML tool
  # required — plain awk/sed line matching.
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"

  local run_status until_val orch_pid orch_started
  run_status="$(_front_field status)"; [ -n "$run_status" ] || run_status="unknown"
  until_val="$(_front_field until)"
  orch_pid="$(_front_field orchestrator_pid)"
  orch_started="$(_front_field orchestrator_started_at)"

  # Per-task phase table: every `| ... |` line after the front matter.
  local table; table="$(awk '/^\|/{print}' "$run_md")"
  local task_rows task_count
  task_rows="$(printf '%s\n' "$table" | grep -Ev '^\| *:?-+:? *(\| *:?-+:? *)*\|?$' || true)"
  if [ -n "$task_rows" ]; then
    task_count=$(( $(printf '%s\n' "$task_rows" | wc -l | tr -d ' ') - 1 )) # minus header row
    [ "$task_count" -ge 0 ] || task_count=0
  else
    task_count=0
  fi

  # Last meaningful event from the stream-json orchestrator.log: the last line
  # that looks like an assistant or tool event, else just the last line.
  # Degrades gracefully if the log is absent/empty.
  local last_event=""
  if [ -s "$log" ]; then
    last_event="$(grep -E '"type":"(assistant|tool_use|tool_result)"' "$log" 2>/dev/null | tail -1)"
    [ -n "$last_event" ] || last_event="$(tail -1 "$log")"
    last_event="$(printf '%s' "$last_event" | cut -c1-240)"
  fi

  local pid_state; pid_state="$(_pid_state "$orch_pid" "$orch_started")"

  # Done-sentinel (written by `teardown --done-sentinel` / `exit-reason` on a
  # terminal reason) is the single source of "the run stopped for good" — it can
  # mark the run stopped even before RUN.md's own `status:` field is committed.
  # It carries WHICH terminal reason it stands for; only `done` displays as done
  # (a pre-task-15 sentinel, with no `reason:` line, is a `done` sentinel), so a
  # `systemic` halt never reads back as a clean finish.
  local sentinel_done="no" sentinel_reason="" sentinel_at=""
  if [ -f "$sentinel" ]; then
    sentinel_done="yes"
    sentinel_reason="$(sed -n 's/^reason: //p' "$sentinel" | head -1)"
    _is_exit_reason "$sentinel_reason" || sentinel_reason="done"
    sentinel_at="$(_file_mtime "$sentinel")"
  fi

  # The exit reason (task 15). The sentinel is written LAST on the terminal path, so
  # it normally wins — but NOT unconditionally. The sentinel is durable and a
  # `--resume` (the documented recovery from `deadline`) brings the run back to
  # life; a sentinel that unconditionally out-voted RUN.md would make that resumed,
  # RUNNING run read back as finished forever. So prefer whichever is FRESHER: a
  # RUN.md declaration written AFTER the sentinel (a live run that has since
  # declared `continuing`) means the sentinel is stale.
  local exit_r declared_at
  exit_r="$(_front_field exit_reason)"
  _is_exit_reason "$exit_r" || exit_r=""
  declared_at="$(_front_field exit_reason_at)"
  case "$declared_at" in *[!0-9]*) declared_at="" ;; esac

  if [ -n "$sentinel_reason" ]; then
    if [ -n "$exit_r" ] && [ -n "$declared_at" ] && [ -n "$sentinel_at" ] \
       && [ "$declared_at" -gt "$sentinel_at" ]; then
      sentinel_done="stale"
    else
      exit_r="$sentinel_reason"
    fi
  fi

  # Only a live `done` sentinel displays as a finished run (a pre-task-15 sentinel,
  # with no `reason:` line, is a `done` sentinel), so a `systemic` halt or a
  # `deadline` stop never reads back as a clean finish.
  local run_status_display="$run_status"
  [ "$sentinel_done" = "yes" ] && [ "$exit_r" = "done" ] && run_status_display="done"

  # Is a relaunch expected? This is the question the whole exit contract exists to
  # answer, so `status` answers it outright instead of leaving a human to infer it
  # from an exit code that says the same thing (0) either way. A LIVE terminal
  # reason or an un-superseded sentinel means the supervisor has torn down; anything
  # else means the launchd timer is still expected to wake this run.
  local relaunch="yes"
  if [ "$sentinel_done" = "yes" ] || { [ -n "$exit_r" ] && _is_terminal_reason "$exit_r"; }; then
    relaunch="no"
  fi

  # Heartbeat: the ONLY signal that separates slow from wedged. Aged against the
  # per-task ceiling — a beat older than the ceiling means no /deliver-task
  # sub-step boundary has been crossed in longer than a whole task is allowed to
  # take, which is a stall, not slowness.
  local hb_state="none" hb_age="" hb_at="" hb_iso="" hb_note="" hb_line
  if [ -f "$hb" ]; then
    hb_at="$(sed -n 's/^at: //p' "$hb" | head -1)"
    hb_iso="$(sed -n 's/^iso: //p' "$hb" | head -1)"
    hb_note="$(sed -n 's/^note: //p' "$hb" | head -1)"
  fi
  case "$hb_at" in
    ''|*[!0-9]*) hb_line="(no heartbeat file — a pre-heartbeat run, or the orchestrator never started a loop iteration)" ;;
    *)
      hb_age=$(( $(date +%s) - hb_at ))
      [ "$hb_age" -ge 0 ] || hb_age=0
      if [ "$hb_age" -gt "$ceiling" ]; then
        hb_state="stale"
        hb_line="${hb_iso:-?} (${hb_age}s ago, per-task ceiling ${ceiling}s) — STALL: no heartbeat for longer than a whole task is allowed to take${hb_note:+ [last: $hb_note]}"
      else
        hb_state="healthy"
        hb_line="${hb_iso:-?} (${hb_age}s ago, per-task ceiling ${ceiling}s) — healthy${hb_note:+ [last: $hb_note]}"
      fi
      ;;
  esac

  # The ALARM sentinel (task 16): the whole point is that a halted/stalled run is
  # visible from shell with no model call, so the one-shot state reporter must
  # show it — not only the desktop notification the human may have missed.
  local alarm_file="$dir/.auto-pilot/$ALARM_SENTINEL_NAME" alarm_count=0 alarm_display="none"
  if [ -f "$alarm_file" ]; then
    alarm_count="$(grep -c '^condition: ' "$alarm_file" 2>/dev/null | tr -d ' ')"
    case "$alarm_count" in ''|*[!0-9]*) alarm_count=0 ;; esac
    alarm_display="$(grep '^condition: ' "$alarm_file" 2>/dev/null | sed 's/^condition: //' | tr '\n' ',' | sed -e 's/,$//' -e 's/,/, /g')"
    [ -n "$alarm_display" ] || alarm_display="unreadable"
  fi

  echo "run: $label"
  echo "dir: $dir"
  echo "status: $run_status_display (front-matter: $run_status; done-sentinel: $sentinel_done)"
  echo
  echo "tasks:"
  if [ -n "$table" ]; then
    printf '%s\n' "$table"
  else
    echo "  (no task table found in RUN.md)"
  fi
  echo
  echo "log: ${last_event:-(orchestrator.log absent, empty, or no assistant/tool events found)}"
  echo "pid: $pid_state (pid=${orch_pid:-none} started=\"${orch_started:-none}\")"
  echo "until: ${until_val:-(none)}"
  if [ "$alarm_count" -gt 0 ]; then
    echo "alarm: $alarm_display (see $alarm_file)"
  else
    echo "alarm: none"
  fi
  echo "heartbeat: $hb_line"
  echo "exit_reason: ${exit_r:-(none declared — the orchestrator has not exited, or was hard-killed before it could)} (relaunch expected: $relaunch)"
  echo "STATUS: $run_status_display pid=$pid_state tasks=$task_count until=${until_val:-none} alarms=$alarm_count heartbeat=$hb_state${hb_age:+ heartbeat_age=${hb_age}s} exit_reason=${exit_r:-none} relaunch=$relaunch"
}

# ---------------------------------------------------------------------------
# Task 13 — HEAD guard: the run worktree's HEAD must stay on the run-state
# branch `auto-pilot/<run_id>` for the entire run (skills/auto-pilot/references/
# run-state.md "Run worktree HEAD invariant"). Task code belongs in a SEPARATE
# worker worktree (owned/torn-down by /deliver-task); an orchestrator that
# instead `git checkout`s a task branch INSIDE the run worktree wedges --resume,
# which reads .auto-pilot/RUN.md off this exact worktree. This guard runs at
# every run-loop iteration and at the top of --resume.
# ---------------------------------------------------------------------------

# Assert HEAD in the run worktree is `auto-pilot/<run-id>`; if not, restore it
# and (with --questions) record the deviation as a QUESTIONS.md entry — which
# then reaches REPORT.md via the existing rolling rewrite (no new REPORT.md
# section invented; run-state.md owns that format). A CLEAN deviation is
# restored and the run proceeds, per the task's "restore before proceeding"
# directive; a deviation with a dirty run worktree is fail-closed (restoring
# would carry or lose the uncommitted edits), as is a restore git itself refuses.
assert_run_head() {
  local dir="" run_id="" questions="" ignore_untracked_run_state=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --run-id) [ $# -ge 2 ] || die "missing value for --run-id"; run_id="$2"; shift 2 ;;
      --questions) [ $# -ge 2 ] || die "missing value for --questions"; questions="$2"; shift 2 ;;
      --ignore-untracked-run-state) ignore_untracked_run_state=1; shift ;;
      *) die "unknown assert-run-head argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$run_id" ] || die "assert-run-head requires --dir and --run-id"
  [ -e "$dir/.git" ] || die "assert-run-head: not a git worktree (fail-closed): $dir"
  local expected="auto-pilot/$run_id"
  local actual
  actual="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)" \
    || die "assert-run-head: cannot read HEAD in $dir"
  if [ "$actual" = "$expected" ]; then
    echo "spawn-orchestrator: HEAD OK ($expected)"
    return 0
  fi
  # A deviation with a DIRTY run worktree is the wedge case: a non-conflicting
  # `git checkout` would silently carry those uncommitted (task-branch) edits onto
  # the run-state branch, and a conflicting one blocks the restore. Either way,
  # fail closed rather than restore — the guard only repairs a CLEAN deviation.
  #
  # --ignore-untracked-run-state (doctor, task 14 / D1): a real run worktree
  # ALWAYS carries untracked files under .auto-pilot/ — orchestrator.log,
  # verify-broker.log, this doctor's own doctor-state — because they are
  # written straight to disk, never `git add`ed. `git reset`/`git checkout`
  # cannot discard untracked files, so without this flag the dirty check
  # below never clears in a real run and this guard permanently fail-closes.
  # We do NOT `git clean` them away either: those logs are the run's only
  # forensic record, and deleting them to unblock a HEAD repair is exactly
  # the "destroys what a human would miss" failure this guard exists to
  # avoid. Untracked `.auto-pilot/` paths are therefore filtered out of the
  # dirty check and left on disk, untouched; every other kind of dirt —
  # tracked changes anywhere, or untracked files OUTSIDE `.auto-pilot/` —
  # still fails closed exactly as before. Callers other than doctor never
  # pass this flag, so their behavior is unchanged.
  local dirty
  dirty="$(git -C "$dir" status --porcelain 2>/dev/null)"
  if [ "$ignore_untracked_run_state" = 1 ] && [ -n "$dirty" ]; then
    dirty="$(printf '%s\n' "$dirty" | grep -v '^?? \.auto-pilot/')"
  fi
  if [ -n "$dirty" ]; then
    die "assert-run-head: HEAD is on '$actual', not '$expected', and the run worktree has uncommitted changes (fail-closed) — restoring would carry or lose them; resolve by hand"
  fi
  # A detached HEAD reads back as the literal "HEAD"; record its SHA so the
  # parked ref (and any commit made there) stays findable after the restore.
  case "$actual" in HEAD) actual="detached@$(git -C "$dir" rev-parse --short HEAD 2>/dev/null)" ;; esac
  local giterr
  giterr="$(git -C "$dir" checkout "$expected" 2>&1 >/dev/null)" \
    || die "assert-run-head: HEAD is on '$actual', not '$expected', and could not be restored (fail-closed): ${giterr:-git checkout failed}"
  if [ -n "$questions" ]; then
    # Resolve a relative --questions against the run worktree (--dir), not the
    # caller's cwd — the documented invocation passes `.auto-pilot/QUESTIONS.md`.
    case "$questions" in /*) ;; *) questions="$dir/$questions" ;; esac
    # Number from the MAX existing index, not the count — QUESTIONS.md is not
    # guaranteed contiguously numbered from Q1 (other writers append too).
    local n; n="$(grep -oE '^## Q[0-9]+' "$questions" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)"
    [ -n "$n" ] || n=0
    local qn=$((n + 1))
    {
      [ -s "$questions" ] && printf '\n'
      printf '## Q%s — RUN — run worktree HEAD was parked on `%s`\n\n' "$qn" "$actual"
      printf -- '- **Options:** restore HEAD to `%s` and continue | halt the run for a human\n' "$expected"
      printf -- '- **Call:** restored HEAD to `%s` and continued\n' "$expected"
      printf -- '- **Why:** the run worktree'\''s HEAD staying on the run-state branch is load-bearing for --resume (run-state.md "Run worktree HEAD invariant"); the run worktree was clean, so the deviation is fully repairable and halting an otherwise-recoverable run would be worse\n'
      printf -- '- **Reversible:** yes — the run worktree was clean, so HEAD is only restored and nothing is lost\n'
    } >>"$questions" \
      || die "assert-run-head: restored HEAD to '$expected' but could not write the QUESTIONS.md entry (fail-closed): $questions"
  fi
  echo "spawn-orchestrator: HEAD DEVIATION restored (was $actual, now $expected)"
}

# ---------------------------------------------------------------------------
# Task 14 — run doctor (generalizes findings #22/#23): a cheap, deterministic,
# no-model-call invariant audit run at the top of every run-loop iteration and
# at the top of `--resume`. Both #22 (the 401 loop) and #23 (the vanished
# run-state) shipped as a clean `exit 0` — nothing ever asked "am I still a
# valid run?" `doctor` is that question, asked seven ways, each with a stated
# repair or a halt; none may be silently ignored.
#
# Exit codes (a caller GATES the run loop on these):
#   0   every invariant holds, or was repaired/parked — the loop may proceed
#   30  HALT — an invariant demanded `status: systemic`; the loop must NOT
#       dispatch (a caller checking `$?` before its next /deliver-task call
#       therefore cannot reach it)
#   2   bad usage / an unrepairable fail-closed condition (`die`)
#
# Composition, not reimplementation (per the task's ALIGNMENT note):
#   I1  calls the existing assert_run_head (task 13) — it already asserts,
#       restores, and records the deviation.
#   I2  reads RUN.md/QUESTIONS.md/REPORT.md via `git show <branch>:<path>`,
#       the same belt --resume already uses (resume.md).
#   I3/I4 read/write RUN.md's table via _restack_read_run_md (extended above
#       with _RS_PHASE) and _set_task_phase (below).
#   I7  reuses _write_supervisor_state/_supervisor_state_field's file shape
#       (own state file, doctor-state — task 10's supervisor-state is a
#       DIFFERENT file with a DIFFERENT scope; see the I7 block).
#   Halt reuses _supervisor_halt (now --label-optional, above).
#   A halt NOTIFIES a human through task 16's JAILED seam (alarm-request), never
#       `alarm` directly — see _doctor_halt below, where the reason why is the
#       whole point.
# ---------------------------------------------------------------------------

# Halt the run, and make sure a HUMAN is actually told WHICH invariant failed.
#
# Doctor runs INSIDE the jail. `alarm` (task 16) cannot work from in here — the
# sandbox profile DENIES exec of /usr/bin/osascript, so the notification is
# silently denied — and calling it anyway is WORSE than not calling it: `alarm`
# writes the ALARM SENTINEL, which is the per-condition idempotency key, and
# `_supervisor_alarm_scan`'s `status: systemic` branch skips its own notification
# whenever `_alarm_raised` finds that sentinel ("we already screamed about this
# run"). So an in-jail `alarm` would leave the sentinel as a gag: the doctor's
# notification is denied by the jail, and the supervisor's — the one that CAN
# reach a human — is then suppressed by the very sentinel the denied attempt
# wrote. A halt that tells nobody is finding #22's silence, restored.
#
# `alarm-request` is the seam task 16 built for exactly this ("(JAILED side) Drop
# an alarm the agent cannot deliver itself"): doctor writes a request FILE, and
# the UN-JAILED supervisor delivers it — `_supervisor_alarm_scan` drains requests
# FIRST, before its own scan, and it runs both above the gate (every wake) and
# from `supervisor-check` (right after the agent exits, i.e. the SAME wake this
# halt happens in). So the request is delivered promptly, under a condition id
# whose action text task 16 already wrote (`invariant`), carrying doctor's own
# diagnosis — WHICH invariant failed — which the generic systemic scan could
# never state. Once delivered, the sentinel it writes is what makes the
# supervisor's follow-on systemic halt correctly SILENT: one alarm, named right.
#
# The halt itself therefore passes an EMPTY --condition (suppressing the in-jail
# `alarm`), and the request goes first — the notification is the point of the
# halt, and neither a broken git checkout nor a wedged teardown may cost us it.
# A subshell around alarm-request, `_alarm_safe`-style: its argument validation
# is `die` (an `exit`), which would take the halt down before RUN.md, REPORT.md
# and the teardown.
_doctor_halt() {
  local dir="$1" label="$2" reason="$3"
  ( alarm_request --dir "$dir" --condition invariant --reason "doctor: $reason" ) \
    || echo "spawn-orchestrator: doctor: could not file the alarm-request (the halt continues; the supervisor's systemic scan is the remaining channel)" >&2
  _supervisor_halt --dir "$dir" ${label:+--label "$label"} --condition "" --reason "$reason"
}

# Set a single task row's `phase` cell (RUN.md table column 2) in place,
# atomic tmp+mv. Matches the row by its exact `task` column text. Fail-closed
# if the task isn't found — a doctor repair silently rewriting the wrong row
# (or no row at all) must never happen.
_set_task_phase() {
  local f="$1" task="$2" new_phase="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.taskphase.XXXXXX")" || die "mktemp failed"
  awk -v task="$task" -v phase="$new_phase" 'BEGIN { FS = OFS = "|" }
    /^\|/ {
      t = $2; gsub(/^[ \t]+|[ \t]+$/, "", t)
      if (t == task) { $3 = " " phase " "; done = 1 }
    }
    { print }
    END { if (!done) exit 1 }
  ' "$f" >"$tmp"
  if [ $? -ne 0 ]; then
    rm -f "$tmp"
    die "doctor: task row not found (fail-closed): $task in $f"
  fi
  mv "$tmp" "$f" || { rm -f "$tmp"; die "failed to write $f"; }
}

# Append a QUESTIONS.md entry in run-state.md's format, numbering from the MAX
# existing index (matching assert_run_head's convention) so QUESTIONS.md stays
# one continuously-numbered log regardless of which guard wrote which entry.
# A no-op (not fail-closed) when --questions was not passed.
_doctor_questions_entry() {
  local qfile="$1" title="$2" options="$3" call="$4" why="$5" reversible="$6"
  [ -n "$qfile" ] || return 0
  local n; n="$(grep -oE '^## Q[0-9]+' "$qfile" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)"
  [ -n "$n" ] || n=0
  local qn=$((n + 1))
  {
    [ -s "$qfile" ] && printf '\n'
    printf '## Q%s — DOCTOR — %s\n\n' "$qn" "$title"
    printf -- '- **Options:** %s\n' "$options"
    printf -- '- **Call:** %s\n' "$call"
    printf -- '- **Why:** %s\n' "$why"
    printf -- '- **Reversible:** %s\n' "$reversible"
  } >>"$qfile" 2>/dev/null \
    || echo "spawn-orchestrator: doctor WARNING — could not append QUESTIONS.md entry: $qfile" >&2
}

# gh field readers (mirrors restack's `gh pr view <n> --json X --jq .X`
# pattern — one call per field, so gh does the JSON extraction and neither
# side depends on `jq` being installed).
_doctor_pr_state()  { "$1" pr view "$2" --json state   --jq .state   2>/dev/null; }
_doctor_pr_draft()  { "$1" pr view "$2" --json isDraft --jq .isDraft 2>/dev/null; }
_doctor_pr_labels() { "$1" pr view "$2" --json labels  --jq '[.labels[].name] | join(",")' 2>/dev/null; }

doctor() {
  local dir="" run_id="" label="" questions="" handler="repo-pr" gh_bin="" limit="3" context="loop"
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --run-id) [ $# -ge 2 ] || die "missing value for --run-id"; run_id="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --questions) [ $# -ge 2 ] || die "missing value for --questions"; questions="$2"; shift 2 ;;
      --handler) [ $# -ge 2 ] || die "missing value for --handler"; handler="$2"; shift 2 ;;
      --gh) [ $# -ge 2 ] || die "missing value for --gh"; gh_bin="$2"; shift 2 ;;
      --no-progress-limit) [ $# -ge 2 ] || die "missing value for --no-progress-limit"; limit="$2"; shift 2 ;;
      --context) [ $# -ge 2 ] || die "missing value for --context"; context="$2"; shift 2 ;;
      *) die "unknown doctor argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$run_id" ] || die "doctor requires --dir and --run-id"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac
  case "$handler" in repo-pr|linear) ;; *) die "unknown --handler (fail-closed): $handler" ;; esac
  case "$context" in loop|resume) ;; *) die "unknown --context (fail-closed): $context" ;; esac
  case "$limit" in *[!0-9]*|"") die "--no-progress-limit must be a positive integer: $limit" ;; esac
  [ "$limit" -ge 1 ] || die "--no-progress-limit must be a positive integer"
  if [ -n "$questions" ]; then
    case "$questions" in /*) ;; *) questions="$dir/$questions" ;; esac
  fi

  local branch="auto-pilot/$run_id"
  local run_md="$dir/.auto-pilot/RUN.md" report_md="$dir/.auto-pilot/REPORT.md"
  # These five counters are PER-INVARIANT, never per task row / per worktree
  # (D8): each of the seven invariants below contributes AT MOST one bucket
  # increment, so ok+repaired+parked+halt+skipped never exceeds 7 — a summary
  # reading "7 invariants — ok=12" (D8's exact bug, from incrementing once per
  # TASK ROW inside an invariant that scans the whole table) can't happen.
  # Per-row/per-worktree DETAIL still lives in the *_notes arrays below and is
  # shown parenthetically; it just never feeds these counters directly.
  local n_ok=0 n_repaired=0 n_parked=0 n_halt=0 n_skipped=0
  local -a repaired_notes=() parked_notes=() skipped_notes=() report_bullets=()

  # Append the accumulated report_bullets (if any) as ONE dated "## Doctor"
  # section, then print the one-line summary. Called on EVERY exit path (ok,
  # parked-but-continuing, and halt) so a halt that happened after some earlier
  # invariant already repaired something never loses that record.
  _doctor_finish() {
    if [ "${#report_bullets[@]}" -gt 0 ]; then
      {
        printf '\n## Doctor — %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        printf '%s\n' "${report_bullets[@]}"
      } >>"$report_md" 2>/dev/null \
        || echo "spawn-orchestrator: doctor WARNING — could not append to $report_md (findings are on stdout only)" >&2
    fi
    local total=7 summary
    summary="spawn-orchestrator: doctor: $total invariants — ok=$n_ok repaired=$n_repaired"
    [ "${#repaired_notes[@]}" -gt 0 ] && summary+=" ($(IFS=', '; printf '%s' "${repaired_notes[*]}"))"
    summary+=" parked=$n_parked"
    [ "${#parked_notes[@]}" -gt 0 ] && summary+=" ($(IFS=', '; printf '%s' "${parked_notes[*]}"))"
    summary+=" halt=$n_halt"
    summary+=" skipped=$n_skipped"
    [ "${#skipped_notes[@]}" -gt 0 ] && summary+=" ($(IFS=', '; printf '%s' "${skipped_notes[*]}"))"
    echo "$summary"
  }

  # --- Invariant 1: run worktree HEAD is on the run-state branch -----------
  # Delegate entirely to assert_run_head (task 13): it asserts, restores, and
  # records the deviation itself. Run in a $(...) so a `die` inside only kills
  # the subshell (canonicalize()'s pattern above) — an unrepairable deviation
  # (a dirty run worktree) is doctor's own fail-closed exit, not a systemic
  # halt (there is nothing wrong with the RUN STATE itself in that case).
  #
  # Before delegating: if HEAD is parked off-branch AND the worktree is dirty
  # ONLY inside .auto-pilot/, that dirt is never authoritative — the run-state
  # branch, not the task branch's working tree, is the run's single memory.
  # A stale/half-written/deleted .auto-pilot/ copy sitting on a task branch is
  # exactly the shape of findings #22/#23 (this is invariant 1 and 2's own
  # deadlock scenario: RUN.md deleted + HEAD parked). assert_run_head fail-
  # closes on ANY dirt, so without handling this, I1 can never repair the
  # state I2 exists to restore.
  #
  # D1: a real run worktree ALWAYS carries UNTRACKED .auto-pilot/ content —
  # orchestrator.log, verify-broker.log, this doctor's own doctor-state — and
  # `git reset`/`git checkout` (below) cannot discard untracked files at all,
  # so on a real run this used to no-op silently and the repair could never
  # fire. We do NOT `git clean` those away: they are the run's only forensic
  # record, and destroying them to unblock a HEAD repair is exactly the
  # "destroys what a human would miss" failure doctor exists to prevent.
  # TRACKED .auto-pilot/ changes (a modified/deleted RUN.md, etc.) are still
  # discarded here, as before; assert_run_head is told (via
  # --ignore-untracked-run-state) to tolerate whatever untracked .auto-pilot/
  # content remains rather than treat it as blocking dirt. Dirt OUTSIDE
  # .auto-pilot/ still fails closed below, verbatim.
  local i1_discarded=0 i1_parked_on
  i1_parked_on="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  if [ "$i1_parked_on" != "$branch" ] \
     && [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ] \
     && [ -z "$(git -C "$dir" status --porcelain -- . ":(exclude).auto-pilot/" 2>/dev/null)" ]; then
    if [ -n "$(git -C "$dir" status --porcelain -- .auto-pilot/ 2>/dev/null | grep -v '^??')" ]; then
      git -C "$dir" reset -q -- .auto-pilot/ 2>/dev/null
      git -C "$dir" checkout -q -- .auto-pilot/ 2>/dev/null
    fi
    i1_discarded=1
  fi
  local i1_out i1_rc
  i1_out="$(assert_run_head --dir "$dir" --run-id "$run_id" --ignore-untracked-run-state ${questions:+--questions "$questions"} 2>&1)"; i1_rc=$?
  [ "$i1_rc" -eq 0 ] || die "doctor: invariant 1 (HEAD) could not be repaired: $i1_out"
  if [ "$i1_discarded" -eq 1 ]; then
    n_repaired=$((n_repaired + 1)); repaired_notes+=("I1: discarded stale .auto-pilot/ dirt on $i1_parked_on, HEAD restored")
    report_bullets+=("- **I1 repaired** — the run worktree's HEAD was parked off \`$branch\` with stale \`.auto-pilot/\` content; tracked changes there were discarded (untracked run logs were left in place — never \`git clean\`ed) and HEAD was restored (see QUESTIONS.md for the deviation record).")
  elif printf '%s' "$i1_out" | grep -q 'HEAD DEVIATION restored'; then
    n_repaired=$((n_repaired + 1)); repaired_notes+=("I1: HEAD restored")
    report_bullets+=("- **I1 repaired** — the run worktree's HEAD was parked off \`$branch\`; restored (see QUESTIONS.md for the deviation record).")
  else
    n_ok=$((n_ok + 1))
  fi

  # --- Invariant 2: RUN.md/QUESTIONS.md/REPORT.md readable FROM THE BRANCH -
  # Finding #23 exactly: reading the WORKING TREE instead of the branch is
  # what let a run continue into a stateless void. A branch-read failure, or a
  # RUN.md whose front matter doesn't parse (no run_id/status), means the run
  # has no memory — HALT, do not guess, and stop auditing further invariants
  # (there is nothing reliable left to check them against).
  local run_branch questions_branch report_branch
  run_branch="$(git -C "$dir" show "$branch:.auto-pilot/RUN.md" 2>/dev/null)"
  questions_branch="$(git -C "$dir" show "$branch:.auto-pilot/QUESTIONS.md" 2>/dev/null)"
  report_branch="$(git -C "$dir" show "$branch:.auto-pilot/REPORT.md" 2>/dev/null)"
  local branch_ok=1 fm_run_id="" fm_status=""
  if [ -z "$run_branch" ] || ! git -C "$dir" cat-file -e "$branch:.auto-pilot/QUESTIONS.md" 2>/dev/null \
     || ! git -C "$dir" cat-file -e "$branch:.auto-pilot/REPORT.md" 2>/dev/null; then
    branch_ok=0
  else
    local front2; front2="$(printf '%s\n' "$run_branch" | awk '/^---$/{c++; next} c==1{print}')"
    fm_run_id="$(printf '%s\n' "$front2" | grep -E '^run_id:' | head -1 | sed -e 's/^run_id: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
    fm_status="$(printf '%s\n' "$front2" | grep -E '^status:' | head -1 | sed -e 's/^status: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
    { [ -n "$fm_run_id" ] && [ -n "$fm_status" ]; } || branch_ok=0
  fi
  if [ "$branch_ok" -eq 0 ]; then
    n_halt=$((n_halt + 1))
    _doctor_halt "$dir" "$label" "invariant 2: RUN.md/QUESTIONS.md/REPORT.md unreadable from $branch, or RUN.md front matter doesn't parse — the run has no memory"
    _doctor_finish
    return 30
  fi
  # Branch reads are good — now check the WORKING TREE copy (the belt: a
  # working-tree RUN.md missing/unparseable while the BRANCH is fine is a
  # *repair*, not a halt — restore .auto-pilot/ from the branch.
  local wt_ok=1
  if [ ! -f "$run_md" ]; then
    wt_ok=0
  else
    local front_wt fm_run_id_wt fm_status_wt
    front_wt="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
    fm_run_id_wt="$(printf '%s\n' "$front_wt" | grep -E '^run_id:' | head -1 | sed -e 's/^run_id: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
    fm_status_wt="$(printf '%s\n' "$front_wt" | grep -E '^status:' | head -1 | sed -e 's/^status: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
    { [ -n "$fm_run_id_wt" ] && [ -n "$fm_status_wt" ]; } || wt_ok=0
  fi
  if [ "$wt_ok" -eq 0 ]; then
    git -C "$dir" checkout "$branch" -- .auto-pilot/ 2>/dev/null \
      || die "doctor: invariant 2 repair failed — could not restore .auto-pilot/ from $branch (fail-closed)"
    n_repaired=$((n_repaired + 1)); repaired_notes+=("I2: RUN.md restored from branch")
    report_bullets+=("- **I2 repaired** — RUN.md was missing/unparseable in the run worktree though it was readable on \`$branch\`; restored \`.auto-pilot/\` from the branch.")
  else
    n_ok=$((n_ok + 1))
  fi

  # From here on, RUN.md's table is the source of truth for I3/I4/I5/I6.
  _restack_read_run_md "$run_md"
  local base_branch="$_RS_BASE_BRANCH"
  local n_rows="${#_RS_TASK[@]}"

  # --- Invariants 3 & 4: every in-flight-with-a-PR task has a real PR; a ----
  # `handed-off` repo-pr task carries its review signal (one gh call covers
  # both — state, isDraft, labels).
  local need_gh=0 i
  for ((i = 0; i < n_rows; i++)); do
    case "${_RS_PHASE[$i]}" in pr-open|in-review|iterating|handed-off) need_gh=1 ;; esac
  done
  if [ "$need_gh" -eq 1 ]; then
    [ -n "$gh_bin" ] || gh_bin="$(command -v gh 2>/dev/null)" || true
    [ -n "$gh_bin" ] || die "doctor: gh not found (fail-closed): pass --gh <path> (mockable in tests) or put gh on PATH — needed for invariants 3/4"
  fi
  # i3_status/i4_status classify the WHOLE invariant, not per task row (D8):
  # "parked"/"repaired" wins over "ok" if ANY row needed it. A gh read that
  # could not be determined at all bumps i3_skipped instead — it is neither a
  # pass nor a violation, just unknown this pass (D2).
  local i3_status="ok" i4_status="ok" i3_skipped=0
  for ((i = 0; i < n_rows; i++)); do
    local task="${_RS_TASK[$i]}" phase="${_RS_PHASE[$i]}" pr="${_RS_PR[$i]}"
    case "$phase" in pr-open|in-review|iterating|handed-off) ;; *) continue ;; esac
    if _restack_empty "$pr"; then
      _set_task_phase "$run_md" "$task" "parked"
      i3_status="parked"; parked_notes+=("I3: $task")
      report_bullets+=("- **I3 parked — $task**: phase was \`$phase\` with no PR number recorded — a human must look.")
      _doctor_questions_entry "$questions" "$task — phase $phase with no recorded PR" \
        "park the task | leave it as-is" "parked" \
        "phase $phase claims a delivery in flight, but RUN.md has no PR number to verify against — never re-dispatch on a guess" \
        "yes — re-claim or re-link the PR by hand, then flip the phase back"
      continue
    fi
    # D3: the pr cell may be a bare number, a `#`-prefixed number, or RUN.md's
    # own markdown-link form (`[#188](https://…/pull/188)`) — `_pr_number` is
    # the one shared parser for all three; a cell that still doesn't parse is
    # treated the same as "no PR recorded" rather than handed to gh verbatim.
    local pr_num; pr_num="$(_pr_number "$pr")"
    if [ -z "$pr_num" ]; then
      _set_task_phase "$run_md" "$task" "parked"
      i3_status="parked"; parked_notes+=("I3: $task")
      report_bullets+=("- **I3 parked — $task**: phase was \`$phase\` with an unparseable PR cell (\`$pr\`) — a human must look.")
      _doctor_questions_entry "$questions" "$task — phase $phase with an unparseable PR cell" \
        "park the task | leave it as-is" "parked" \
        "RUN.md's pr cell (\`$pr\`) did not parse to a PR number — never guess which PR a phase claims" \
        "yes — fix the RUN.md cell by hand, then flip the phase back"
      continue
    fi
    local state state_rc
    state="$(_doctor_pr_state "$gh_bin" "$pr_num")"; state_rc=$?
    if [ "$state_rc" -ne 0 ]; then
      # D2: a non-zero gh rc (401, rate limit, network blip) is UNDETERMINED,
      # never a positive signal the PR is gone. Parking on it is the exact
      # bug that would park every in-flight task on one transient gh hiccup —
      # the same 401 finding #22 already burned us on once. Leave the phase
      # alone; a human, or the next doctor pass once gh recovers, gets a real
      # signal to act on instead of a guess.
      i3_skipped=1; skipped_notes+=("I3: $task (gh unreadable)")
      echo "spawn-orchestrator: doctor I3: $task — gh unreadable (exit $state_rc), skipping (undetermined; never park on a transient gh failure)"
      continue
    fi
    case "$state" in
      OPEN) ;;
      MERGED)
        # A human merged it post-hand-off — the expected, healthy end state,
        # NOT a violation. Doctor must not "repair" a merge.
        continue
        ;;
      CLOSED|"")
        _set_task_phase "$run_md" "$task" "parked"
        i3_status="parked"; parked_notes+=("I3: $task")
        local why="PR #$pr_num is CLOSED (unmerged)"
        [ -z "$state" ] && why="PR #$pr_num does not exist or is unreadable"
        report_bullets+=("- **I3 parked — $task**: $why — the delivery's PR is gone; a human must look.")
        _doctor_questions_entry "$questions" "$task — $why" \
          "park the task | leave it as-is" "parked" \
          "$why; never silently re-dispatch a task whose PR vanished" \
          "yes — recreate the PR or re-link a new one, then flip the phase back"
        continue
        ;;
      *)
        _set_task_phase "$run_md" "$task" "parked"
        i3_status="parked"; parked_notes+=("I3: $task")
        report_bullets+=("- **I3 parked — $task**: PR #$pr_num has unexpected state '$state' — a human must look.")
        continue
        ;;
    esac

    # Invariant 4: repo-pr's review signal is the PR itself — labeled
    # `task-loop` (not `task-claim`) and NOT a draft (deliver-task step 7: the
    # task file is deleted in the PR rather than flipped to needs_review, so
    # the ready task-loop PR IS the review signal). Only repo-pr is wired;
    # a future `linear` handler's own signal is kept behind --handler.
    if [ "$phase" = "handed-off" ] && [ "$handler" = "repo-pr" ]; then
      # Each gh WRITE's exit code is checked, and `fixes` only grows on a write
      # that actually succeeded (D5's rule, stated there for I5's failed
      # `worktree remove`, and just as binding here): a gh blip mid-repair that
      # still recorded "I4 repaired" in REPORT.md/QUESTIONS.md would be the exact
      # silent lie doctor exists to eliminate. A failed write is announced and
      # left for the next pass, which re-reads the still-stale label/draft state.
      local draft labels fixes=""
      draft="$(_doctor_pr_draft "$gh_bin" "$pr_num")"
      labels="$(_doctor_pr_labels "$gh_bin" "$pr_num")"
      case ",$labels," in *,task-claim,*)
        if "$gh_bin" pr edit "$pr_num" --remove-label task-claim --add-label task-loop >/dev/null 2>&1; then
          fixes="label task-claim->task-loop"
        else
          echo "spawn-orchestrator: doctor I4: $task — \`gh pr edit\` FAILED for PR #$pr_num (label left at task-claim, not reported as a repair)"
        fi ;;
      esac
      if [ "$draft" = "true" ]; then
        if "$gh_bin" pr ready "$pr_num" >/dev/null 2>&1; then
          fixes="${fixes:+$fixes, }marked ready (was draft)"
        else
          echo "spawn-orchestrator: doctor I4: $task — \`gh pr ready\` FAILED for PR #$pr_num (PR left as a draft, not reported as a repair)"
        fi
      fi
      if [ -n "$fixes" ]; then
        i4_status="repaired"; repaired_notes+=("I4: $task PR #$pr_num")
        report_bullets+=("- **I4 repaired — $task** (PR #$pr_num): $fixes — a G6/G7 crash gap left the repo-pr review signal stale.")
        _doctor_questions_entry "$questions" "$task — PR #$pr_num missing its repo-pr review signal" \
          "apply the missing label/ready swap | leave it as-is" "applied ($fixes)" \
          "deliver-task step 7: a ready, task-loop-labeled PR IS the review signal for repo-pr; a G6/G7 crash can leave it labeled task-claim or still draft" \
          "yes — re-label/re-draft by hand if this was wrong"
      fi
    fi
  done
  case "$i3_status" in
    ok) n_ok=$((n_ok + 1)) ;;
    parked) n_parked=$((n_parked + 1)) ;;
  esac
  [ "$i3_skipped" -eq 1 ] && n_skipped=$((n_skipped + 1))
  case "$i4_status" in
    ok) n_ok=$((n_ok + 1)) ;;
    repaired) n_repaired=$((n_repaired + 1)) ;;
  esac

  # --- Invariant 5: no orphan worker worktrees from a dead dispatch (G2) ----
  # Conservative: prune ONLY when EVERY condition holds (D4). `pending` is
  # deliberately NOT on the safe list. RUN.md's phase cell is written by a
  # commit the orchestrator makes AFTER it dispatches, so there is a window —
  # right after each RUN.md commit+push — where a worker worktree is LIVE (a
  # real dispatch, possibly with an open PR already) while its row still
  # reads `pending`. `pending` therefore cannot distinguish "never dispatched"
  # from "dispatched moments ago"; only a phase that is unambiguously
  # TERMINAL for this worktree (`parked`, `handed-off`) is safe on the phase
  # alone. Leaving an orphan worktree behind is harmless (the next pass, or a
  # human, can still clean it up); deleting a live one destroys work — the
  # asymmetry is why every condition below must hold, not most of them.
  #
  # An UNMATCHED worktree — no RUN.md row names its branch, including a worker
  # left on a DETACHED HEAD (`rev-parse --abbrev-ref` reads back the literal
  # "HEAD", which no row's `branch` cell can ever equal) — is NOT self-evidently
  # abandoned, and treating it as such was a data-loss bug. The orchestrator
  # writes a task's `branch`/`phase`/`pr` cells back only AFTER /deliver-task
  # returns (SKILL.md "State update after each task"), so for the whole of a
  # LIVE dispatch the row reads `| t | pending | - | …` and matches NOTHING —
  # while a freshly-created worker worktree is clean, carries no commits beyond
  # its base, and has no PR yet, i.e. passes every other condition below. The
  # `pending`-is-not-safe guard above never saw that case, because the match is
  # keyed on the branch cell the dispatch has not written yet.
  #
  # So an unmatched worktree is prunable ONLY when the run's orchestrator is
  # PROVABLY DEAD — nothing can be mid-dispatch if the process that dispatches
  # is gone. That is the same stale-orchestrator machinery --resume already
  # gates on (resume.md "Stale-orchestrator guard"; launch-runtime.md "Orphan /
  # stale detection"): RUN.md's `orchestrator_pid` + `orchestrator_started_at`
  # via _pid_state, not a second liveness check of doctor's own. `live` skips,
  # and so does an UNDETERMINED read (`none` — no pid recorded, or `ps`
  # unreadable): same D2 posture as I3/I6, an undetermined signal never
  # green-lights a destructive action. A recycled pid (`mismatch`) means the
  # recorded process is gone, which IS provably dead.
  local run_root="$(dirname "$dir")" workers_root
  workers_root="$run_root/workers"
  if [ -d "$workers_root" ]; then
    local front orch_state orch_dead=0
    front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
    orch_state="$(_pid_state "$(_front_field orchestrator_pid)" "$(_front_field orchestrator_started_at)")"
    case "$orch_state" in dead|mismatch) orch_dead=1 ;; esac
    local wt_line wt any_pruned=0
    while IFS= read -r wt_line; do
      case "$wt_line" in "worktree "*) wt="${wt_line#worktree }" ;; *) continue ;; esac
      case "$wt" in "$workers_root"/*) ;; *) continue ;; esac
      [ -d "$wt" ] || continue
      local wtbranch matched=0 matched_phase="" matched_pr="" matched_base="" j
      wtbranch="$(git -C "$wt" rev-parse --abbrev-ref HEAD 2>/dev/null)"
      for ((j = 0; j < n_rows; j++)); do
        if [ "${_RS_BRANCH[$j]}" = "$wtbranch" ]; then
          matched=1; matched_phase="${_RS_PHASE[$j]}"; matched_pr="${_RS_PR[$j]}"; matched_base="${_RS_BASE[$j]}"
          break
        fi
      done
      local safe_phase=0 unmatched_live=0
      if [ "$matched" -eq 0 ]; then
        # No RUN.md row names this branch — could be a dead dispatch's orphan,
        # or a LIVE dispatch whose row hasn't been written back yet. Only the
        # orchestrator being provably dead tells the two apart.
        if [ "$orch_dead" -eq 1 ]; then safe_phase=1; else unmatched_live=1; fi
      else
        case "$matched_phase" in parked|handed-off) safe_phase=1 ;; esac
      fi

      local dirty local_tip pushed=0
      dirty="$(git -C "$wt" status --porcelain 2>/dev/null)"
      local_tip="$(git -C "$wt" rev-parse HEAD 2>/dev/null)"
      if [ -z "$local_tip" ]; then
        pushed=1   # no commits at all — a truly dead dispatch
      else
        local remote_tip; remote_tip="$(git -C "$wt" rev-parse "origin/$wtbranch" 2>/dev/null)"
        [ -n "$remote_tip" ] && [ "$local_tip" = "$remote_tip" ] && pushed=1
        if [ "$pushed" -ne 1 ]; then
          # OR: no commits beyond its base — nothing would be lost by removal.
          local base_ref="$matched_base"; [ -n "$base_ref" ] || base_ref="$base_branch"
          local ahead; ahead="$(git -C "$wt" rev-list --count "origin/$base_ref..$wtbranch" 2>/dev/null)"
          [ "$ahead" = "0" ] && pushed=1
        fi
      fi

      # No OPEN PR for this branch. Only checkable when RUN.md recorded a PR
      # number for the matched row — an unmatched/no-PR row has nothing to
      # check, so this holds trivially. A PR read that can't be resolved
      # (gh missing, an unparseable cell, or a failing gh call) is
      # UNDETERMINED — same D2 posture as I3/I6: undetermined never
      # green-lights a destructive action, so it fails this condition shut.
      local no_open_pr=1
      if [ "$matched" -eq 1 ] && ! _restack_empty "$matched_pr"; then
        [ -n "$gh_bin" ] || gh_bin="$(command -v gh 2>/dev/null)" || true
        local wpr_num=""
        [ -n "$gh_bin" ] && wpr_num="$(_pr_number "$matched_pr")"
        if [ -n "$gh_bin" ] && [ -n "$wpr_num" ]; then
          local wpr_state wpr_rc
          wpr_state="$(_doctor_pr_state "$gh_bin" "$wpr_num")"; wpr_rc=$?
          if [ "$wpr_rc" -eq 0 ]; then
            [ "$wpr_state" = "OPEN" ] && no_open_pr=0
          else
            no_open_pr=0   # unreadable — fail closed (do not prune)
          fi
        else
          no_open_pr=0   # gh unavailable/unparseable PR cell — fail closed
        fi
      fi

      if [ "$safe_phase" -eq 1 ] && [ -z "$dirty" ] && [ "$pushed" -eq 1 ] && [ "$no_open_pr" -eq 1 ]; then
        if git -C "$dir" worktree remove --force "$wt" 2>/dev/null; then
          any_pruned=1
          repaired_notes+=("I5: removed $(basename "$wt")")
          report_bullets+=("- **I5 repaired** — removed orphan worker worktree \`$wt\` (G2): terminal/no RUN.md row, clean, already pushed (or no commits beyond base), no open PR.")
        else
          # D5: a FAILED `git worktree remove` must never be reported as a
          # completed repair — that is exactly the silent-lie class doctor
          # exists to eliminate. Report it as a skipped/failed prune instead.
          echo "spawn-orchestrator: doctor I5: FAILED to remove (left in place, not reported as a repair): $wt"
          report_bullets+=("- **I5 FAILED** — \`git worktree remove\` failed for \`$wt\`; left in place. Not a completed repair.")
        fi
      elif [ "$unmatched_live" -eq 1 ]; then
        echo "spawn-orchestrator: doctor I5: skipped (unsafe to prune) — no RUN.md row names branch '${wtbranch:-?}' and the run's orchestrator is $orch_state, not provably dead (this is what a LIVE dispatch looks like before its row is written back): $wt"
      else
        echo "spawn-orchestrator: doctor I5: skipped (unsafe to prune): $wt"
      fi
    done < <(git -C "$dir" worktree list --porcelain 2>/dev/null)
    git -C "$dir" worktree prune >/dev/null 2>&1
    if [ "$any_pruned" -eq 1 ]; then
      n_repaired=$((n_repaired + 1))
    else
      n_ok=$((n_ok + 1))
    fi
  else
    n_ok=$((n_ok + 1))
  fi

  # --- Invariant 6: a chained task's parent tip still equals its frozen -----
  # base_sha — this guard models the ORCHESTRATOR moving a base mid-run, never
  # a human merging/reviewing the parent (run-state.md's `base_sha` note): only
  # park when the parent's own PR is POSITIVELY read as NOT merged; a merged
  # parent's remedy is `restack`, not a park, and doctor says so rather than
  # parking the child. An UNREADABLE parent state — gh not resolvable at all
  # (D7), or a gh call that fails (D2) — must fail closed toward NOT parking:
  # parking a child whose parent actually merged is precisely the violation
  # the comment above warns against, and a guess is no better than a stale
  # read.
  local i6_status="ok" i6_skipped=0
  for ((i = 0; i < n_rows; i++)); do
    local task="${_RS_TASK[$i]}" phase="${_RS_PHASE[$i]}" base="${_RS_BASE[$i]}" bsha="${_RS_BASE_SHA[$i]}"
    [ "$base" != "$base_branch" ] || continue     # independent task — nothing frozen
    _restack_empty "$bsha" && continue            # no frozen base yet — nothing to compare
    [ "$phase" != "handed-off" ] || continue       # terminal success — already delivered

    local pidx=-1 j
    for ((j = 0; j < n_rows; j++)); do
      if [ "${_RS_BRANCH[$j]}" = "$base" ]; then pidx=$j; break; fi
    done
    [ "$pidx" -ge 0 ] || continue   # parent not tracked in this run

    local parent_pr="${_RS_PR[$pidx]}" parent_merged=0 parent_unreadable=0
    if ! _restack_empty "$parent_pr"; then
      [ -n "$gh_bin" ] || gh_bin="$(command -v gh 2>/dev/null)" || true
      local ppr_num=""
      [ -n "$gh_bin" ] && ppr_num="$(_pr_number "$parent_pr")"
      if [ -n "$gh_bin" ] && [ -n "$ppr_num" ]; then
        local pstate pstate_rc
        pstate="$(_doctor_pr_state "$gh_bin" "$ppr_num")"; pstate_rc=$?
        if [ "$pstate_rc" -eq 0 ]; then
          [ "$pstate" = "MERGED" ] && parent_merged=1
        else
          parent_unreadable=1
        fi
      else
        # gh not resolvable at all, or the recorded PR cell didn't parse
        # (D7): the parent's merge state can't be determined from here.
        parent_unreadable=1
      fi
    fi
    if [ "$parent_merged" -eq 1 ]; then
      echo "spawn-orchestrator: doctor I6: $task — parent's PR merged; remedy is restack, not park (skipping)"
      continue
    fi
    if [ "$parent_unreadable" -eq 1 ]; then
      i6_skipped=1
      echo "spawn-orchestrator: doctor I6: $task — parent PR state unreadable; skipping (undetermined; never park on a guess)"
      continue
    fi

    local current_tip
    current_tip="$(git -C "$dir" rev-parse "$base" 2>/dev/null)"
    [ -n "$current_tip" ] || current_tip="$(git -C "$dir" rev-parse "origin/$base" 2>/dev/null)"
    if [ -n "$current_tip" ] && [ "$current_tip" != "$bsha" ] && [ "$phase" != "parked" ]; then
      _set_task_phase "$run_md" "$task" "parked"
      i6_status="parked"; parked_notes+=("I6: $task")
      report_bullets+=("- **I6 parked — $task**: parent \`$base\`'s tip moved off the frozen base_sha (\`$bsha\` -> \`$current_tip\`) without the parent's PR merging.")
      _doctor_questions_entry "$questions" "$task — parent $base's tip moved off its frozen base_sha" \
        "park the child | ignore the divergence" "parked" \
        "the base_sha freeze/park guard models the ORCHESTRATOR moving a base mid-run (never a human merge, which is restack's job)" \
        "yes — re-run doctor once the base is reconciled, or restack if the parent later merges"
    fi
  done
  case "$i6_status" in
    ok) n_ok=$((n_ok + 1)) ;;
    parked) n_parked=$((n_parked + 1)) ;;
  esac
  [ "$i6_skipped" -eq 1 ] && n_skipped=$((n_skipped + 1))

  # --- Invariant 7: forward progress since the last DOCTOR iteration --------
  # Ownership split (say it here AND in run-state.md): task 10's
  # supervisor-check guard owns no-progress ACROSS WAKES (process-level — the
  # agent died, the branch didn't move between launchd relaunches). THIS guard
  # owns no-progress ACROSS ITERATIONS within one LIVE agent process (the
  # agent is alive and looping but nothing advances). Different scopes,
  # different state files (doctor-state, never supervisor-state) — the two
  # can never both halt the same run for the same reason.
  local dstate="$dir/.auto-pilot/doctor-state"
  if [ "$context" = "resume" ]; then
    # D6: doctor runs once at the top of --resume and again at the top of the
    # first loop iteration, with the run HEAD necessarily unchanged between
    # the two — incrementing here would put the counter at 2 before any work
    # is even attempted, one strike from a spurious halt. A resume is BY
    # DEFINITION a fresh start, not a stalled iteration: reset, never
    # increment, in this context.
    _write_supervisor_state "$dstate" 0 "$(_run_head "$dir")"
    n_ok=$((n_ok + 1))
  elif _run_is_paused "$dir"; then
    _write_supervisor_state "$dstate" 0 "$(_run_head "$dir")"
    n_ok=$((n_ok + 1))
  else
    local prev_head prev_count head count
    prev_head="$(_supervisor_state_field "$dstate" head)"
    prev_count="$(_supervisor_state_field "$dstate" count)"
    case "$prev_count" in ''|*[!0-9]*) prev_count=0 ;; esac
    head="$(_run_head "$dir")"; head="${head:-unknown}"
    if [ -n "$prev_head" ] && [ "$prev_head" = "$head" ]; then
      count=$((prev_count + 1))
    else
      count=1
    fi
    # Subshelled — same shape, same reason as supervisor-check's no-progress guard:
    # a `die` from this bookkeeping write would `exit` before invariant 7's halt and
    # before doctor's own exit-30 (HALT) contract, turning a wedged run dir into a
    # silent relaunch loop instead of the alarm it is supposed to raise.
    ( _write_supervisor_state "$dstate" "$count" "$head" ) \
      || echo "spawn-orchestrator: doctor WARNING — could not persist doctor no-progress state (the counter cannot advance across iterations while this is broken — the halt below still fires if THIS iteration already reached the limit)" >&2
    if [ "$count" -ge "$limit" ]; then
      n_halt=$((n_halt + 1))
      _doctor_halt "$dir" "$label" "invariant 7: no forward progress after $count consecutive doctor iterations"
      _doctor_finish
      return 30
    else
      n_ok=$((n_ok + 1))
    fi
  fi

  _doctor_finish
  return 0
}

check_profile() {
  [ $# -eq 1 ] || die "check-profile takes exactly one <file>"
  local f="$1"
  [ -f "$f" ] || die "profile not found: $f"
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available (macOS only)"
  # Compile the profile by loading it. We test COMPILATION, not permission: a
  # locked-down profile legitimately denies exec of an unlisted probe binary, so
  # reaching execvp() means the profile parsed and loaded fine. Only a genuine
  # parse/compile error (which never reaches execvp) is a failure.
  local errout status
  errout="$(sandbox-exec -f "$f" /usr/bin/true 2>&1)"; status=$?
  if [ "$status" -eq 0 ] || printf '%s' "$errout" | grep -q 'execvp('; then
    echo "spawn-orchestrator: profile OK $f"
    return 0
  fi
  [ -n "$errout" ] && echo "$errout" >&2
  die "profile failed to compile: $f"
}

# Guarded so the test suite can `source` this file to unit-test an internal
# helper directly (e.g. `_restack_diff_audit`) without also running the
# dispatcher below against whatever args the sourcing shell happens to have —
# every `die` in this file is a real `exit`, which would kill the sourcing
# shell too. Direct execution (the only real-world use) is unaffected.
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
[ $# -ge 1 ] || die "usage: spawn-orchestrator.sh <render-profile|check-profile> …"
sub="$1"; shift
case "$sub" in
  render-profile) render_profile "$@" ;;
  render-settings) render_settings "$@" ;;
  write-launch) write_launch "$@" ;;
  record-handle) record_handle "$@" ;;
  smoke-test) smoke_test "$@" ;;
  detach) detach "$@" ;;
  teardown) teardown "$@" ;;
  launch) launch "$@" ;;
  write-verify-broker) write_verify_broker "$@" ;;
  verify-request) verify_request "$@" ;;
  verify-broker) verify_broker "$@" ;;
  verify-await) verify_await "$@" ;;
  check-profile) check_profile "$@" ;;
  status) status "$@" ;;
  assert-run-head) assert_run_head "$@" ;;
  classify-exit) classify_exit "$@" ;;
  supervisor-check) supervisor_check "$@" ;;
  supervisor-gate) supervisor_gate "$@" ;;
  supervisor-scan) supervisor_scan "$@" ;;
  alarm) alarm "$@" ;;
  alarm-request) alarm_request "$@" ;;
  alarm-clear) alarm_clear "$@" ;;
  exit-reason) exit_reason "$@" ;;
  clear-exit-state) clear_exit_state "$@" ;;
  heartbeat) heartbeat "$@" ;;
  restack) restack "$@" ;;
  co-review-stale-check) co_review_stale_check "$@" ;;
  co-review-stale-clear) co_review_stale_clear "$@" ;;
  doctor) doctor "$@" ;;
  -h|--help) sed -n '2,/^[^#]/{/^#/p;}' "$0"; exit 0 ;;
  *) die "unknown subcommand: $sub" ;;
esac
fi
