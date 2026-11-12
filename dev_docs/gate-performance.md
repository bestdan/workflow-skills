# Gate performance

Where `just check`'s wall time actually goes, why `just check-fast` exists, and
what would have to change for the full gate to get faster.

Day-to-day contributing doesn't require any of this — see
[`CONTRIBUTING.md`](../CONTRIBUTING.md). Read this before trying to speed the
gate up, or before adding concurrency anywhere in it.

Each measurement here names its machine: an 18-core Mac (how most sessions run
the gate), a 4-core Intel Xeon @ 2.80GHz Linux box, which the sections on the
orchestrator split and ShellCheck were measured on, and CI's `macos-latest`
runners, which appear once, in
[the bats fan-out on CI](#the-bats-fan-out-on-the-macos-ci-runner).
All runs are warm (the mypy pin already fetched, the dprint plugins already
compiled). Times on your machine will differ; the ratios are what travel.

## Where the time goes

`scripts/check.sh` runs its checks concurrently, so a run costs roughly its
slowest member rather than their sum. Every run ends with its own answer to
this section: a `→ timing` block naming its five slowest checks (see
[Timing warnings](#timing-warnings)). Trust that over the table below, which
ages as checks are added.

Full gate on the 18-core Mac, unsandboxed, 2026-11-11:

| check                                | wall      | why                                                        |
| ------------------------------------ | --------- | ---------------------------------------------------------- |
| `scripts/test-shell.sh`              | **~216s** | 40 bats files, one job each, plus the orchestrator suite   |
| `scripts/test-research-spike.sh`     | ~155s     | hundreds of Python invocations, mostly interpreter startup |
| `scripts/test-pr-review-comments.sh` | ~72s      |                                                            |
| `scripts/test-preflight-consent.sh`  | ~68s      |                                                            |
| `scripts/test-verify-fix.sh`         | ~47s      |                                                            |
| **full `just check`**                | **~217s** | CPU: 245s user + 388s sys; 200–235s over four runs         |
| `just check-fast`                    | ~127s     | bounded by the bats files, which it still runs             |

The gate starts about 140 jobs at once: roughly 100 `scripts/test-*.sh`
harnesses, the 40 bats files, and the linters. So a single check's time says as
much about contention as about the check. `test-research-spike.sh` took ~81s in
a run where the bats files ran serially and ~155s in one where they ran in
parallel, while the gate as a whole fell from 479s to 217s. The same holds for
`test-shell.sh`: its bats files take 113s run alone in parallel, and ~216s in
the full gate. Sys time exceeding
user time says that much of the CPU goes to the kernel spawning processes, not
to test logic.

`just check-fast` skips `test-research-spike.sh` and the orchestrator suite
outright and narrows the shell lint to the files your branch has touched. That
narrowing is per-file, so **a branch that adds several long shell files pays
their full lint cost on every `--fast` run**. That is the mechanism working,
not a regression.

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
| `reserve`       | ~1.3s        | 280   |
| serial sum      | ~58s         |       |
| via the driver  | **~21s**     |       |

Splitting was worth doing because it cut two costs at once — the suite's wall
time (~58s → ~21s) and its ShellCheck bill (~33s → ~9s). Note the serial sum
(~58s) has caught back up with the monolith's ~58s, because every part rebuilds
the prelude's fixture bundle. Concurrency is what pays
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
  `# --- PRE-618 caller-repo integrity assertion` in the epilogue. Per-part
  detection is _at least_ as strong as the whole-suite version it replaced. Be
  careful about the attribution claim, though: the parts run concurrently, so a
  persistent escape fails every part whose epilogue runs after it. That narrows
  the offender to a set, not to one part. (A transient mutate-then-restore
  inside a single run is still invisible — it was in the monolith too.)
- **The notifier guard.** Installed in the prelude (`# --- The notifier guard`),
  asserted in the epilogue (`# --- the notifier guard held`). Its structural
  halves are per-part. Its **behavioral** half could not be — it asserts the
  guard log is non-empty, and most parts raise no alarm at all — so each part
  drops its count in `$SO_TEST_TALLY_DIR` and the driver asserts the sum. That is
  the one whole-suite claim in this harness; if you reshard again, it has to move
  with the driver, not be dropped.

  One trap worth knowing, because it survived the monolith and then survived a
  first pass at this split: the per-PATH half must assert against the composite
  `PATH` a fixture **actually runs under**, stored whole at its call site. An
  assertion that _reconstructs_ that PATH cannot do its job — re-inject `$GUARD`
  and it can never fail (the guard dir is inside `$BASE`, which is what the check
  looks for), omit it and it reports leaks at safe call sites. Either way it
  stops tracking the fixture while still printing `ok`. The epilogue's enumerated
  list carries this warning; keep it exhaustive with the grep it names.
- **Opt-in strict seatbelt mode.** `SO_TEST_REQUIRE_SEATBELT=1` demands the
  seatbelt-behavioral assertions actually ran. It gates on the skip count, and
  per-part gating is equivalent to the old whole-suite one: the run fails if any
  part skipped.
- **macOS runs ~14 assertions Linux skips** (every `sandbox-exec` Seatbelt
  compile, the Homebrew Cellar and CLT-shim grants, the exec-dir escape tests), so
  a split balanced only on Linux is not balanced. They are all in the `profile`
  part.

The suite reports **759 passed / 14 skipped** on Linux, against the monolith's
726/14. The extra 33 break down as: the epilogue's per-part assertions (two
notifier-guard structural checks and three caller-repo checks, now ×7 instead of
×1) give +30 net of the whole-suite notifier count that moved to the driver, and
the per-PATH guard loop covers 8 composite PATHs where the monolith covered 5,
giving +3. `PSSTUB_PATH`, `SRW_PATH` and `SRL_PATH` are defined LATER in the
monolith than the point where it asserted, so they were unreachable there; the
epilogue runs last in every part, so they are covered now.

On macOS the same suite reports **777 passed / 0 skipped** — the 14 Linux skips
run for real, plus three host-fixture assertions Linux reports as bare notes.
CI runs that as a second job (`Host-sensitive suites (macOS)`, added in #360)
with `SO_TEST_REQUIRE_SEATBELT=1`, so the zero-skip claim is enforced rather
than hoped for.

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
linted as 10 files instead of 1, cost **8.3s instead of 33.3s — with 343 more
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
(~10s, 3862 lines) and `scripts/spawn-orchestrator.sh` (~5.5s, 6408 lines) —
reproduce the ranking with:

```sh
# the per-file lint bill, worst first
for f in $(git ls-files '*.sh'); do
  printf '%s %s\n' "$( { TIMEFORMAT=%R; time shellcheck -s bash --severity=warning \
    -e SC1090,SC1091 "$f" >/dev/null 2>&1; } 2>&1 )" "$f"
done | sort -rn | head
```

## Does adding concurrency help?

For the bats files and for the orchestrator driver, yes; each case below was
settled by measurement.

**The bats fan-out.** `scripts/test-shell.sh` runs each `.bats` file as its own
job, in every mode. On the 18-core Mac, 2026-11-11, with 40 files and 580 tests:

```
bats serial, alone          455s wall   (CPU: 84s user + 128s sys)
bats one job per file       113s wall   (slowest: test/guard-pinned-branch.bats)
full gate, bats serial      479s
full gate, bats per file    217s
```

Serial bats spends more than half its wall time waiting rather than on CPU, and
running the files concurrently recovers that. Serial bats would be harmless only
if it finished inside the orchestrator suite, and at 40 files it does not; the
[timing warnings](#timing-warnings) are how a check that grows onto the critical
path gets noticed.

**The split's own concurrency.** The driver running its parts concurrently
versus serially, interleaved A/B of the full `check.sh`, measured on the 4-core
Linux box while bats still ran serially:

```
driver concurrent   42.7s  43.5s  43.6s
driver serial       65.2s  69.5s  68.2s
```

That one is not close. The concurrency inside the driver is load-bearing and
pays for the ~1s of extra fixture setup each part costs.

### The bats fan-out on the macOS CI runner

CI's `macos` job runs only `scripts/test-shell.sh`, on a runner far smaller
than the Mac above, so the fan-out was measured there separately, 2026-11-12.
A throwaway branch ran the job as a matrix of six CI jobs, each on its own
runner: three ran all 40 bats files in one serial `bats` process, and three
ran one background process per file, as `main` does. The whole matrix then ran
a second time. Run
[43203256575](https://github.com/bestdan/workflow-skills/actions/runs/43203256575),
attempts 1 and 2. Job duration, with the `test-shell.sh` step in brackets:

```
attempt 1   serial     384s (364)   481s (457)   239s (225)*
            per file   209s (187)   175s (157)   268s (249)
attempt 2   serial     445s (424)   397s (374)   332s (313)
            per file   229s (210)   191s (173)   104s  (87)*
```

`macos-latest` is not one machine. The two runs marked `*` landed on an
"Apple M2 Pro (Virtual)" runner with 5 cores and 14 GB; the other ten ran on
"Apple M1 (Virtual)" with 3 cores and 7 GB. Compare within a runner type:

| runner      | serial               | per file             |
| ----------- | -------------------- | -------------------- |
| M1, 3 cores | 332–481s, median 397 | 175–268s, median 209 |
| M2 Pro, 5   | 239s (one run)       | 104s (one run)       |

On the M1 runner the slowest per-file run (268s) beat the fastest serial run
(332s), so the gain is outside run-to-run noise, and all twelve CI jobs passed.
The spread within one arm on one runner type is still 90–150s, and the runner
type alone moves a run by about as much. A single macOS duration therefore
says little: one before/after pair can show either sign.

Per file costs no extra CPU there (attempt 1 on M1: serial 3m57s–4m33s user,
per file 3m31s–4m57s). What it costs is latency per test. `bats --timing`
showed no serial test over 5s; per file, 23 to 92 tests per M1 run took over
10s, and the slowest took 61s against 3.6s serial
(`test/guard-foreign-worktree.bats`). Summed over every test, attempt 1 on M1
came to 2215–3581s per file against 276–357s serial, 6 to 13 times as much. Nothing
failed. The 5s budget in `test/await-pr-review.bats` that concurrency broke on
a workstation (see [the wall-clock hazard](#the-wall-clock-hazard)) is now
`LAND_TIMEOUT=60`, and that file's slowest test took 9.1s. A new bats test
with an elapsed-time bound has to hold on a 3-core runner running 41
processes at once: the 40 bats files and the orchestrator suite.

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

It depends on the machine. On the 18-core work laptop it is process creation,
machine-wide, and the evidence points at CrowdStrike Falcon, which vets every
launch. There the full gate
is bounded by how many processes the whole run starts, not by its slowest file,
so splitting a file buys nothing. On machines without Falcon, process starts
scale with the cores, and the slowest file can still be the bound. The
laptop's measurements, all unsandboxed, 2026-11-12:

- **The gate leaves the cores idle.** A full run took 181s wall for 227s user
  and 340s sys: about 3 of 18 cores busy on average, with sys above user.
- **Process starts hit a ceiling.** N concurrent loops of `/usr/bin/true`
  level off at 580–720 starts/s in total from N=9 up to N=72;
  `python3 -c pass` levels off at about 375/s from the same N=9. One loop alone manages only
  272 starts/s.
- **Running the bats files in parallel barely beats running them in series.**
  The 40 files run one after another took 293s; `scripts/test-shell.sh` runs
  them in parallel in 180s.
- **A file is slow mainly because it shares the machine.**
  `guard-pinned-branch` took 30s alone, 46s beside five other slow files, and
  167s in the full gate. Inside each slow file the cost is spread evenly
  (median 0.5–1.4s a test), so no single test dominates.

The ceiling is most likely Falcon's, though no run has compared one machine
with and without it. On three machines without Falcon, starts climb to about
the core count, 5–21 times the laptop's ceiling. The benchmark, the four-machine
table, the per-unit CPU ranking and the concurrency-cap A/B are in
[the research record](research/2026-11-12-gate-process-start-ceiling.md).

What moves the gate on the laptop is starting fewer processes, wherever they
are, and that helps every other machine too. For
the bats files (#1496) that meant two changes in the heaviest suites:

- **Build fixtures once.** `setup_file` builds the fixture repos, and each test
  copies them with one `cp -R` instead of about fifteen to twenty `git` calls.
  `pin_git_env` in `test/test_helper.bash` gives `setup_file` the same git
  isolation `setup_test` has.
- **Build hook payloads in bash.** `json_string` in the helper replaces the
  `python3` that built each payload, and `guard-foreign-worktree` reads the
  verdict with a pattern match instead of a second `python3`.

Applied to `guard-pinned-branch`, `guard-foreign-worktree` and
`worktree-create-hook`, that cut the full gate from a median of 182s
(181–187s) to 173s on every one of three interleaved passes each.
`guard-pinned-branch` alone went from 30.5s to 21.7s.

Ranked by what they would buy next:

1. **Fewer processes in the orchestrator suite.** `test-spawn-orchestrator.sh`
   is the largest single unit, about 128 of the gate's roughly 575 CPU-seconds.
   Its cost is `spawn-orchestrator.sh` doing real work rather than test
   overhead, so a gain there means fewer `doctor` calls or a cheaper `doctor`;
   the record has the trace.
2. **Fewer processes in the heaviest bats files.** The 40 files together take
   about 164 CPU-seconds. Solo CPU (user plus sys) is the proxy for a unit's
   share of the process budget; `guard-foreign-worktree` (17.1s),
   `co-review-ledger-sync` (16.5s) and `guard-pinned-branch` (14.9s) lead.
3. **Splitting the slowest files**, on machines without Falcon only. There the
   slowest file may still bound the gate; measure the gate there first.

Two things that look promising do not pay on the laptop:

- **Capping concurrency (#1498).** Nine jobs at a time made the gate slower
  (median 185s against 179s uncapped) and cut its CPU by only about 4%; 18 made
  no measurable difference in two valid passes. No cap ships.
- **Batching `research-spike.py` calls (#1497).** That harness is about 3% of
  the gate's CPU, most of it interpreter startup that batching would only
  partly remove.

## Skipping an unchanged tree

A repeat run on a tree that already passed the full gate does not run again.
After a green full run, `scripts/check.sh` stores a fingerprint in
`check-green.json` in the worktree's own git dir, never tracked; a later run
whose fingerprint matches prints
`check.sh: OK (tree unchanged since the last green full run at <time>)` and
exits 0 in under a second, against about 200s for the run it skips (both on
the 18-core Mac, unsandboxed, 2026-11-11). Common
cases: a re-check before pushing, an amend that only rewords the commit, and a
second agent in the same worktree.

The fingerprint, computed by `scripts/check-cache.py`, covers:

- the working tree as `git add -A` would stage it: tracked edits and untracked,
  non-ignored files, built in a temporary index so the real one is untouched.
  Staging and committing do not change it, and pins in tracked files
  (`mise.toml`, the mypy pin) come with the tree.
- four inputs from outside the tree: the installed `claude` version,
  the agent-guidance plugin copy, `python3 --version`, and whether the run is
  inside Claude Code's sandbox, where some suites skip.

Only a full run stores it, and only when the tree still matches at the end, so
a file edited while the gate ran is not recorded as green. A green full run
satisfies a later `--fast`, `--changed` or `--base` run too, since each only
drops checks.
`--with-evals` and CI (`GITHUB_ACTIONS` or `CI` set) never use it.
`--no-cache` or `CHECK_NO_CACHE=1` forces a run, and a forced green run
still refreshes the record.

The fingerprint does not cover tools that resolve outside the tree and are not
listed above: `uv`, and `dprint`, `node`, `shellcheck` or `shfmt` when they
come from PATH rather than the `mise.toml` pins, which the tree covers. After
upgrading one of those, run with `--no-cache`.

It does not cover two kinds of file inside the tree either, and after editing
one, run with `--no-cache` too:

- **Ignored files the gate reads.** `validate.py` checks the task cards in
  `dev_docs/tasks/`, which `.gitignore` excludes.
- **Uncommitted edits inside a submodule.** The `test/vendor/bats-*` checkouts
  count only by the commit each one is on.

## Choosing checks from the diff

`just check-changed` (`scripts/check.sh --changed`) runs the cheap checks and
only the test units this branch's diff can reach. A test unit is one job of the
full gate: a `test/*.bats` file, a `scripts/test-*.sh` harness, or the
orchestrator suite. `scripts/check-select.py` picks them, and its docstring has
the rules. In short, a changed file selects every test that names it, by file
name, by the unittest file a wrapper runs, or by the package directory a Python
module sits in. A change under `skills/`, `commands/` or `agents/` adds
`test-validate.sh`. The run is the full gate instead when the diff touches gate
infrastructure (`check.sh`, `scripts/lib/`, the bats helper, `mise.toml`…) or
a file outside `dev_docs/` that no test names: a test can read a file through
a glob or a loop without naming it, so an unplaced file is never skipped.
Renames count under both the old and the new path.

This is a heuristic, and it is safe only as an edit loop. Plain `just check`
and CI always run the full gate, so a selection that misses a dependency costs
a red CI run, never a bad merge.

Replaying the last 100 merged PRs through `select()` as shipped, on the
18-core Mac, 2026-11-12: 74 got a subset, 16 the full gate (11 for
infrastructure, 5 for a file no test names), and 10 touched only `dev_docs/`.
The subset runs selected a median of 13 of 136 units and took a median of
142s against 204s for the full gate, about 127 minutes saved over the 100.
32 of the 74 finished within 60s; a trial run after a one-line edit to
`scripts/check-cache.py` selected one unit and took 11s. The times are upper
bounds, taken from one run with every unit in parallel; a subset runs with less
contention.

The median is high because four bats files of 142–167s each
(`guard-pinned-branch`, `worktree-create-hook`, `co-review-ledger-sync`,
`utilities`) are selected often, mostly through the directory rule matching a
skill's bare directory name such as `references`. Narrowing that rule for
markdown would cut the median to about 41s, but tests reach markdown without
naming it in at least three ways: a glob of `reviewers/*.md`, an
`os.path.join(..., "references", "threads.md")`, and a recursive grep of a
skill directory. So the selector keeps the broad rule, and the time comes back
by cutting the processes those files start, which shortens both modes;
splitting them would not on the work laptop, for the reason in
[What the bottleneck is now](#what-the-bottleneck-is-now). Tightening the
rule later needs its own replay, measured against the files each test
actually opens rather than against name matches.

Before it was built (#1492), simpler rules simulated on the same PRs gave
lower figures: 3 units and 20s when only tests naming the file count, 4 units
and 36s with the package and skill rules. Following every chain of files that
name each other selected 121 units, so the selector never does.

Replaying the 12 failed CI runs whose logs carry the current
`check.sh: FAIL (...)` summary, the selection missed 5 failing units and caught
3. All 5 misses were unrelated to their diffs: four `test-spawn-orchestrator.sh`
wall-clock and launchctl failures, and one `test-local-review.sh` that could not
bind its address. That sample is small, which is the main reason this stays an
edit loop rather than becoming the gate.

## Timing warnings

Every `scripts/check.sh` run ends with a `→ timing (<mode>)` block from
`scripts/check-timings.py`: the total wall time and the five slowest checks.
Two warnings follow when they apply, each prefixed `⚠ slower:`. Neither fails
the gate; wall time is too noisy to gate on.

- **Budget.** The gate's total exceeded the budget for its mode, set in
  `scripts/check.sh` beside `timing_mode=`: full 300s, `--fast` 180s,
  dev_docs-only 60s. Each sits above that mode's normal spread on the 18-core
  Mac (full 200–235s over four runs, `--fast` 127s in one run, 2026-11-11) and well
  below the 479s the full gate took with serial bats. A fixed number catches
  slow creep that a rolling
  comparison absorbs: a gate that grows 10% a week never looks unusual against
  its own last few runs. When it trips, the slowest list says where the time
  went. Raise a budget only with a measurement, recorded here.
- **Jump.** A check, or the whole gate, took at least 1.5× its median over this
  machine's recent green runs of the same mode, and at least 30s more. The
  30s floor is set by contention: single checks swing about 2× between loaded
  runs, and a 10s floor warned on two different checks in two consecutive green
  runs (`test-plan-graph.sh` 14s → 29s, `typecheck.sh` 11s → 25s). It needs
  3 earlier runs and keeps the last 20 per mode. This names the check that
  moved. Under `--fast`, a jump on `scripts/lint-shell.sh --fast` usually
  reflects how many shell files the branch touches (the per-file narrowing
  above), not a regression.

The history is `check-timings.tsv` in the clone's common dir, so every worktree
of a clone shares it, and it is never tracked. Only green runs without
`--with-evals` are recorded. CI starts with no history, so there only the
budget applies, emitted as a `::warning::` annotation on the run.

Other load on the machine, such as another session's gate, inflates both
signals. Treat a warning as a prompt to read the slowest list, not as proof of
a regression.

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

The driver A/B in [the concurrency section](#does-adding-concurrency-help) is
what that produces, and it was worth running: its answer would have been easy to
get backwards by reasoning alone.

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
  above ran at 42.7–43.6s for a configuration that measured ~41s on the same
  otherwise idle Linux box, and that section's early isolated runs read ~10% faster than its
  final interleaved ones. That is exactly why the comparison is interleaved:
  both arms drift together, and the _difference_ survives. Absolute numbers in
  this file are worth about ±10%; the ratios are not.
