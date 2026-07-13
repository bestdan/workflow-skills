# Enforce Exercising Load-Bearing Runtime Claims

**Status:** design for review\
**Scope:** docs-only. This proposes future checks and fixtures; it does not implement them.

## Problem

The failure pattern in `dev_docs/tasks/orch_py_plan/orch_py_task_11.md:22` is narrow and repeatable:
a runtime claim is reasoned to, written into a plan or decision, copied into downstream specs, and
never executed. The current evidence is repo-local:

- `dev_docs/orchestrator-python-port.md:53` says the constrained tier was first hand-drawn wrong,
  then corrected by hand wrong again, before
  `scripts/audit-orchestrator-reachability.py` made the boundary executable.
- `dev_docs/decisions/script_language.md:177` preserves the struck Go premise; lines 207-209 state
  that neither the launchd PATH nor Seatbelt allowlist constraint had been exercised.
- `dev_docs/tasks/orch_py_plan/orch_py_task_10.md:51` identifies the archetype:
  `scripts/smoke-confinement.sh:51` grants `git`, but the suite never runs `git` in the jail.
- `dev_docs/decisions/script_language.md:242` records the launch-script drift trap: a generated
  launch script is written once and re-run by launchd, so a baked version-stamped interpreter path
  fails after a `uv` upgrade.

The deeper defect class is resolve-target blindness. I verified the current host has both forms:

```console
$ command -v git && xcode-select -p
/usr/bin/git
/Library/Developer/CommandLineTools

$ command -v python3.11
/Users/danielegan/.local/bin/python3.11

$ python3.11 -c 'import os,sys; print(os.path.realpath(sys.executable))'
/Users/danielegan/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none/bin/python3.11
```

And I exercised the current renderer with real `sandbox-exec` profiles:

| Profile grant                                                    | Invocation                | Result                                         |
| ---------------------------------------------------------------- | ------------------------- | ---------------------------------------------- |
| `--exec /usr/bin/git`                                            | `/usr/bin/git --version`  | fails: CLT target exec is not permitted        |
| `--exec-dir /usr/bin`                                            | `/usr/bin/git --version`  | fails: CLT target exec is not permitted        |
| `--exec-dir /Library/Developer/CommandLineTools` plus `/usr/bin` | `/usr/bin/git --version`  | succeeds: `git version 2.39.5 (Apple Git-154)` |
| `--exec-dir ~/.local/bin`                                        | `~/.local/bin/python3.11` | fails: symlink target exec is not permitted    |
| `--exec-dir ~/.local/share/uv/python`                            | `~/.local/bin/python3.11` | succeeds: Python reports `(3, 11)`             |

That confirms the rule this design should generalize: for Seatbelt exec, the path that matters is
the path the binary resolves to or re-execs, not necessarily the path the caller invokes.

### The second defect class: assertions that pass vacuously

Resolve-target blindness is only half of what the `git` fix uncovered. The other half is an
assertion that goes green **because the thing under test never ran**. It is the more dangerous of
the two, because it is how a green suite hides an open hole:

- With `curl` unexecutable in the jail, every egress probe returned `rc=126` ("cannot execute") — so
  the suite reported **PASS (blocked)** while proving nothing. `curl` never ran to be blocked. An
  egress test that passes when the network is wide open is worse than no test, and it is what kept
  the layer-2 hole in `dev_docs/tasks/orch_py_plan/orch_py_task_11.md` invisible.
- The "inner sandbox initializes" check passed for the same reason: `claude` never ran to print the
  failure line it was grading.

The shape is general: an assertion whose _pass_ condition is "something went wrong" cannot tell
**denied by policy** from **never executed**. A negative assertion must therefore prove the probe was
executable and actually ran, then was refused — not merely that it exited non-zero. The confinement
smoke's staged-binary fixture is the pattern to lift: it `cp`s a real binary into the RW worktree and
asserts `[ -x ]` **before** the deny assertion, precisely because `denied()` passes on _any_ non-zero
exit, so a silently-failed `cp` would let the assertion pass on `ENOENT` instead of on a Seatbelt
denial.

This design must catch both classes. Mechanism 3 carries the liveness requirement below.

## Decidable Rule

A **load-bearing runtime claim** is a claim that satisfies all three predicates:

1. It asserts machine behavior, not preference or intent. Examples: "this binary runs in the jail",
   "this path is resolvable under launchd's PATH", "this generated script survives tool upgrades",
   "this subcommand is unreachable from constrained contexts".
