# Testing

What the suites are, and — the part that surprises people — **which of them
actually run on your machine**. The gate does not execute the same set of
assertions everywhere, so "729 passed" means different things on a Linux runner
and on a maintainer's Mac.

Day-to-day contributing doesn't require any of this; see
[`CONTRIBUTING.md`](../CONTRIBUTING.md) for the dev loop and the gate. Read this
when you're interpreting a skip, wondering why CI was green and your laptop
wasn't, or adding a test that depends on the host.

## The suites

| Suite                                | Shape                            | Size            |
| ------------------------------------ | -------------------------------- | --------------- |
| `test/*.bats`                        | Bats, 10 files                   | 66 tests        |
| `scripts/test-spawn-orchestrator.sh` | Hand-rolled asserts, single file | ~729 assertions |
| `scripts/test-*.sh` (the rest)       | Hand-rolled, per-subject         | tens each       |

`scripts/test-shell.sh` runs the Bats files and the orchestrator suite;
`scripts/check.sh` runs everything. The Bats files live in git submodules under
`test/vendor/`, so a fresh `git worktree add` starts with them unpopulated —
`scripts/ensure-bats.sh` recovers that automatically.

## What runs where

**CI runs `ubuntu-latest`.** That single fact drives most of the surprises
below: the entire seatbelt behavioral layer is macOS-only, so **CI never
exercises it**. It runs only on a maintainer's Mac, and only when that Mac can
actually apply a sandbox profile.

| Assertion group                            | macOS (usable seatbelt) | macOS (nested sandbox)  | Linux / CI        |
| ------------------------------------------ | ----------------------- | ----------------------- | ----------------- |
| Seatbelt behavioral (33 skip sites)        | **runs**                | ≥14 skipped             | ≥14 skipped       |
| `confinement smoke rejects non-macOS`      | skipped                 | skipped                 | **runs**          |
| `zip`/`unzip` round-trips (3)              | runs if installed       | runs if installed       | runs if installed |
| Host-fixture blocks (13: git, `plutil`, …) | runs if fixture present | runs if fixture present | several skip      |
| Everything else                            | runs                    | runs                    | runs              |

Two consequences worth internalizing:

- **Linux is not a subset of macOS coverage.** One test runs _only_ where
  `sandbox-exec` is absent (below), so neither platform is strictly weaker.
  Full coverage of the repo is the union of at least two hosts.
- **Never compare counts or timings across platforms.** A run on your Mac and a
  run in CI are executing different assertion sets, so a difference in duration
  or in "N passed" is not by itself evidence of anything.

## The seatbelt probe has three states, not two

`scripts/test-spawn-orchestrator.sh` sets `SEATBELT_OK` by _using_
`sandbox-exec`, not by looking for it:

```sh
SEATBELT_OK=0
if command -v sandbox-exec >/dev/null 2>&1; then
  printf '(version 1)\n(allow default)\n' >"$BASE/seatbelt-probe.sb"
  if sandbox-exec -f "$BASE/seatbelt-probe.sb" /usr/bin/true >/dev/null 2>&1; then
    SEATBELT_OK=1
  fi
fi
```

Presence of the binary does not prove it can apply a profile. Inside a **nested
sandbox** — this suite run under `sandbox-exec`, or under an agent harness that
already sandboxes it — `sandbox_apply` is refused with `Operation not
permitted` before the profile's own rules are ever consulted.

That distinction is load-bearing, because gating on presence alone fails in two
directions at once:

- assertions that need a profile to **succeed** fail outright; and, worse,
- assertions written as _"if `sandbox-exec` denies X, ok"_ **pass for entirely
  the wrong reason** — a refused `sandbox_apply` _is_ a denial, so the assert
  reports ok on evidence it never gathered.

The second is a false pass, which is why the probe exists: it converts both
failure modes into a loud skip. So a skip here means "this did not run", never
"this was checked and was fine".

The three states report distinguishable messages:

```
sandbox-exec present but cannot apply a profile here, nested sandbox
sandbox-exec not available on this host, non-macOS
```

Both are skips, and both leave `SEATBELT_OK=0`, so the layer is equally
unexercised either way. Only the reason differs — read the reason, not the
count.

## The inverted test

`test/utilities.bats` → `confinement smoke rejects non-macOS environments`:

```sh
command -v sandbox-exec >/dev/null && skip "requires a host without sandbox-exec"
```

This is the one assertion that skips **on macOS** and runs **on Linux**. It
exists to prove the confinement guard rejects a host it cannot protect, which
can only be observed where `sandbox-exec` genuinely is missing. Don't "fix" it
to run everywhere — stubbing `sandbox-exec` to make it run on a Mac would test
the stub, not the guard. (`test/smoke-confinement.bats` deliberately _does_
stub it, for a different assertion where the guard exits long before
`sandbox-exec` is reached.)

## Counted skips vs. bare-echo skips

The orchestrator suite distinguishes two kinds, and the distinction is
deliberate:

- **Counted** — `skipped "..."` (33 call sites) increments `skip`. This counter
  means exactly one thing: _seatbelt-behavioral assertions that did not run_.
  Not all 33 fire in a given run — several sit inside blocks with further
  conditions — but `SEATBELT_OK=0` forces at least 14, which is why an
  unseatbelted host reports `14 skipped` rather than `33`.
- **Bare echo** — `echo "skip - ..."` (13 sites: Homebrew Cellar, `python3`,
  `plutil`, `shasum`, `ps -o lstart`, and six git-absent blocks) does **not**
  increment it.

Keep new skips on the correct side. Folding a host-fixture skip into the
counter would break `SO_TEST_REQUIRE_SEATBELT` on hosts where seatbelt coverage
is in fact complete, because the git-absent skips fire on every Linux runner.

## Forcing full coverage

```sh
SO_TEST_REQUIRE_SEATBELT=1 scripts/test-spawn-orchestrator.sh
```

Fails the run if **any** seatbelt assertion was skipped. A zero skip count is
the only thing that means full coverage. Use it on a macOS host, outside any
nesting sandbox, when you have changed confinement/seatbelt behavior and need
the layer actually exercised rather than reported.

CI does **not** set this — it is on `ubuntu-latest`, where the flag could only
ever fail. Verifying a seatbelt change is therefore a manual, local step, and
nothing in the automated gate will catch you skipping it.

Without the flag the suite still announces the gap on exit rather than burying
it:

```
NOTE: 14 seatbelt-behavioral tests were SKIPPED — sandbox-exec cannot apply a
      profile here (nested sandbox). Run this suite unsandboxed for full coverage.
```

## Tests that measure themselves against the wall clock

A separate hazard, but it lands here because it presents as platform variance:
a handful of assertions bound their **success** path by elapsed time, so they
flip under CPU contention rather than because anything broke.

`test/await-pr-review.bats` is the worked example. Its landing tests need two
polls of a stubbed `gh` but once bounded them with a 5s deadline that is only
checked _between_ polls, so one descheduled poll ended the run before the
"landed" fixture was fetched — surfacing as `AWAIT_REVIEW: timeout` with `gh`
called exactly once, reproducibly under `scripts/check.sh` and never in
isolation. It now uses `LAND_TIMEOUT`, a bounded hang-bound rather than a
duration; the timeout-asserting cases keep a small budget, since delay can only
slow those, never fake a pass.

The rule that generalizes: **a test's success path must not be bounded by wall
time.** Bound the condition instead, and reserve elapsed-time budgets for
assertions whose expected outcome is a timeout. Before adding concurrency
anywhere in the gate, grep for `sleep`, `SECONDS`, `--timeout`, and polling
loops in the suites it touches.
