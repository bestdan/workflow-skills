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

- a link to `scripts/*.py`, `scripts/*.sh`, `test/**`, or a smoke/audit fixture;
- `sandbox-exec`, `just check`, `uv run scripts/validate.py`,
  `scripts/audit-orchestrator-reachability.py`, or a future exec-contract command;
- a fenced `console` block with at least one command line beginning with `$`.

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

`scripts/audit-orchestrator-reachability.py:22` currently says it is a report and exits 0 always.
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

Contract input:

| Field         | Meaning                                                             |
| ------------- | ------------------------------------------------------------------- |
| `name`        | human label, e.g. `git`, `bash`, `claude`, `python3.11`             |
| `invoke_path` | exact path the runtime will invoke                                  |
| `probe_args`  | cheap command that proves execution, e.g. `--version` or `-c '...'` |
| `resolver`    | how to compute the relevant target grant                            |
| `expected`    | profile entry that must cover the target: literal or subpath        |
| `live_only`   | whether credentials/network are required; default false             |

Resolution rules:

- For normal binaries and symlinks, compute `realpath(invoke_path)` and require the rendered
  profile to grant either the resolved literal or an enclosing approved subpath.
- For known Apple developer-tool shims, run a cheap outside-jail probe or use `xcode-select -p` to
  identify the active developer dir, then require that dir's `usr/bin` or enclosing developer dir to
  be granted. The `git` failure proves `realpath(/usr/bin/git)` alone is insufficient: the file is a
  shim whose second exec target is `/Library/Developer/CommandLineTools/usr/bin/git`.
- For generated launch scripts, assert separately that the invoked path is the stable path while the
  profile grant covers the resolved target. For uv Python this means launch invokes
  `~/.local/bin/python3.11`, while the profile grants `~/.local/share/uv/python`.

Assertions per binary:

1. The invoked path exists and is executable before rendering, unless the contract explicitly marks
   it optional.
2. The rendered profile contains an exec grant covering the resolved target or shim target.
3. `sandbox-exec -f <rendered-profile> <invoke_path> <probe_args>` exits 0.
4. The negative side remains meaningful: at least one ungranted binary still fails, so a profile that
   denies everything or allows everything cannot satisfy the suite accidentally.

Run-critical initial set:

- `bash`: already exercised indirectly by `scripts/smoke-confinement.sh:79`, but should become an
  explicit exec-contract row.
- `git`: required by the unattended agent; task 10 shows the grant without execution at
  `scripts/smoke-confinement.sh:51`.
- `claude`: the generated launch script invokes it under `sandbox-exec` at
  `scripts/spawn-orchestrator.sh:1470`.
- `python3.11`: once Tier B Python lands, launch should invoke the stable symlink and the jail
  should grant the uv install root, per `dev_docs/decisions/script_language.md:242` and `:250`.

The fixture should be available in two modes:

- fast deterministic profile-contract mode for `just check`, limited to rendering and text/metadata
  assertions that do not need credentials;
- macOS smoke mode for `scripts/smoke-confinement.sh`, which actually runs the binaries under
  `sandbox-exec`. The existing smoke already requires macOS and credentials for Claude calls
  (`scripts/smoke-confinement.sh:8`), so keep it outside `just check`.

## Counterfactual Test

| Historical failure                                                                                                                          | Would this design catch it before a spec?                            | Mechanism                             | Notes                                                                                                                                                                                                                                                                                                                                               |
| ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | ------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Tier boundary hand-drawn wrong twice (`dev_docs/orchestrator-python-port.md:53`)                                                            | Yes, after the audit gate exists; partially before then              | Tier-boundary gate plus doc linter    | The audit already found the eight missed subcommands. The new gate prevents later `PORTED` claims from contradicting the audit. The doc linter would also flag "freely portable" / "Tier" claims with no adjacent audit citation.                                                                                                                   |
| False Go premise: no interpreter can satisfy launchd PATH and Seatbelt exec allowlist (`dev_docs/decisions/script_language.md:177`, `:207`) | Yes                                                                  | Doc linter plus exec-contract repro   | The ADR sentence includes `interpreter`, `launchd`, `PATH`, `Seatbelt`, `process-exec`, and `only option`; without a nearby `sandbox-exec` repro or checked-in fixture, the linter fails. The exec-contract fixture gives the expected citation.                                                                                                    |
| `git` grant never executed (`scripts/smoke-confinement.sh:51`; task card at `dev_docs/tasks/orch_py_plan/orch_py_task_10.md:51`)            | Yes                                                                  | Exec-contract fixture                 | A positive row for `git` would run `/usr/bin/git --version` in the rendered jail and fail on this host with `can't exec '/Library/Developer/CommandLineTools/usr/bin/git'`. The failure is not caught by denial-only assertions such as `scripts/smoke-confinement.sh:81`.                                                                          |
| Baked resolved version-stamped interpreter path would break after uv upgrade (`dev_docs/decisions/script_language.md:242`)                  | Yes for the design claim; later drift still needs runtime mitigation | Exec-contract fixture plus doc linter | The fixture distinguishes stable invoke path from resolved grant path: launch invokes `~/.local/bin/python3.11`, profile grants `~/.local/share/uv/python`. It would reject a generated launch script that bakes `.../cpython-3.11.14...`. It cannot prove a future user will not uninstall Python mid-run; that remains an explicit residual risk. |

## Cost and False-Positive Budget

`just check` should stay fast and deterministic. The proposed additions to `just check` are text
and metadata checks only:

- doc linter: markdown text scan over a small `dev_docs` subset;
- tier gate: existing audit text scan plus one set intersection;
- profile-contract text mode: render a profile and inspect exec grants for declared run-critical
  binaries where the required binary is present.

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
   Recommendation: land the exec-contract fixture first, because it directly catches the shared
   resolve-target defect class. Then add the doc linter, then wire the tier gate once the `PORTED`
   list exists.

5. **Should a missing optional binary fail profile-contract mode?**\
   Recommendation: no for optional developer tools, yes for run-critical binaries in the selected
   launch mode. The contract should report skipped optional rows explicitly so absence is visible
   without making non-applicable hosts red.