2. A downstream plan, ADR, task, generated artifact, or safety property depends on it. If changing
   the claim would change the runtime choice, port boundary, allowlist, launch script, or smoke
   expectation, it is load-bearing.
3. It is cheap to falsify in this repo: a script, smoke assertion, generated fixture, or command can
   exercise it without credentials, or with an explicitly live-only smoke gate.

Claims matching this rule must be adjacent to an executable citation. Adjacent means within the same
paragraph, the next paragraph, or the same table row. Acceptable citations are:

- a checked-in script or fixture with the relevant assertion;
- a smoke-suite assertion name and command;
- a re-runnable fenced `console` repro block that includes the command and expected result.

This rule is intentionally about runtime behavior. It does not apply to editorial claims, rough cost
estimates, language taste, or user-facing docs that describe usage without deciding a constrained
runtime behavior.

Honesty clause: this rule catches the four motivating failures only if the doc linter is allowed to
scan planning docs as well as ADRs. If enforcement is limited to `dev_docs/decisions/`, it catches
the false Go premise but not the original port-plan boundary or task-card archetype.

## Enforcement Mechanisms

### 1. Doc linter for visible omissions

Add a repo-native validation pass to `scripts/validate.py`, which already runs in `just check` via
`scripts/check.sh:38`. The linter scans:

- `dev_docs/decisions/**/*.md`;
- `dev_docs/orchestrator-python-port.md`;
- `dev_docs/tasks/orch_py_plan/**/*.md` while that plan exists.

It flags paragraphs and table rows that contain both:

- a runtime domain term: `jail`, `sandbox-exec`, `Seatbelt`, `process-exec`, `exec allowlist`,
  `launchd`, `PATH`, `interpreter`, `symlink`, `shim`, `resolved`, `rendered profile`,
  `generated launch script`, `Tier A`, `Tier B`, `PORTED`; and
- an asserting verb or phrase: `runs`, `cannot`, `can`, `only option`, `satisfies`, `portable`,
  `unconstrained`, `constrained`, `survives`, `fails`, `allowed`, `denied`, `must`.

It passes if the same paragraph/table row or immediately following paragraph contains one of:

- a **`file:line`** citation into `scripts/*.py`, `scripts/*.sh`, `test/**`, or a smoke/audit
  fixture — a bare filename does not pass. A bare `scripts/smoke-confinement.sh` is exactly the
  citation the `git` archetype already had: the script named the binary and never ran it, so naming
  a file proves nothing about whether it asserts anything;
- a named smoke/audit **assertion** plus the command that runs it (e.g. a `§1c` assertion label and
  `bash scripts/smoke-confinement.sh`), or `uv run scripts/validate.py`,
  `scripts/audit-orchestrator-reachability.py`, or a future exec-contract command;
- a fenced `console` block with at least one command line beginning with `$` **and** the expected
  result, so the block is re-runnable and falsifiable rather than decorative.

**No token may appear in both lists.** In particular `sandbox-exec` is a _trigger_ term above, so it
cannot also be a citation: otherwise any paragraph flagged for saying `sandbox-exec` would satisfy
the pass condition by saying it, and the rule could never fire on precisely the paragraphs it exists
to police. A citation must name something _executable_, not repeat the domain term.

It fails with a message like:

```text
dev_docs/decisions/script_language.md:207: load-bearing runtime claim has no adjacent executable citation
```

Escape hatch: require an inline marker with a reason, for example:

```markdown
<!-- exercise-claim: waive because launchd StartInterval overlap needs a live multi-hour run; tracked in dev_docs/... -->
```

The validator fails a waiver that lacks `because` and either a file citation or issue/PR reference.
Waivers are counted at the end of validation. The expected steady-state budget is zero or one; a
new waiver should be treated like adding a skipped test.

### 2. Tier-boundary gate

`scripts/audit-orchestrator-reachability.py:23` currently says it is a report and exits 0 always.
Add a deterministic mode for `just check` that fails if any subcommand on the future Python
`PORTED` list is Tier B. This is the executable form of the claim in
`dev_docs/orchestrator-python-port.md:62`: a subcommand is constrained if it is reachable from a
launchd, jail, or constrained in-process context.

Design details:

- Keep the current report mode unchanged for humans.
- Add a flag such as `--fail-if-ported-tier-b <path-to-ported-list>` once the dispatch seam lands.
- Make `scripts/check.sh` run that flag after the port introduces `PORTED`.
- Output the offending subcommand, handler, and `constrained_by` citation from the audit JSON.

