# Gate performance

Where `just check`'s wall time actually goes, why `just check-fast` exists, and
what would have to change for the full gate to get faster.

Day-to-day contributing doesn't require any of this — see
[`CONTRIBUTING.md`](../CONTRIBUTING.md). Read this before trying to speed the
gate up, or before adding concurrency anywhere in it.

This file keeps the rules and the few numbers a decision needs today. Most
numbers link to the dated research record that holds its measurement, machine
and raw figures. Times on your machine will differ; the ratios are what travel.

## Where the time goes

`scripts/check.sh` runs its checks concurrently, so a run costs roughly its
slowest member rather than their sum. Every run ends with its own answer to
this section: a `→ timing` block naming its five slowest checks (see
[Timing warnings](#timing-warnings)). Trust that over any table.

The full gate takes about 175s on the 18-core work laptop
([record](research/2026-11-12-gate-process-start-ceiling.md)). It starts about
150 jobs at once: roughly 100 `scripts/test-*.sh` harnesses, the 40 bats files
with one job each, the orchestrator suite's parts, and the linters. So a single
check's time says as much about contention as about the check: the same harness
can take twice as long in one full run as in another
([record](research/2026-11-12-bats-fan-out.md)). Sys time exceeding user time
says that much of the CPU goes to the kernel starting processes, not to test
logic.

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
`scripts/lib/spawn-orchestrator-test-epilogue.sh` asserts they held. The driver
runs the parts concurrently. Splitting the monolith cut the suite's wall
time to about a third and its ShellCheck bill to about a quarter, even though every part rebuilds
the prelude's fixture bundle
([record](research/2026-11-12-orchestrator-harness-split.md)).

### What a split of this suite has to preserve

Re-derive this before relying on it — and note the scan below is lexical, so
`eval`, indirect expansion and dynamically-built names are invisible to it.

```sh
# names defined in one part and read in another
grep -n '^[A-Za-z_][A-Za-z0-9_]*=\|^[A-Za-z_][A-Za-z0-9_]*()' scripts/test-spawn-orchestrator-*.sh
```

A lexical `$NAME` scan misses two classes of coupling:

- **Bare names inside arithmetic.** A name read only as `$((NAME + 3600))`
  carries no sigil. Grep for the bare word, not just `$`.
- **Fixture files, not variables.** A part can read a file another part's tests
  built, with no variable crossing. The comment above `CX=` in
  `scripts/test-spawn-orchestrator-exit-contract.sh` names one such file, now
  rebuilt locally; the prelude's shared fixture bundle exists for the same reason.

Four guarantees are structural rather than ordinary tests, and any further
resharding has to keep them:

- **The caller-repo snapshot/assert bracket (PRE-618).** The suite drives real
  git-mutating fixtures, some deliberately in a dir that is not its own repo, so
  git walks upward and can reach the caller's. Search for
  `# --- Caller-repo safety snapshot` in the prelude and
  `# --- PRE-618 caller-repo integrity assertion` in the epilogue. Per-part
  detection is _at least_ as strong as a whole-suite version. Be careful about
  the attribution claim, though: the parts run concurrently, so a persistent
  escape fails every part whose epilogue runs after it. That narrows the
  offender to a set, not to one part. (A transient mutate-then-restore inside a
  single run is still invisible.)
- **The notifier guard.** Installed in the prelude (`# --- The notifier guard`),
  asserted in the epilogue (`# --- the notifier guard held`). Its structural
  halves are per-part. Its **behavioral** half cannot be — it asserts the guard
  log is non-empty, and most parts raise no alarm at all — so each part drops
  its count in `$SO_TEST_TALLY_DIR` and the driver asserts the sum. That is the
  one whole-suite claim in this harness; if you reshard again, it has to move
  with the driver, not be dropped.

  The per-PATH half must assert against the composite `PATH` a fixture
  **actually runs under**, stored whole at its call site. An assertion that
  _reconstructs_ that PATH cannot do its job — re-inject `$GUARD` and it can
  never fail (the guard dir is inside `$BASE`, which is what the check looks
  for), omit it and it reports leaks at safe call sites. Either way it stops
  tracking the fixture while still printing `ok`. The epilogue's enumerated
  list carries this warning; keep it exhaustive with the grep it names.
- **Opt-in strict seatbelt mode.** `SO_TEST_REQUIRE_SEATBELT=1` demands the
  seatbelt-behavioral assertions actually ran. It gates on the skip count, and
  per-part gating is equivalent to a whole-suite one: the run fails if any part
  skipped.
- **macOS runs ~14 assertions Linux skips** (every `sandbox-exec` Seatbelt
  compile, the Homebrew Cellar and CLT-shim grants, the exec-dir escape tests), so
  a split balanced only on Linux is not balanced. They are all in the `profile`
  part.

CI runs the suite on macOS as a second job (`Host-sensitive suites (macOS)`,
added in #360) with `SO_TEST_REQUIRE_SEATBELT=1`, so zero skips there is
enforced rather than hoped for.

## ShellCheck's cost is superlinear in file length

Within one file, ShellCheck's time grows roughly quadratically with its length,
so splitting a long shell file cuts its lint bill as well as its run time. The
same assertions linted as 10 files instead of 1 took a quarter of the time
([record](research/2026-11-12-orchestrator-harness-split.md)).

Two cautions if you measure this:

- **Truncated prefixes lie.** `head -n` usually cuts mid-construct; ShellCheck
  then reports a parse error and exits before doing the expensive analysis, which
  looks like a spectacular speedup. Check `bash -n` on the prefix first.
- **Length alone does not predict cost across files.** The curve is steep
  _within_ one file, but its coefficient is not universal: a longer file can
  lint faster than a shorter one. So splitting a long file is a good bet, not a
  guarantee. Measure the file in front of you rather than extrapolating from
  its line count.

Reproduce the per-file ranking with:

```sh
# the per-file lint bill, worst first
for f in $(git ls-files '*.sh'); do
  printf '%s %s\n' "$( { TIMEFORMAT=%R; time shellcheck -s bash --severity=warning \
    -e SC1090,SC1091 "$f" >/dev/null 2>&1; } 2>&1 )" "$f"
done | sort -rn | head
```

## Does adding concurrency help?

For the bats files and for the orchestrator driver, yes; both were settled by
measurement.

- **The bats fan-out.** `scripts/test-shell.sh` runs each `.bats` file as its
  own job, in every mode. That roughly halved the full gate on the 18-core
  workstation, and the macOS CI job on the 3-core runner
  ([record](research/2026-11-12-bats-fan-out.md)). Serial bats would be
  harmless only if it finished inside the orchestrator suite, and at 40 files it
  does not; the [timing warnings](#timing-warnings) are how a check that grows
  onto the critical path gets noticed.
- **The driver's own concurrency.** Running the orchestrator parts concurrently
  rather than serially took about a third off the full gate on a 4-core box
  ([record](research/2026-11-12-orchestrator-harness-split.md)). It is
  load-bearing, and pays for the extra fixture setup each part costs.

What the fan-out costs is latency per test, on small machines most. On CI's
3-core macOS runner the tests together take 6 to 13 times as long to finish as
they do run serially, with nothing failing. A single macOS job duration says little:
the spread within one arm on one runner type was 90–150s, and `macos-latest`
hands out two runner types that move a run by about as much. **A new bats test
with an elapsed-time bound has to hold on a 3-core runner running 41 processes
at once**: the 40 bats files and the orchestrator suite.

## The wall-clock hazard

Widening concurrency breaks tests that measure themselves against elapsed time —
not because they are slow, but because they get descheduled.
`test/await-pr-review.bats` blew a 5s budget under load and reported `after=9s`.
The repo's convention is to make such a test **time-independent** rather than to
cap concurrency: `LAND_TIMEOUT` there, and `test/claude-auto-resume.bats`
virtualizes the clock outright.

The orchestrator suite runs its parts concurrently, so its bounds matter most.
The ones next in line:

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
machine-wide, most likely because CrowdStrike Falcon vets every launch; no run
has compared one machine with and without it. There the full gate is bounded
by how many processes the whole run starts, not by its slowest file, so
splitting a file buys nothing. On three machines without Falcon, process starts
climb to about the core count, 5–21 times the laptop's ceiling, and the slowest
file can still be the bound. The benchmark, the per-unit CPU ranking and the
concurrency-cap A/B are in
[the process-start record](research/2026-11-12-gate-process-start-ceiling.md);
why splitting does not help on the laptop is in
[the bats record](research/2026-11-12-bats-process-starts.md).

What moves the gate on the laptop is starting fewer processes, wherever they
are, and that helps every other machine too. In a bats file that means two
changes:

- **Build fixtures once.** `setup_file` builds the fixture repos, and each test
  copies them with one `cp -R` instead of about fifteen to twenty `git` calls.
  `pin_git_env` in `test/test_helper.bash` gives `setup_file` the same git
  isolation `setup_test` has.
- **Build hook payloads in bash.** `json_string` in the helper replaces a
  `python3` that builds each payload; read a verdict with a pattern match rather
  than a second `python3`.

Applied to `guard-pinned-branch`, `guard-foreign-worktree` and
`worktree-create-hook` (#1505), that took the full gate from a median of 182s to
173s ([record](research/2026-11-12-bats-process-starts.md)).

What would buy something next: **splitting the slowest files**, on machines
without Falcon only. There the slowest file may still bound the gate; measure
the gate there first.

Four things that look promising do not pay on the laptop:

- **More bats files converted.** After #1505, the next two heaviest files took
  the same two moves (#1509) and cut about 240 of their processes and 4.4
  CPU-seconds, under 1% of the gate's CPU, which an interleaved full-gate A/B
  could not tell apart from noise. What is left in them is the scripts under
  test doing their work
  ([record](research/2026-11-12-bats-fixtures-round-two.md)). Apply the moves to
  a new test as a habit, not as a speedup project.

- **The orchestrator suite.** `test-spawn-orchestrator.sh` is the largest
  single unit, about 128 of the gate's roughly 575 CPU-seconds
  ([record](research/2026-11-12-gate-process-start-ceiling.md)), but the cost is
  `spawn-orchestrator.sh` doing real work. A trace found no hotspot, and the one
  test-side change found saves about 3 CPU-seconds.
- **Capping concurrency (#1498).** Nine jobs at a time made the gate slower and
  cut its CPU by only about 4%. No cap ships.
- **Batching `research-spike.py` calls (#1497).** That harness is about 3% of
  the gate's CPU, most of it interpreter startup that batching would only
  partly remove.

## Skipping an unchanged tree

A repeat run on a tree that already passed the full gate does not run again.
After a green full run, `scripts/check.sh` stores a fingerprint in
`check-green.json` in the worktree's own git dir, never tracked; a later run
whose fingerprint matches prints
`check.sh: OK (tree unchanged since the last green full run at <time>)` and
exits 0 in under a second, against about 200s for the run it skips (18-core
Mac, 2026-11-11). Common cases: a
re-check before pushing, an amend that only rewords the commit, and a second
agent in the same worktree.

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
a red CI run, never a bad merge. On the last 100 merged PRs, 74 got a subset,
with a median of 142s against 204s for the full gate; on the failed CI runs it
was replayed against, every unit it missed failed for reasons unrelated to its
diff ([record](research/2026-11-12-check-selection-replay.md)).

A few slow bats files, selected often through the directory rule matching a
skill's bare directory name such as `references`, set that median. The rule
stays broad, because tests reach markdown without naming it (a glob, a path
join, a recursive grep), so the time comes back by cutting the processes those
files start, which shortens both modes. Tightening the rule needs its own
replay, measured against the files each test actually opens rather than
against name matches.

## Timing warnings

Every `scripts/check.sh` run ends with a `→ timing (<mode>)` block from
`scripts/check-timings.py`: the total wall time and the five slowest checks.
Two warnings follow when they apply, each prefixed `⚠ slower:`. Neither fails
the gate; wall time is too noisy to gate on.

- **Budget.** The gate's total exceeded the budget for its mode, set in
  `scripts/check.sh` beside `timing_mode=`: full 300s, `--fast` 180s,
  `--changed` 120s, dev_docs-only 60s. The full and `--fast` budgets sit above their
  modes' normal spread on the 18-core Mac ([record](research/2026-11-12-bats-fan-out.md)). A fixed
  number catches slow creep that a rolling comparison absorbs: a gate that grows
  10% a week never looks unusual against its own last few runs. When it trips,
  the slowest list says where the time went. Raise a budget only with a
  measurement, recorded in a research record.
- **Jump.** A check, or the whole gate, took at least 1.5× its median over this
  machine's recent green runs of the same mode, and at least 30s more. The
  30s floor is set by contention: single checks swing about 2× between loaded
  runs, and a lower floor warned on unrelated checks in consecutive green runs.
  It needs 3 earlier runs and keeps the last 20 per mode. This names the check
  that moved. Under `--fast`, a jump on `scripts/lint-shell.sh --fast` usually
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
    s=$(date +%s); (cd "$d" && scripts/check.sh --no-cache) >/dev/null 2>&1
    echo "$d $(($(date +%s) - s))s"
  done
done
```

Report wall **and** CPU (`user` + `sys`): a change that raises CPU while holding
wall flat is buying nothing. After any concurrency change, run the full gate at
least four times and confirm zero flakes before calling it green. Record the
result in a dated research record and link it from here, rather than adding the
numbers to this file.

Three traps worth avoiding:

- **A worktree shares the repo's ref store.** Running
  `git update-ref refs/remotes/origin/main HEAD` inside a `git worktree` — a
  plausible way to simulate "the branch has landed", e.g. to measure `--fast`
  with an empty touched set — rewrites that ref for **every** worktree including
  the primary one. The base-vs-branch A/B then silently compares the branch
  against itself and reports a speedup of zero. Use a separate `git clone` for
  that trick, and if a base arm ever measures suspiciously like the branch,
  check `git log --oneline -1 refs/remotes/origin/main` before believing it.
- **Cold caches read as real cost.** A first run can take several times as long
  as the warm ones. Discard the first run of anything.
- **Your own measuring load is background drift.** Isolated runs read faster
  than interleaved ones, and other work on the machine moves both. That is why
  the comparison is interleaved: both arms drift together, and the _difference_
  survives. Absolute numbers are worth about ±10%; the ratios are not.
