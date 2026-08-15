# Testing

What the suites are, and — the part that surprises people — **which of them
actually run on a given host**. The gate does not execute the same set of
assertions everywhere, so "729 passed" means different things on a Linux runner
and on a Mac. CI runs both, precisely because neither is enough on its own.

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
`scripts/check.sh` runs everything. The `test/*.bats` files are ordinary tracked
sources — it is the Bats **runner and helper libraries** (`bats-core`,
`bats-assert`, `bats-file`, `bats-support`) that live in git submodules under
`test/vendor/`. A fresh `git worktree add` starts those submodules unpopulated;
`scripts/ensure-bats.sh` recovers that automatically.

## What runs where

**CI runs two jobs on two hosts** (`.github/workflows/ci.yml`):

| Job                             | Runner          | Entrypoint                                            |
| ------------------------------- | --------------- | ----------------------------------------------------- |
| `Deterministic gate`            | `ubuntu-latest` | `scripts/check.sh` — the whole gate                   |
| `Host-sensitive suites (macOS)` | `macos-latest`  | `scripts/test-shell.sh`, `SO_TEST_REQUIRE_SEATBELT=1` |

The macOS job runs the host-dependent layer only — the Bats files and the
orchestrator suite. Everything else in `check.sh` (dprint, `validate.py`,
typecheck, the python suites) is host-independent, so running it twice would buy
nothing but a toolchain install.

That job exists because the seatbelt behavioral layer is macOS-only. On
`ubuntu-latest` it reports `14 skipped` on every run, so for most of this repo's
life **CI never exercised it** and a green badge was not evidence that
confinement works. It now runs for real, under the strict flag, so green means
zero seatbelt assertions were skipped rather than "the layer was reported
absent".

| Assertion group                            | macOS (usable seatbelt) | macOS (nested sandbox)  | Linux             |
| ------------------------------------------ | ----------------------- | ----------------------- | ----------------- |
| Seatbelt behavioral (33 skip sites)        | **runs**                | ≥14 skipped             | ≥14 skipped       |
| `confinement smoke rejects non-macOS`      | skipped                 | skipped                 | **runs**          |
| `zip`/`unzip` round-trips (3)              | runs if installed       | runs if installed       | runs if installed |
| Host-fixture blocks (13: `plutil`, git, …) | runs if fixture present | runs if fixture present | several skip      |
| Everything else                            | runs                    | runs                    | runs              |