This gate is cheap: the audit is a local text scan over `scripts/spawn-orchestrator.sh` and skill
docs. It has no network, no credentials, and no `sandbox-exec`.

### 3. Exec-contract fixture

Add a positive, resolve-target-aware fixture for every run-critical binary the rendered jail grants.
The fixture should live in the confinement smoke path, but it should be factored as a general
contract rather than a one-off `git --version`.

**Prior art, and the floor this must not fall below.** Task 10 already lands a concrete, execution-
asserting `§1c` in `scripts/smoke-confinement.sh`: `git` runs under the `--toolchain` profile, `gh`
runs (the Homebrew symlink shape), a real `git commit` runs end-to-end, a binary staged in the RW
worktree still cannot exec, and the unlisted-interpreter denial is re-examined rather than flipped.
The general contract's job is to **subsume `§1c`, not to rebuild it** — every assertion above is a
floor, and the refactor must lose none of them.

Contract input:

| Field         | Meaning                                                                                            |
| ------------- | -------------------------------------------------------------------------------------------------- |
| `name`        | human label, e.g. `git`, `gh`, `claude`, `python3.11`                                              |
| `invoke_path` | exact path the runtime will invoke                                                                 |
| `probe_args`  | a command that exercises the **run-critical operation**, not a version string — see below          |
| `resolver`    | how to compute the relevant target grants                                                          |
| `expected`    | the **set** of profile entries that must cover **every** exec hop: literal or subpath, one per hop |
| `live_only`   | whether credentials/network are required; default false                                            |

**`probe_args` must exercise the operation the runtime depends on.** A version string is not a probe.
`git --version` clears the shim's first hop and stops there — but a real `git commit` forks the
`git-core` helpers out of the toolchain's `libexec`, a **second exec hop** that a `bin`-only grant
misses entirely. A contract row of `git` / `--version` would therefore go green against a profile
that still cannot commit, which is this design's own failure mode wearing a new hat: a probe too
cheap to reach the load-bearing path. Probe what the run actually does (`git`: `init` + `commit` +
`log`; `gh`: a subcommand, not `--version`).

Resolution rules:

- For normal binaries and symlinks, compute `realpath(invoke_path)` and require the rendered
  profile to grant either the resolved literal or an enclosing approved subpath.
- For known Apple developer-tool shims, run a cheap outside-jail probe or use `xcode-select -p` to
  identify the active developer dir, then require the dir's **`usr`** subpath to be granted — **not
  `usr/bin`**. `usr/bin` is the grant that misses `<dev>/usr/libexec`, where the `git-core` helpers
  live; granting the enclosing `usr` covers both hops. The `git` failure proves
  `realpath(/usr/bin/git)` alone is insufficient: the file is a shim whose second exec target is
  `/Library/Developer/CommandLineTools/usr/bin/git`, which in turn execs out of `libexec`. Keep the
  grant narrow at `<dev>/usr` rather than the whole developer dir — a whole-`<dev>` grant would
  silently flip the unlisted-`python3` denial into an allow, since CLT's `python3` resolves to
  `<dev>/Library/Frameworks/…`.
- For generated launch scripts, assert separately that the invoked path is the stable path while the
  profile grant covers the resolved target. For uv Python this means launch invokes
  `~/.local/bin/python3.11`, while the profile grants `~/.local/share/uv/python`.

Assertions per binary:

1. The invoked path exists and is executable before rendering, unless the contract explicitly marks
   it optional.
2. The rendered profile contains an exec grant covering **every** declared hop — the resolved target,
   the shim target, and any second-hop helper dir — not just the first.
3. `sandbox-exec -f <rendered-profile> <invoke_path> <probe_args>` exits 0, where `probe_args`
   exercises the run-critical operation.
4. The negative side remains meaningful: at least one ungranted binary still fails, so a profile that
   denies everything or allows everything cannot satisfy the suite accidentally.
5. **Liveness — no vacuous passes.** Every negative assertion must prove its probe was _executable
   and actually ran_, then was refused. A pass condition of "exited non-zero" cannot distinguish
   **denied by policy** from **never executed** (`rc=126`, `ENOENT`), and that ambiguity is what let
   the egress probes report PASS while the network was open. Stage the probe binary, assert `[ -x ]`
   on it, and fail the fixture — not the assertion — if the setup itself did not take.
