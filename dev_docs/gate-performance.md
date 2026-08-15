# Gate performance

Where `just check`'s wall time actually goes, why `just check-fast` exists, and
what would have to change for the full gate to get faster.

Day-to-day contributing doesn't require any of this — see
[`CONTRIBUTING.md`](../CONTRIBUTING.md). Read this before trying to speed the
gate up, or before adding concurrency anywhere in it.

## Where the time goes

`scripts/check.sh` already runs its checks concurrently, so a run costs its
**slowest single member**, not their sum. Measured on a 4-core Linux box, warm:

| check                                       | wall     | why                                                                                      |
| ------------------------------------------- | -------- | ---------------------------------------------------------------------------------------- |
| `scripts/test-spawn-orchestrator.sh`        | **~60s** | 347 sequential invocations of the script under test, 39 `bash` fixtures, 772 `git` calls |
| `shellcheck` inside `scripts/lint-shell.sh` | **~35s** | ~31s of it is one file — see below                                                       |
| `scripts/test-research-spike.sh`            | **~31s** | 305 Python invocations; most of each is interpreter startup                              |
| `scripts/typecheck.sh`                      | ~4s      | ~8s on the first run of a new mypy pin, while `uvx` fetches it                           |
| everything else (10 checks)                 | ≤ ~4s    |                                                                                          |

The gate uses only ~1.8 of 4 cores, so the idle capacity is real — but it cannot
be spent usefully, for the reason in the next section.

**The second row is mostly the first row's file.** ShellCheck's runtime is
superlinear in file size, and `test-spawn-orchestrator.sh` is 5816 lines.
Per-file: that file 31.2s, `test-research-spike.sh` 11.9s,
`spawn-orchestrator.sh` 5.9s, every other shell file ≤0.35s.

## Why adding concurrency does not help

It was tried. Fanning the ten `.bats` files into ten jobs takes them from ~17s
(their sum) to ~4s (the slowest) — but in a full run they are already hidden
behind the ~60s orchestrator suite, and 17s and 4s are both far under 60s.
Interleaved A/B, alternating base and branch:

```
full check.sh         base 66.8s    fanned 68.9s
test-shell.sh alone   base 61.0s    fanned 66.3s
```

Measurably worse: the extra jobs steal cores from the one suite that is the
critical path. This is structural rather than a property of that machine — the
fan-out could only help if the orchestrator suite dropped below ~17s.

So `scripts/test-shell.sh` fans out **only under `--fast`**, where the
orchestrator suite is skipped and the bats files really are the critical path.
There it is the whole difference: `check.sh --fast` is ~7.2s fanned vs ~18.2s
serial.

`--fast` keeps `scripts/typecheck.sh`. At ~4s warm it is in the same class as
every other retained check, and it is the kind of defect a fast loop most wants
caught early. Only the three rows above it are worth skipping.

Widening concurrency also has a cost beyond wasted cores: it breaks tests that
measure themselves against the wall clock. `test/await-pr-review.bats` blew a 5s
budget under load and reported `after=9s` — descheduled, not slow. The repo's
convention is to make such a test time-independent rather than to cap
concurrency (`LAND_TIMEOUT` there; `test/claude-auto-resume.bats` virtualizes the
clock outright). Bounds inside the orchestrator suite that are next in line if
concurrency ever widens again: L1122 `--timeout 4`, L4117–4119 a `SECONDS`-based
elapsed check against 30s, L5604 `SPAWN_REPORT_TIMEOUT=2`, L5616 `sleep 1`,
L5669 a `sleep 3` stub. L5722 already polls to a deadline — copy that.

## The only thing that would make the full gate faster

Split `scripts/test-spawn-orchestrator.sh`. It is simultaneously the ~60s suite
and ~31s of the ShellCheck cost, so breaking it into smaller **files** (not just
a `--shard k/n` flag, which would leave the lint cost untouched) is the one
change that cuts both.

Its 39 `# ---` sections are close to independent. A scan for names defined in one
section and read in a later one finds **nine** such reads across seven sections:

| dependency        | kind                                | note                                      |
| ----------------- | ----------------------------------- | ----------------------------------------- |
| §8, §14, §15 ← §6 | fn `fc` (L314)                      | hoist to the preamble — mechanical        |
| §31 ← §28         | fn `_gate_iso` (L1657)              | hoist to the preamble — mechanical        |
| §18 ← §16         | var `LAUNCH_PATH`                   | adjacent pair; keep together or rebuild   |
| §23 ← §22         | var `RUNDIR`                        | adjacent pair; same                       |
| §35 ← §30, §31    | vars `ALFAIL`, `STUBF`, `STUB_PATH` | the whole-suite notifier guard; see below |

Re-derive this before relying on it. The scan is lexical, so `eval`, indirect
expansion and dynamically-built names are invisible to it — an earlier pass that
checked only variables missed both function dependencies and reported "three".

Four sections are structural rather than ordinary tests, and a split has to keep
their guarantees:

- **§0** (L40) captures a caller-repo safety snapshot and **§37** (L5749) asserts
  it. The suite drives real git-mutating fixtures, some deliberately in a dir
  that is not its own repo — so git walks upward and can reach the caller's. This
  guard exists because that once committed fixture files onto a live branch
  (PRE-618).
- **§2** (L153) installs the notifier guard so the suite can never reach the real
  `/usr/bin/osascript`; **§35** (L5034) asserts it held over the whole suite, and
  counts the incidental alarms. Under sharding that count has to be reformulated,
  not dropped.
- **§38** (L5785) is an opt-in strict mode demanding full Seatbelt coverage.

Note also that macOS runs ~32 assertions Linux skips (every `sandbox-exec`
Seatbelt compile, the Homebrew Cellar and CLT-shim grants, the exec-dir escape
tests), so a split that only balances on Linux is not balanced.

Getting the gate under ~20s would additionally need `test-research-spike.sh`,
whose 305 Python invocations are mostly import overhead.

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
