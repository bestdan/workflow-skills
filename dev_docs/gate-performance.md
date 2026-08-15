# Gate performance

Where `just check`'s wall time actually goes, why `just check-fast` exists, and
what would have to change for the full gate to get faster.

Day-to-day contributing doesn't require any of this — see
[`CONTRIBUTING.md`](../CONTRIBUTING.md). Read this before trying to speed the
gate up, or before adding concurrency anywhere in it.

## Where the time goes

`scripts/check.sh` already runs its checks concurrently, so a run costs its
**slowest single member**, not their sum. Measured on a 4-core Linux box, warm
(each check alone; under a full run's contention everything stretches — the
gate itself measures ~47s wall):

| check                                | wall     | why                                                                       |
| ------------------------------------ | -------- | ------------------------------------------------------------------------- |
| `scripts/test-research-spike.sh`     | **~31s** | 305 Python invocations; most of each is interpreter startup               |
| `scripts/lint-shell.sh`              | ~27s     | ShellCheck over the whole tree, dominated by the largest files, see below |
| `scripts/test-spawn-orchestrator.sh` | ~23s     | seven concurrent suites; the slowest, `exit-contract.sh`, is ~19s         |
| `scripts/typecheck.sh`               | ~4s      | ~8s on the first run of a new mypy pin, while `uvx` fetches it            |
| everything else (10 checks)          | ≤ ~4s    |                                                                           |

ShellCheck's runtime is superlinear in file size, so a whole-tree pass is
dominated by the few largest files: `test-research-spike.sh` ~8s and
`spawn-orchestrator.sh` ~7s, every other file well under a second. That
superlinearity is why the orchestrator harness lives as several files — as one
5816-line file it cost ~31s of ShellCheck _and_ ~60s as a serial suite, the #1
and #2 costs of the whole gate at once.

## How the orchestrator harness is split

`scripts/test-spawn-orchestrator.sh` is a thin driver over seven per-area
suites in `scripts/test-spawn-orchestrator/`, each runnable on its own and all
run concurrently by the driver. What to know before touching them:

- **`_prelude.sh` is sourced by every suite.** It owns the fixture base, the
  git isolation, the assertion helpers, and — because each suite is now its own
  process — the opening half of the two safety brackets: the PRE-618
  caller-repo snapshot and the notifier guard. `finish()`, called as each
  suite's last statement, is the closing half: it asserts both brackets held
  and prints the summary line the driver parses. A new suite gets all of this
  by sourcing the prelude and ending with `finish`.
- **The incidental-alarm count is the driver's assertion, not a suite's.** The
  guard log is pooled across suites via `$NOTIFY_GUARD_LOG` (exported by the
  driver, appended to by every suite's stubs) because "the guard caught the
  suite's incidental alarms" is a whole-run property — several suites raise
  none, and a per-suite count would fail them.
- **`shared_launch_inputs` is the cross-suite fixture seam.** The render suite
  builds and asserts the renderer's outputs; four other suites consume the same
  files (`cf.sb`, `wl.json`, `prompt.txt`, the generated `launch.sh`) as
  known-good inputs, and rebuild them through this prelude function. Its flags
  must stay identical to the render suite's own calls — two suites grep the
  generated `launch.sh` for generator behavior.
- **macOS runs ~32 assertions Linux skips** (Seatbelt compiles, the Homebrew
  Cellar and CLT-shim grants, the exec-dir escape tests), so balance judged on
  Linux alone is not balance. `SO_TEST_REQUIRE_SEATBELT=1` still works per
  suite: `finish()` gates on the suite's own skip count.

## Concurrency: spend cores on the critical path, and only there

Fanning the ten `.bats` files into ten jobs takes them from ~17s (their sum) to
~4s (the slowest) — but in a full run they are hidden behind the orchestrator
harness either way, so the fan-out cannot move the total. When it was tried
against the old single-file suite, an interleaved A/B measured the full gate at
66.8s serial-bats vs 68.9s fanned: the extra jobs just stole cores from the
check that was the critical path. The orchestrator split is the same lesson
applied in the other direction — its seven-way fan-out pays _because_ that
suite was the critical path (~60s → ~23s, and the gate followed, ~67s → ~47s
by interleaved A/B).

So `scripts/test-shell.sh` fans the bats files out **only under `--fast`**,
where the orchestrator harness is skipped and they really are the critical
path. There it is the whole difference: `check.sh --fast` is ~7.2s fanned vs
~18.2s serial.

`--fast` keeps `scripts/typecheck.sh`. At ~4s warm it is in the same class as
every other retained check, and it is the kind of defect a fast loop most wants
caught early.

Widening concurrency also has a cost beyond wasted cores: it breaks tests that
measure themselves against the wall clock. `test/await-pr-review.bats` blew a 5s
budget under load and reported `after=9s` — descheduled, not slow. The repo's
convention is to make such a test time-independent rather than to cap
concurrency (`LAND_TIMEOUT` there; `test/claude-auto-resume.bats` virtualizes the
clock outright; a success-case `--timeout` is a hang bound to keep generous, see
1ddb13e). The orchestrator suites' own bounds were audited when their
seven-way fan-out landed: the two tight ones were widened
(`runtime.sh`'s `verify-await --timeout`, `doctor.sh`'s scan-elapsed bound),
and the rest already poll to a deadline or carry an order of magnitude of
headroom.

## What would make the gate faster from here

`test-research-spike.sh` is now the bound. Its 305 Python invocations cost
~70ms each in interpreter/import startup, so batching invocations (or driving
the module in-process) is the lever; nothing else moves the gate until it
drops below `lint-shell.sh`'s ~27s.

## Measuring a change

Never quote a before/after taken from two separate runs. That mistake shipped a
claimed "64s → 53s" speedup that a controlled comparison later showed to be
nothing but drifting background load. Use an interleaved A/B — a worktree at the
base commit and the branch tree, alternating, at least two passes each:

```sh
git worktree add /tmp/base <base-sha>
for i in 1 2; do
  for d in /tmp/base .; do
    s=$(date +%s); (cd "$d" && scripts/check.sh) >/dev/null 2>&1
    echo "$d $(($(date +%s) - s))s"
  done
done
```

Report wall **and** CPU (`user` + `sys`): a change that raises CPU while holding
wall flat is buying nothing. After any concurrency change, run the full gate at
least four times and confirm zero flakes before calling it green.
