---
title: Enforce exercising — a load-bearing claim about runtime behavior must be executed, not asserted
priority: high
size: 3
status: ready
created: 2026-07-13
expires: 2026-12-31
source_branch: bestdan/port-orchestrator-to-python
parent: orch_py
related_files:
  - scripts/smoke-confinement.sh:51 # grants git, never RUNS it — the archetype
  - scripts/smoke-confinement.sh:81 # asserts a DENIAL; every allow-side exec is untested
  - dev_docs/decisions/script_language.md:177 # the struck "only option" claim — asserted, never run
  - scripts/audit-orchestrator-reachability.py # the counter-example: a claim made re-runnable
tags: [orchestrator, process, testing, seatbelt, meta]
---

← [[orch_py_plan]]

## Context

**The same failure has now occurred four times on this one plan, and it is always the same
shape:** a claim about runtime behavior is *reasoned to*, written into a document, propagated into
downstream specs — and **never executed even once**. Every instance was cheap to test and none of
them were.

| # | The claim | How long it survived | What exercising it took |
| - | --------- | -------------------- | ----------------------- |
| 1 | "`status`, `classify-exit`, `exit-reason`, `doctor` are freely portable" | until PR #205 co-review | reading the call graph |
| 2 | "the tier boundary is these 9 subcommands" (hand-drawn, twice) | until task 1 | one script — it found **8 more** |
| 3 | "no interpreter can satisfy launchd's PATH **and** the Seatbelt exec allowlist" — the premise the **whole plan** rested on, and Go's entire justification | until task 2 | **one `sandbox-exec` invocation** |
| 4 | "bake the resolved absolute path" (would have died on the first `uv` upgrade, at 3am) | until PR #207 co-review | rendering a profile and exec'ing the symlink |

And the archetype in the code itself: **`smoke-confinement.sh:51` grants `git` to the jail and
never runs `git` in the jail.** The grant is a no-op that has never been exercised — which is
exactly why `orch_py_task_10` (git cannot exec inside the jail) went undetected. The suite asserts
the *allowlist entry*, not the *execution*. Note the asymmetry that made this invisible: §1's exec
tests are all **denials** (`denied "exec unlisted /usr/bin/python3"`), and a denial passes whether
the mechanism works or is broken in a way that denies everything. **Nothing in the suite proves a
permitted binary can actually run.**

The counter-example is `scripts/audit-orchestrator-reachability.py` (task 1): the same class of
claim, made **executable and re-runnable**, immediately found eight subcommands that two rounds of
careful hand-tracing had missed. That is the pattern to generalize.

**This is not a call for more tests in general.** It is narrow: a claim is **load-bearing** if a
downstream decision or spec depends on it. The rule is that a load-bearing claim about *what the
machine does* must be accompanied by a command that **makes the machine do it**.

## Task

> **Scope shrank: PR #208 (task 10's fix) already closed the archetype.** `smoke-confinement.sh`
> now carries positive `allowed "exec git …"` assertions under a real `--toolchain` profile, so a
> granted binary is proven to *run*, not merely to be listed. Step 1 below is therefore **done** —
> what remains is to **generalize** it. Do not re-litigate the git row; build the contract around
> it.

1. ~~**Close the archetype — assert execution, not configuration.**~~ **DONE in PR #208.** It also
   supplied the rule the design must generalize: **grant the _narrowest_ resolve-target dir, never
   the enclosing tree.** #208 grants `<dev>/usr`, not the whole CLT tree — the broad grant would
   have made a second interpreter executable in the jail *with every test still green*. A fixture
   that only checks "does the granted binary run" would **not** have caught that; it needs a
   negative side too (an un-granted interpreter must still be refused).

2. **Make the jail's exec contract executable, both directions.** The recurring root cause is
   subtler than "no test": **Seatbelt checks the path a binary _resolves to_, not the path you
   invoke.** It bit `git` (`/usr/bin/git` → CLT shim) and it bit the port's interpreter
   (`~/.local/bin/python3.11` → uv's version dir) — the *same* defect, found six weeks apart, both
   by accident. Add a fixture that, for each binary the run depends on, resolves it (`readlink -f`
   / the shim's target) and asserts the rendered profile grants **the resolved path** and that it
   **execs**. This is the generalized regression guard for a whole defect class.

3. **Adopt the rule in `dev_docs/decisions/` and enforce it where it is cheap.** A decision doc
   making a load-bearing claim about runtime behavior must cite **the command that exercises it**,
   not prose. Concretely, one of:
   - a checked-in script (`audit-orchestrator-reachability.py` is the model), or
   - a `smoke-*`/harness assertion, or
   - a verbatim, re-runnable repro block in the doc (as `orch_py_task_10` carries).

   Wire what can be wired: extend `scripts/validate.py` (or `just check`) to flag a decision doc
   that asserts a jail/PATH/exec/interpreter property with **no** adjacent citation of an
   executable check. A heuristic linter is acceptable here — it does not need to be sound, it
   needs to make the omission *visible*, since all four failures above were invisible.

4. **Add the tier-boundary gate that task 1 called for and nobody built.** `orch_py_task_3` was
   asked to assert that no subcommand on the Python `PORTED` list appears in the audit's Tier B.
   That is the same rule in a different costume: today the audit is a script someone must
   *remember* to run. Make `just check` run it and fail on a `PORTED` ∩ Tier B intersection.

## Acceptance Criteria

**Code-enforced:**

- `smoke-confinement.sh` contains at least one **positive** exec assertion per class of granted
  binary (`git`, the pinned interpreter, `bash`) — each **fails** if the binary cannot run jailed.
  Verify by deliberately removing the grant and watching the suite go red.
- A fixture asserts, for every run-critical binary, that the **resolve target** (not just the
  invoked path) is on the rendered profile's exec allowlist **and** execs. It must catch both the
  `git` CLT-shim case and the `~/.local/bin/python3.11` → uv-version-dir case.
- `just check` fails if any `PORTED` subcommand is in the reachability audit's Tier B.
- The rule is written into `dev_docs/decisions/` (its own short ADR, or a section of
  `script_language.md`), with the four-failure table above as the evidence — this is a decision
  about how decisions get made, so it belongs there, not in a task card that will be deleted at
  task 9.

**User-run:**

- Re-run the four historical claims against the new fixtures and confirm each would now have been
  caught **before** it reached a spec. If any would still slip through, say so — a rule that would
  not have caught its own motivating failures is theatre.

## Note

Deliberately **not** in scope: a general "write more tests" push, or retro-testing every existing
claim in the repo. The rule binds only **load-bearing claims about runtime behavior** — a claim a
downstream decision or spec is built on. The whole reason it is worth writing down is that the
cost of exercising each of the four failures above was one command, and the cost of not exercising
them was a plan built on a false premise and a jail that cannot run `git`.