6. **Launch-script agreement.** Extract the executable path actually baked into the _generated_ launch
   script (`write_launch`, `scripts/spawn-orchestrator.sh:1471`) and require it to equal the
   contract's stable `invoke_path`. Without this, nothing in the contract ever reads the generated
   script, and a launch script that bakes `…/cpython-3.11.14-…/bin/python3.11` would pass every
   assertion above while breaking on the next `uv` upgrade — the fixture would be testing its own
   declared path, not the one launchd will re-exec at 3am.

Run-critical set. Task 10 establishes it as **eight** binaries — `git`, `gh`, `jq`, `rg`, `node`,
`uv`, `claude`, `codex` — and the contract should cover all of them; a fixture that covers half will
not catch the next symlink-farm regression. The rows that carry a distinct _shape_ and must be in the
first slice:

- `git`: the CLT **shim** shape (re-exec plus a `libexec` second hop); grant asserted without
  execution at `scripts/smoke-confinement.sh:51`.
- `gh`: the Homebrew **symlink-farm** shape — a different failure than `git`'s, and the one that
  matters most, since `gh` is how the unattended agent opens PRs. `/opt/homebrew/bin` is 294/295
  symlinks into `Cellar`, so the `bin` grant permitted almost nothing; `jq` and `rg` are the same
  shape and ride along on the `Cellar` grant.
- `claude`: the generated launch script invokes it under `sandbox-exec` at
  `scripts/spawn-orchestrator.sh:1470`.
- `bash`: already exercised indirectly by `scripts/smoke-confinement.sh:79`, but should become an
  explicit exec-contract row — it is the control, a real binary with no indirection.
- `python3.11`: once Tier B Python lands, launch should invoke the stable symlink and the jail
  should grant the uv install root, per `dev_docs/decisions/script_language.md:242` and `:250`.

The fixture should be available in two modes:

- fast deterministic profile-contract mode for `just check`, limited to rendering and text/metadata
  assertions that do not need credentials;
- macOS smoke mode for `scripts/smoke-confinement.sh`, which actually runs the binaries under
  `sandbox-exec`. The existing smoke already requires macOS and credentials for Claude calls
  (`scripts/smoke-confinement.sh:8`), so keep it outside `just check`.

## Counterfactual Test

| Historical failure                                                                                                                          | Would this design catch it before a spec?                            | Mechanism                             | Notes                                                                                                                                                                                                                                                                                                                                                                                                                             |
| ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tier boundary hand-drawn wrong twice (`dev_docs/orchestrator-python-port.md:53`)                                                            | Only once the tier-boundary gate exists                              | Tier-boundary gate                    | The audit already found the eight missed subcommands; the new gate prevents later `PORTED` claims from contradicting it. The doc linter is **not** load-bearing here and is not claimed to be: it fires only on the exact strings `Tier A`/`Tier B`/`PORTED`, so a "freely portable" claim slips past it, and it cannot tell a real audit citation from a plausible-looking fabricated one. Visibility, not proof.                |
| False Go premise: no interpreter can satisfy launchd PATH and Seatbelt exec allowlist (`dev_docs/decisions/script_language.md:177`, `:207`) | Yes                                                                  | Doc linter plus exec-contract repro   | The ADR sentence includes `interpreter`, `launchd`, `PATH`, `Seatbelt`, `process-exec`, and `only option`; without a nearby `sandbox-exec` repro or checked-in fixture, the linter fails. The exec-contract fixture gives the expected citation.                                                                                                                                                                                  |
| `git` grant never executed (`scripts/smoke-confinement.sh:51`; task card at `dev_docs/tasks/orch_py_plan/orch_py_task_10.md:51`)            | Yes                                                                  | Exec-contract fixture                 | A positive row for `git` runs it in the rendered jail and fails on this host with `can't exec '/Library/Developer/CommandLineTools/usr/bin/git'`. The failure is not caught by denial-only assertions such as `scripts/smoke-confinement.sh:81`. Note the probe must be a real `commit`, not `--version`: `--version` clears the shim hop and would still go green on a `bin`-only grant that cannot fork the `git-core` helpers. |
| Baked resolved version-stamped interpreter path would break after uv upgrade (`dev_docs/decisions/script_language.md:242`)                  | Yes for the design claim; later drift still needs runtime mitigation | Exec-contract fixture plus doc linter | Assertion 6 reads the _generated_ launch script and requires its baked path to equal the stable `invoke_path`: launch invokes `~/.local/bin/python3.11`, profile grants `~/.local/share/uv/python`. It would reject a launch script that bakes `.../cpython-3.11.14...`. It cannot prove a future user will not uninstall Python mid-run; that remains an explicit residual risk.                                                 |
| Egress probes reported PASS while the network was wide open (`dev_docs/tasks/orch_py_plan/orch_py_task_11.md`)                              | Yes                                                                  | Liveness assertion (assertion 5)      | With `curl` unexecutable, every probe returned `rc=126` and the deny assertion passed on "exited non-zero" — never distinguishing _denied by policy_ from _never executed_. Assertion 5 fails the fixture when the probe was not executable, so the suite cannot report a blocked network it never tested. This is the row the resolve-target mechanisms alone do **not** cover.                                                  |

