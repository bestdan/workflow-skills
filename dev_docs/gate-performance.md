# Gate performance

Where `just check`'s wall time actually goes, why `just check-fast` exists, and
what would have to change for the full gate to get faster.

Day-to-day contributing doesn't require any of this — see
[`CONTRIBUTING.md`](../CONTRIBUTING.md). Read this before trying to speed the
gate up, or before adding concurrency anywhere in it.

Every number here was measured on a 4-core Intel Xeon @ 2.80GHz Linux box, warm
(the mypy pin already fetched, the dprint plugins already compiled). Times on
your machine will differ; the ratios are what travel.

## Where the time goes

`scripts/check.sh` runs its checks concurrently, so a run costs roughly its
slowest member rather than their sum:

| check                                       | wall     | why                                                                      |
| ------------------------------------------- | -------- | ------------------------------------------------------------------------ |
| `scripts/test-research-spike.sh`            | **~27s** | 305 Python invocations; most of each is interpreter startup              |
| `scripts/test-shell.sh`                     | **~24s** | 10 bats files (~18s serial) alongside the orchestrator suite             |
| ↳ `scripts/test-spawn-orchestrator.sh`      | ~21s     | 7 parts run concurrently; their serial sum is ~57s                       |
| `shellcheck` inside `scripts/lint-shell.sh` | **~22s** | see [ShellCheck's cost](#shellchecks-cost-is-superlinear-in-file-length) |
| `scripts/typecheck.sh`                      | ~3s      | ~8s on the first run of a new mypy pin, while `uvx` fetches it           |
| everything else (9 checks)                  | ≤ ~4s    |                                                                          |
| **full `just check`**                       | **~41s** | above the slowest member: three of them contend for 4 cores              |

The shape that matters: there is **no single dominant check any more**. Three
members sit within a few seconds of each other, so shaving one of them moves the
total by a fraction of what it costs. That is a change from the situation this
document used to describe, when one 5816-line file was both the slowest suite
and most of the lint bill; see [the split](#the-orchestrator-harness-is-several-files).

Interleaved A/B of the full gate across that change, three passes each:

```
base (monolith)   61.8s  62.0s  60.3s   (mean 61.4s)
split             41.0s  40.2s  40.8s   (mean 40.7s)
```

`just check-fast` is ~7.5s. It skips `test-research-spike.sh` and the
orchestrator suite outright and narrows the shell lint to the files your branch
has touched. That narrowing is per-file, so **a branch that adds several long
shell files pays their full lint cost on every `--fast` run** — the branch that
performed this split measured 13–15s while it was adding nine of them, against
~7.5s once they were in the base. That is the mechanism working, not a
regression.

## The orchestrator harness is several files

`scripts/test-spawn-orchestrator.sh` is a driver. The assertions live in
`scripts/test-spawn-orchestrator-<part>.sh`, one file per topic:

```sh
ls scripts/test-spawn-orchestrator-*.sh
```

Each part is independently runnable (`bash scripts/test-spawn-orchestrator-doctor.sh`)
and self-contained: `scripts/lib/spawn-orchestrator-test-prelude.sh` builds it a
private fixture tree and the isolation guards, and
`scripts/lib/spawn-orchestrator-test-epilogue.sh` asserts they held.

| part            | wall (alone) | lines |
| --------------- | ------------ | ----- |
| `exit-contract` | ~16.5s       | 1253  |
| `status-report` | ~11.8s       | 678   |
| `doctor`        | ~8.2s        | 1132  |
| `supervisor`    | ~5.6s        | 653   |
| `alarm`         | ~5.2s        | 475   |
| `profile`       | ~5.0s        | 882   |
| `restack`       | ~4.2s        | 538   |
| serial sum      | ~57s         |       |
| via the driver  | **~21s**     |       |

Splitting was worth doing because it cut two costs at once — the suite's wall
time (~58s → ~21s) and its ShellCheck bill (~33s → ~9s). Note the serial sum
(~57s) is _worse_ than the monolith's ~58s was by only a hair, because seven
processes each rebuild the prelude's fixture bundle. Concurrency is what pays
for that overhead and then some.

### What a split of this suite has to preserve

Re-derive this before relying on it — and note the scan below is lexical, so
`eval`, indirect expansion and dynamically-built names are invisible to it.

```sh
# names defined in one part and read in another
grep -n '^[A-Za-z_][A-Za-z0-9_]*=\|^[A-Za-z_][A-Za-z0-9_]*()' scripts/test-spawn-orchestrator-*.sh
```

Two classes of coupling bit during the split, and both are worth knowing about
because a lexical `$NAME` scan misses them:

- **Bare names inside arithmetic.** `NOW_EPOCH` was read only as
  `$((NOW_EPOCH + 3600))`, which carries no sigil, so a scan for `$NAME` reported
  it clean. It is now in the prelude. Grep for the bare word, not just `$`.
- **Fixture files, not variables.** The exit-contract part read `$CX/auth.log`
  and `$CX/weird.log`, built by the classify-exit tests ~1300 lines earlier. No
  variable crossed — a _file_ did. It is rebuilt locally now (see the comment
  above `CX=` in `scripts/test-spawn-orchestrator-exit-contract.sh`). The same
  class produced the prelude's shared fixture bundle: `cf.sb`, `wl.json`,
  `prompt.txt` and the generated `launch.sh`/`job.plist` were side effects of the
  render tests that three other parts consumed.

Four guarantees are structural rather than ordinary tests, and any further
resharding has to keep them:

- **The caller-repo snapshot/assert bracket (PRE-618).** The suite drives real
  git-mutating fixtures, some deliberately in a dir that is not its own repo, so
  git walks upward and can reach the caller's. Search for
  `# --- Caller-repo safety snapshot` in the prelude and
  `# --- PRE-618 caller-repo integrity assertion` in the epilogue. Per-part is
  _stronger_ than the whole-suite version it replaced: a failure now names which
  part escaped.
- **The notifier guard.** Installed in the prelude (`# --- The notifier guard`),
  asserted in the epilogue (`# --- the notifier guard held`). Its structural
  halves are per-part. Its **behavioral** half could not be — it asserts the
  guard log is non-empty, and most parts raise no alarm at all — so each part
  drops its count in `$SO_TEST_TALLY_DIR` and the driver asserts the sum. That is
  the one whole-suite claim in this harness; if you reshard again, it has to move
  with the driver, not be dropped.
- **Opt-in strict seatbelt mode.** `SO_TEST_REQUIRE_SEATBELT=1` demands the
  seatbelt-behavioral assertions actually ran. It gates on the skip count, and
  per-part gating is equivalent to the old whole-suite one: the run fails if any
  part skipped.
- **macOS runs ~14 assertions Linux skips** (every `sandbox-exec` Seatbelt
  compile, the Homebrew Cellar and CLT-shim grants, the exec-dir escape tests), so
  a split balanced only on Linux is not balanced. They are all in the `profile`
  part.

The suite reports **756 passed / 14 skipped** on Linux, against the monolith's
726/14. The extra 30 are the epilogue's per-part assertions (two notifier-guard
structural checks and three caller-repo checks, now ×7 instead of ×1) minus the
whole-suite notifier count, which moved to the driver.

## ShellCheck's cost is superlinear in file length

This is the durable fact, and it is why splitting a file cuts two costs rather
than one. Measured with the gate's own invocation
(`shellcheck -s bash --severity=warning -e SC1090,SC1091`) over syntactically
valid prefixes of the old monolith:

| lines | wall   |
| ----- | ------ |
| 1101  | 1.43s  |
| 1733  | 3.10s  |
| 2249  | 4.54s  |
| 3923  | 12.86s |
| 5033  | 26.23s |
| 5816  | 31.03s |

Roughly quadratic over this range. The payoff is direct: the same assertions,
linted as 10 files instead of 1, cost **9.3s instead of 32.7s — with 330 more
total lines**.

Two cautions if you reproduce this:

- **Truncated prefixes lie.** `head -n` usually cuts mid-construct; ShellCheck
  then reports a parse error and exits before doing the expensive analysis, which
  looks like a spectacular speedup. Check `bash -n` on the prefix first. Every row
  above is a valid prefix.
- **Length alone does not predict cost across files.** The curve above is steep
  _within_ one file, but its coefficient is not universal:
  `scripts/spawn-orchestrator.sh` is 6408 lines — longer than the monolith ever
  was — and lints in ~5.5s, while the monolith wanted ~31s at 5816. So splitting
  a long file is a good bet, not a guarantee. Measure the file in front of you
  rather than extrapolating from its line count.

The largest single-file lint costs now are `scripts/test-research-spike.sh`
(~7.7s, 3862 lines) and `scripts/spawn-orchestrator.sh` (~5.5s, 6408 lines) —
reproduce the ranking with:

```sh
# the per-file lint bill, worst first
for f in $(git ls-files '*.sh'); do
  printf '%s %s\n' "$( { TIMEFORMAT=%R; time shellcheck -s bash --severity=warning \
    -e SC1090,SC1091 "$f" >/dev/null 2>&1; } 2>&1 )" "$f"
done | sort -rn | head
```

## Does adding concurrency help?

It did not before the split, and the reason was that everything hid behind a
~58s suite. That reason is gone, so the question was re-derived on the new tree
rather than carried forward. The answer is still "no", but the margin is now
thin enough that it should be re-measured rather than assumed.

**The bats fan-out.** `scripts/test-shell.sh` fans its ten `.bats` files into ten
jobs **only under `--fast`**. Serially they cost ~18s; fanned, ~6s. In a full run
they sit alongside the orchestrator suite, so serial bats still finishes inside
it. Interleaved A/B of the full `check.sh`, three passes each:

```
bats serial   37.4s  38.5s  40.5s   (mean 38.8s)
bats fanned   39.0s  38.0s  44.6s   (mean 40.5s)
```

No better, and noisier — the extra jobs contend with the suite that is actually
the critical path, which now runs seven of its own parts concurrently. But note
what changed: the old margin was 17s of bats against a 60s suite, and it is now
18s against 21s. **This conclusion is close to inverting** — close enough that
bats serial no longer reliably finishes first. Anything that takes a few seconds
off the orchestrator parts flips it, and at that point the condition in
`scripts/test-shell.sh` (search for `if [ "$fast" -eq 1 ]`) is worth revisiting
— as a measurement, not a preference.

**The split's own concurrency.** The driver running its seven parts concurrently
versus serially, interleaved A/B of the full `check.sh`:

```
driver concurrent   42.7s  43.5s  43.6s
driver serial       65.2s  69.5s  68.2s
```

That one is not close. The concurrency inside the driver is load-bearing and
pays for the ~1s of extra fixture setup each part costs.

## The wall-clock hazard

Widening concurrency breaks tests that measure themselves against elapsed time —
not because they are slow, but because they get descheduled.
`test/await-pr-review.bats` blew a 5s budget under load and reported `after=9s`.
The repo's convention is to make such a test **time-independent** rather than to
cap concurrency: `LAND_TIMEOUT` there, and `test/claude-auto-resume.bats`
virtualizes the clock outright.

The split raised concurrency inside the orchestrator suite from 1 job to 7, so
this section matters more than it did. The bounds that are next in line, re-audited
onto the parts they now live in:

| bound                                    | where                                                                      |
| ---------------------------------------- | -------------------------------------------------------------------------- |
| `verify-await --timeout 4`               | `test-spawn-orchestrator-supervisor.sh`, in the verify-broker round trip   |
| a `$SECONDS` elapsed check against 15s   | `test-spawn-orchestrator-doctor.sh`, `grep -n 'scan_t0=\$SECONDS'`         |
| `SPAWN_REPORT_TIMEOUT=2` and a `sleep 1` | `test-spawn-orchestrator-status-report.sh`, the hung-`gh` reporter fixture |
| a `sleep 3` claude stub                  | `test-spawn-orchestrator-status-report.sh`, the in-wake reporter fixture   |

```sh
grep -n 'timeout [0-9]\|SECONDS\|sleep [0-9]' scripts/test-spawn-orchestrator-*.sh
```

The reap assertion at the end of `test-spawn-orchestrator-status-report.sh`
already does this the right way — it polls to a deadline (`srl_tries` against 50
tries of `sleep 0.2`) instead of sleeping a fixed interval and hoping. Copy that
shape.

## What the bottleneck is now

There isn't one. Three checks sit at ~21–27s and the gate is ~41s, so the next
real gain needs either two of them to move together or the concurrency to fit the
box better.

Ranked by what they would actually buy:

1. **`scripts/test-research-spike.sh` (~27s)** is the biggest single lever, and
   the same shape the orchestrator suite was: 305 Python invocations that are
   mostly interpreter startup. Batching the fixtures through one interpreter, or
   splitting the file so its cases run concurrently, is the obvious move — the
   orchestrator split is the worked precedent.
2. **ShellCheck (~22s)** is now spread across many files rather than concentrated
   in one, so there is no single split left to make. The two largest are named
   above.
3. **The orchestrator suite (~21s)** is bounded by its `exit-contract` part
   (~16.5s). Splitting _that_ part further is the only thing that moves the
   suite's floor, and it would buy at most ~5s before `status-report` (~11.8s)
   becomes the bound.

Getting the whole gate meaningfully under ~30s means doing (1) and (3), because
of the contention: on 4 cores, three ~21–27s members cannot all run flat out.

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

The A/B tables in [the concurrency section](#does-adding-concurrency-help) are
what that produces. Both were worth running: one confirmed a conclusion (bats),
one would have been easy to get backwards by reasoning alone (the driver).

Report wall **and** CPU (`user` + `sys`): a change that raises CPU while holding
wall flat is buying nothing. After any concurrency change, run the full gate at
least four times and confirm zero flakes before calling it green.

Three traps this document's own measurements fell into, all worth avoiding:

- **A worktree shares the repo's ref store.** Running
  `git update-ref refs/remotes/origin/main HEAD` inside a `git worktree` — a
  plausible way to simulate "the branch has landed", e.g. to measure `--fast`
  with an empty touched set — rewrites that ref for **every** worktree including
  the primary one. The base-vs-branch A/B then silently compares the branch
  against itself and reports a speedup of zero. Use a separate `git clone` for
  that trick, and if a base arm ever measures suspiciously like the branch,
  check `git log --oneline -1 refs/remotes/origin/main` before believing it.
- **Cold caches read as real cost.** The `doctor` part first measured 37.8s and
  settled at ~8s once warm. Discard the first run of anything.
- **Your own measuring load is background drift.** The concurrent-driver A/B
  above ran at 42.7–43.6s for a configuration that measures ~41s on an otherwise
  idle box, and this document's early isolated runs read ~10% faster than its
  final interleaved ones. That is exactly why the comparison is interleaved:
  both arms drift together, and the _difference_ survives. Absolute numbers in
  this file are worth about ±10%; the ratios are not.