Both CI jobs are columns in that table — the ubuntu one is "Linux", the macOS one
is "macOS (usable seatbelt)", confirmed by probe rather than assumed (see
[Forcing full coverage](#forcing-full-coverage)).

Three consequences worth internalizing:

- **Linux is not a subset of macOS coverage.** One test runs _only_ where
  `sandbox-exec` is absent (below), so neither platform is strictly weaker.
  Full coverage of the repo is the union of at least two hosts — which is why CI
  adds a macOS job rather than switching to one.
- **Never compare counts or timings across platforms.** Two runs on different
  hosts are executing different assertion sets, so a difference in duration or in
  "N passed" is not by itself evidence of anything. This now applies _within_ a
  single CI run, since its two jobs are different hosts.
- **A skipped assertion on your laptop may still be covered.** Working on Linux
  no longer means the seatbelt layer goes unchecked until someone runs it on a
  Mac; the macOS job will catch it on the PR.

## The Bash 3.2 floor is checked by the macOS job, and nowhere else

`AGENTS.md` requires shell here to run under Bash 3.2 — no associative arrays, no
`declare -A`. Nothing asserts that directly: there is no `BASH_VERSINFO` gate
anywhere in `scripts/`, and the ubuntu job runs Bash 5, so the rule was upheld by
care alone.

The macOS job checks it incidentally but genuinely. On the runner image Homebrew's
bash is **not** ahead of `/bin/bash` on `PATH`:

```
env bash : /bin/bash
         : GNU bash, version 3.2.57(1)-release (arm64-apple-darwin25)
```

so the 31 scripts carrying `#!/usr/bin/env bash` execute under 3.2 there, and a
`declare -A` would fail the job.

Treat that as observed, not guaranteed. It is a property of that image's `PATH`
(measured on `macos26 / 20260728.0273.1`, macOS 26.5.2, arm64), not a contract —
if a future image puts Homebrew's bash first, the floor silently stops being
checked and nothing will announce it. Pin an explicit `/bin/bash` invocation if
that floor ever needs to be guaranteed rather than merely likely.

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

Two of the three states are skips, and they report distinguishable messages (the
third simply runs the assertions, so it announces nothing):

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
- **Bare echo** — `echo "skip - ..."` (13 sites: `plutil` ×2, seven git-absent
  blocks, and one each for Homebrew Cellar, `python3`, `shasum`, and
  `ps -o lstart`) does **not** increment it.

Keep new skips on the correct side. Folding a host-fixture skip into the counter
would break `SO_TEST_REQUIRE_SEATBELT` on a Mac whose seatbelt coverage is in
fact complete: the Homebrew Cellar check, for instance, skips whenever
`/opt/homebrew/Cellar` is absent, which says nothing at all about whether the
seatbelt layer ran.

(The comment in the suite justifying this says the _git_-absent skips fire on
every Linux runner. They don't — each is guarded by `command -v git`, and CI's
`ubuntu-latest` ships git. The rule is right; that particular example isn't.)

## Forcing full coverage

```sh
SO_TEST_REQUIRE_SEATBELT=1 scripts/test-spawn-orchestrator.sh
```

Fails the run if **any** seatbelt assertion was skipped. A zero skip count is
the only thing that means full **seatbelt** coverage — it says nothing about the
host-fixture or inverted-test skips above. Use it on a macOS host, outside any
nesting sandbox, when you have changed confinement/seatbelt behavior and need
the layer actually exercised rather than reported.

**CI sets this**, on the macOS job. A seatbelt regression now fails the gate on
the PR instead of waiting for someone to remember to check locally. Run it by
hand when you want the layer exercised on your own Mac before pushing.

That the flag is satisfiable on a GitHub runner was established by probe before
the job was written, not assumed: `macos-latest` can apply a profile, and the
suite passes there with zero skips (runs
[31872616800](https://github.com/bestdan/workflow-skills/actions/runs/31872616800)
and
[31872828322](https://github.com/bestdan/workflow-skills/actions/runs/31872828322)).
The distinction mattered — a runner that _had_ `sandbox-exec` but could not apply
a profile would have produced a job that skipped exactly what it was added to run.

One fragility to know before you debug a red macOS job: the flag gates on the
skip **count**, and a few of the counted sites sit inside blocks that also need a
host fixture (`launchctl`, `plutil`, a distinct fixture binary). All were present
on the `macos26` image. A future runner image that drops one would fail this job
for a reason that has nothing to do with seatbelt, so read the skip reasons
before assuming a regression.

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

The rule that generalizes: **a test's success path must never have to win a race
against the clock.** That is not the same as banning timeouts on success paths —
`LAND_TIMEOUT=60` is one, and it is correct. The distinction is what the number
means. A deadline that a healthy run misses by three orders of magnitude is a
_hang backstop_: it bounds how long a broken script can wedge the suite, and no
amount of load reaches it. A deadline a loaded machine can plausibly exhaust is a
_duration expectation_, and it makes the test a coin flip.

So: keep success-path budgets far above any plausible runtime, and keep them
bounded so a genuine regression fails rather than hangs. Reserve _small_ budgets
for assertions whose expected outcome is itself a timeout. Before adding
concurrency anywhere in the gate, grep for `sleep`, `SECONDS`, `--timeout`, and
polling loops in the suites it touches — and read
[`gate-performance.md`](gate-performance.md), which covers where the gate's wall
time actually goes and why more concurrency does not help.