## Cost and False-Positive Budget

`just check` should stay fast. The proposed additions to `just check` are text and metadata checks
only:

- doc linter: markdown text scan over a small `dev_docs` subset — fully deterministic;
- tier gate: existing audit text scan plus one set intersection — fully deterministic;
- profile-contract text mode: render a profile and inspect exec grants for declared run-critical
  binaries. This one is **host-dependent, not deterministic**, and the design should stop pretending
  otherwise: it calls `realpath` and `xcode-select -p`, so its result varies with which developer dir
  is selected, whether Homebrew is installed, and whether the uv Python root exists. Two ways to
  handle that, and the check should do both — resolve **host-independently where possible** by
  PATH-shadowing `xcode-select` with a stub pointing at a temp dir (the technique task 10's
  `scripts/test-spawn-orchestrator.sh` already uses, which is what lets its toolchain-grant
  assertions run with no Xcode, no CLT, and no macOS), and, for rows that genuinely need the live
  host, report a **skip** rather than a pass when the binary is absent (Open Question 5).

Do not add live `sandbox-exec` smoke, Claude credentials, network, launchd bootstrap, or multi-wake
tests to `just check`. Those stay in explicit smoke/live commands.

Expected linter noise is moderate on first landing because it is heuristic. Keep the first version
targeted to the terms above and only fail in the three orchestrator runtime doc areas. If it flags a
real claim with no executable citation, add the citation. If it flags text that is not load-bearing,
prefer narrowing the regex over waiving. Use waivers only when the claim is legitimately
unexercisable or too expensive for this repo.

To keep waivers from becoming the default:

- every waiver must include `because` plus a tracking citation;
- `validate.py` prints a waiver count;
- the design target is zero steady-state waivers;
- no blanket file-level waiver.

## Non-Goals

- No general "increase test coverage" mandate.
- No enterprise process, review checklist, or required test plan template.
- No dependencies beyond existing repo tooling.
- No attempt to retroactively exercise every old runtime claim in the repository.
- No credentialed or networked checks in `just check`.
- No claim that the doc linter is sound. Its job is to make omissions visible, not to prove all
  runtime claims are tested.
- No implementation in this design PR: no linter code, no smoke-suite edits, no changes under
  `scripts/`.

## Open Questions

1. **Should the doc linter scan task cards, or only durable docs?**\
   Recommendation: scan `dev_docs/tasks/orch_py_plan/**/*.md` while this plan exists. Two of the
   motivating failures are task-card-visible, and ignoring the plan docs would knowingly miss the
   omissions at the point they are introduced.

2. **Should exec-contract text mode run in `just check` before macOS smoke mode exists?**\
   Recommendation: yes. Text mode catches stale generated launch paths and missing target grants
   without credentials. The actual `sandbox-exec` execution remains a separate macOS smoke.

3. **How should shim targets be declared?**\
   Recommendation: explicit resolver cases for known shim families, starting with
   `xcode-select -p` for Apple developer tools. Do not infer all second execs dynamically from
   stderr; use stderr only as a useful failure message.

4. **What is the first implementation slice after review?**\
   Recommendation: **refactor task 10's `§1c` into the general exec contract** — the fixture is not
   built from scratch, it already exists and is asserting; the slice is generalizing it (contract
   rows, multi-hop `expected`, the liveness assertion, the remaining run-critical binaries) without
   dropping a single assertion it already makes. That comes first because it directly catches both
   defect classes. Then add the doc linter, then wire the tier gate once the `PORTED` list exists.

5. **Should a missing optional binary fail profile-contract mode?**\
   Recommendation: no for optional developer tools, yes for run-critical binaries in the selected
   launch mode. The contract should report skipped optional rows explicitly so absence is visible
   without making non-applicable hosts red.
