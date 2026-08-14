# Would `research-spike` be a good application of Lean?

Evaluated: Lean 4 (the [lean-lang.org](https://lean-lang.org/) dependently-typed
functional language and proof assistant) as an alternative implementation
language for `scripts/research-spike.py`.

**Verdict: no — and the reason is more interesting than "too exotic."** The
script has almost no algorithm to verify. Of its 3,800 lines, 49% are English
and 39% are code; ignoring blank lines the split is 55/45. What that code does
is counting, grouping, and enum membership. Lean's value lands on the part of
this file that is already the least likely to be wrong, and its costs land on
the parts that carry the actual risk: filesystem resolution, markdown
tolerance, and message prose.

There is a real Lean insight here, though, and it is at the design level rather
than the implementation level — see "The part that _is_ Lean-shaped" below.

## What the file actually is, measured

`scripts/research-spike.py`, 3,800 lines. Every line is assigned to exactly one
bucket, by the priority blank > docstring > string literal > comment-only >
code, so the columns partition the file and sum to 100%. A line carrying both
code and a trailing comment counts as code:

| Category                                      | Lines |  Share |
| --------------------------------------------- | ----: | -----: |
| Docstrings                                    |   594 |  15.6% |
| Comment-only lines                            |   421 |  11.1% |
| Non-docstring string literals (message prose) |   848 |  22.3% |
| Blank                                         |   437 |  11.5% |
| Code                                          | 1,500 |  39.5% |
| **Total**                                     | 3,800 | 100.0% |

English (docstrings + comments + message prose) is 1,863 lines, 49.0%. Code is
1,500 lines, 39.5%. Excluding blank lines entirely, the file is 55% English to
45% code.

65 `report.error`/`report.warn` call sites (63 errors, 2 warnings). 7 compiled
regexes. 35 filesystem operations. 93 functions, 17 classes of which 15 are
`@dataclass`. The 3,577-line Bash fixture suite
(`scripts/test-research-spike.sh`) is roughly as large as the script.

And a large fraction of those 1,500 code lines is dataclass declarations and
argparse wiring. The genuinely computational core — `resolve_blockers`,
`derive_counts`, `decision_status`/`decision_statuses`, the ledger splicing and
the freshness comparison — is **173 code lines**, across a 360-line span once
its docstrings and comments are counted.

That ratio is the whole argument. **A verification tool is paid for by
algorithmic risk, and this file has very little.**

## What Lean is good at, as of mid-2026

Grounding, because the answer depends on Lean's actual current state and not on
its reputation:

- Lean 4 compiles through C to native binaries and is a credible general-purpose
  functional language, not only a proof assistant. There is a first-party CLI
  argument library ([`leanprover/lean4-cli`](https://github.com/leanprover/lean4-cli))
  and a formally verified regex engine
  ([`pandaman64/lean-regex`](https://github.com/pandaman64/lean-regex), two
  engines, both proven against a formal semantics).
- The [Lean FRO Year 3 roadmap](https://lean-lang.org/fro/roadmap/y3/)
  (Aug 2025 – Jul 2026) is explicitly aimed at closing the general-purpose gap:
  finishing `Std` (containers, networking, async I/O) toward a 1.0 RC, a
  formatter, better error messages, Lake stabilization, cloud caching, smaller
  binaries, and a stability policy. That is a roadmap for a language that is
  _becoming_ production-ready for general software, not one that already is.
- Where Lean pays: subtle algorithms with stateable correctness properties —
  compilers, cryptographic primitives, schedulers, consensus, numerics, parsers
  with tricky grammars, anything where "it passed the tests" and "it is correct"
  are meaningfully different claims.

## Dimension-by-dimension against the Python

| Dimension             | Python (today)                                                                | Lean 4                                                                                                                                              | Who wins                                |
| --------------------- | ----------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------- |
| Illegal states        | Statuses are `str`; enums checked at validate time                            | Inductive types make a bad status unrepresentable post-parse                                                                                        | **Lean** (real)                         |
| Exhaustiveness        | `is_live_blocker` fails closed by hand, with a 12-line comment explaining why | Compiler-checked `match` over the status type                                                                                                       | **Lean** (real)                         |
| Nontrivial proofs     | n/a                                                                           | Nothing here needs one — see below                                                                                                                  | Tie (nothing at stake)                  |
| Termination           | All loops trivially finite                                                    | `scan_blocks` is index-bounded, so it ports as well-founded recursion on `n - i` — provable, at the cost of a `decreasing_by` obligation per branch | Mild **Python** (a cost, not a blocker) |
| Filesystem + `--root` | 35 path ops, plain                                                            | All in `IO`, all unverified; the type system does not know which repo you meant                                                                     | **Python**                              |
| Message prose         | 848 lines of f-strings, edited constantly                                     | Same lines, in a language with worse string ergonomics and a compile step                                                                           | **Python**                              |
| Test suite            | Bash fixtures over a temp tree, no build                                      | Would need porting; `#guard_msgs` is nice but the fixtures are filesystem trees                                                                     | **Python**                              |
| Distribution          | `python3 script.py` — present everywhere                                      | Ship per-platform binaries in a git-cloned plugin, or make users install elan                                                                       | **Python, decisively**                  |
| Iteration speed       | Edit, run                                                                     | Lake build; toolchains are hundreds of MB each                                                                                                      | **Python**                              |
| Contributor pool      | Anyone                                                                        | Very few                                                                                                                                            | **Python**                              |

## The invariants Lean could prove, and why each one is weak here

Taking the strongest candidates honestly:

1. **`write-ledger` then `validate --strict` is clean.** A genuine round-trip
   property, and a genuine bug class — and the most provable thing here.
   `find_ledger_block(lines)`, `split_track_sections(section)` and
   `insert_track_section(body, track, rel)` are pure `list[str]` → `list[str]`
   transformations, so the whole splice-and-reparse round trip ports to Lean as
   a theorem; only choosing, reading and writing the right file stays an
   external-effect assumption. The objection is not "unprovable," it is
   "shallow and already covered": the property is a data-structure round trip
   that the 3,577-line fixture suite exercises directly, and no bug in this
   area has ever been about the splice arithmetic. The residual risk is
   `check_ledger_block` being pointed at the wrong file — which is the `IO`
   half, and is exactly the PRE-611 shape below.
2. **`status` and `validate` agree about what blocks a decision.** Already
   solved structurally: both call `resolve_blockers`, and the docstring says
   why. Sharing the implementation is the proof.
3. **Track counts sum to the project total.** True by construction in three
   lines of `sum()`. A theorem here would be longer than the code.
4. **No dependency cycles.** Cannot happen. `blocks:`/`blocking:` point
   questions and obligations at decisions only — a bipartite graph, acyclic by
   construction. There is no graph algorithm in this file to verify.
5. **Id qualification is injective.** `project/track/id` vs `project/id`. One
   `if`. Trivially checkable by reading.

That is the entire list. **There is no subtle algorithm in
`research-spike.py`.** Note where the weakness in each item sits: only item 1 is
weak because of `IO`, and even there the pure core is provable. The other four
are weak because the property is trivial or holds by construction. More of this
file is verifiable than a first pass suggests — it is just that verifying it
buys very little.

## The defect actually on record

The script's own docstring documents PRE-611: an `__file__`-anchored `--root`
default scanned the _installed plugin_ instead of the consumer repo and exited
green, because the plugin has no `dev_docs/research/` tree and "no research dir
is clean." The failure was byte-identical to success.

A total functional language with dependent types catches none of that. It is a
default-value and deployment-context bug — a wrong-but-well-typed `Path`. The
fix that worked was the one applied: change the default, and write `--root`
explicitly at every call site in SKILL.md.

This is the representative bug for this codebase. The residual risk here is
**environmental and semantic**, not algorithmic, and formal methods have no
purchase on it.

## Distribution is the hard stop

`workflow-skills` ships as a Claude Code plugin via `.claude-plugin/marketplace.json`,
sourced as a git URL. Consumers clone the repo. **There is no build step, and
there cannot be one.** SKILL.md invokes:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$(...)" validate
```

A Lean port has two options, both bad:

- **Commit prebuilt binaries** for macOS arm64/x64 and Linux arm64/x64 into a
  repo everyone clones — several MB each, checked in, re-signed on every
  release, and a supply-chain surface the current stdlib-only Python script
  explicitly does not have.
- **Require consumers to install elan and run `lake build`** on first use.
  Hundreds of MB per toolchain and a multi-minute build, as a precondition for a
  markdown linter. Nobody installs a theorem prover to run `/research-spike`.

The Python script's dependency profile is its most valuable non-functional
property: `python3` and nothing else. That is deliberate and stated in the
design. Lean cannot match it.

## The part that _is_ Lean-shaped

The interesting finding: **`research-spike` has independently reinvented Lean's
proof-obligation model.** Line them up —

| research-spike                                                    | Lean 4                                       |
| ----------------------------------------------------------------- | -------------------------------------------- |
| `kind: stub` card                                                 | `sorry`                                      |
| `superseded_when:` — a stub must state its own deletion condition | a `sorry` is a warning that never goes quiet |
| `LEDGER.md` open-obligation count                                 | `#print axioms` surfacing `sorryAx`          |
| `destination:` must point at a file that exists                   | a proof term must actually elaborate         |
| "never silently create a destination to make a record resolve"    | you cannot discharge a goal by asserting it  |
| derived, never stored — no `ready:` key to hand-edit              | proof state is checked, never asserted       |

Both systems say the same thing: **unfinished work must have an address, must
be counted mechanically, and must never be dischargeable by assertion.** SKILL.md's
"or this becomes theatre" list is the informal version of Lean's trusted-kernel
argument.

So Lean is the right thing to have _read_ before designing this instrument, and
the right thing to cite when defending it. That is different from being the
right thing to write it in — and it is a compliment to the design, which already
absorbed the idea it needed.

## Cheaper ways to get the real 20%

The two genuine Lean wins above are **illegal states** and **exhaustive
matching**, and neither requires a proof assistant:

1. **`StrEnum` / `Literal` types plus `mypy --strict`** over the record model,
   killing the stringly-typed status class. For exhaustiveness, `--strict` is
   **not** enough on its own: `exhaustive-match` is an optional error code,
   off by default. Either add `--enable-error-code exhaustive-match` (mypy
   1.17+) or put `assert_never(x)` in each `match`'s default arm, which mypy
   has checked at any strictness for much longer. Cost: a day. No new runtime
   dependency; `mypy` is dev-only.
2. **Property-based tests with `hypothesis`** for the round-trip invariant —
   generate arbitrary trees, assert `validate(write_ledger(t))` is clean, assert
   track counts sum to the total, assert `is_live_blocker` fails closed on
   garbage statuses. This is 80% of the confidence a proof would give, at
   roughly 2% of the cost, and it exercises the file-selection and read/write
   path a Lean proof would still have to assume. Dev-only dependency, run under
   `uv` like `validate.py`.
3. If ADTs are genuinely the goal and a rewrite is on the table anyway, **OCaml
   or Rust** dominates Lean for this job: same sum types and exhaustiveness,
   vastly better tooling and distribution, and no proof obligations you did not
   ask for. But recommendation 1 gets most of it without leaving Python or
   breaking the zero-dependency invocation.

## If someone still wants to try Lean in this repo

The narrowest defensible slice, in decreasing order of sanity:

1. **Lean as an executable spec, not the shipped tool.** Model the record
   grammar and the derivation rules in Lean, prove the round-trip and
   sum-consistency theorems, and treat it as documentation. Honest caveat: it
   proves nothing about the Python that actually runs, so it is a design
   artifact, not a guarantee.
2. **A better candidate exists in this repo.** `scripts/plan-graph.py` (244
   lines) does Kahn's algorithm with cycle detection and fail-closed ordering.
   That is a real algorithm with real invariants — acyclicity, order
   completeness, "cycles and order are disjoint" (which its own comment
   asserts). If the goal is to learn Lean on something in this codebase, that
   file is the right target and `research-spike.py` is the wrong one. It would
   still not be worth shipping.
3. **Nothing else.** The 3,577-line Bash fixture suite already covers the
   behavior that matters, and it covers the file-selection and read/write paths
   a Lean port would leave in `IO` regardless.

## Summary

| Question                                    | Answer                                                                     |
| ------------------------------------------- | -------------------------------------------------------------------------- |
| Is there a hard algorithm to verify?        | No — counting, grouping, enum membership                                   |
| Would Lean's type system help?              | Yes, modestly — and typed Python plus `assert_never` gets most of it       |
| Would Lean's proofs help?                   | Barely — most provable properties here are trivial or hold by construction |
| Would Lean have caught the one real defect? | No — PRE-611 was a well-typed wrong path                                   |
| Can Lean match the distribution constraint? | No — the plugin has no build step and cannot get one                       |
| Is Lean the right conceptual model?         | **Yes** — the obligation ledger is `sorry` + `#print axioms`               |

Take the idea, keep the Python.

## Corrections applied after review

Five findings from the PR review were verified against the source and folded
in. Two of them move the pro-Lean column, and the verdict survives on the
remaining grounds — worth recording rather than quietly patching:

1. **The line-count table did not partition.** The original buckets summed to
   3,891 lines for a 3,800-line file and 102%, because blank lines inside
   docstrings were counted twice. Recomputed as a strict priority partition;
   the ratio moved from "~60% English / ~40% code" to 49% English, 39.5% code,
   11.5% blank — 55/45 excluding blanks. The argument is unchanged in kind but
   was overstated by roughly ten points.
2. **The inventory numbers were grep artifacts.** 68 call sites and 17
   dataclasses came from a grep that also matched `report.error` inside
   docstrings and counted every class as a dataclass. The AST counts are 65
   call sites (63 errors, 2 warnings) and 15 `@dataclass` among 17 classes.
3. **The termination row was wrong.** `scan_blocks` advances indices bounded by
   `len(lines)`, so it ports as well-founded recursion on `n - i` — no
   `partial def`, no fuel. The honest cost is a `decreasing_by` obligation per
   branch, which is a tax, not a blocker.
4. **Markdown splicing is not part of the `IO` boundary.** `find_ledger_block`,
   `split_track_sections` and `insert_track_section` are pure list
   transformations and are fully provable. The round-trip objection was
   restated on its real footing: the property is shallow and already covered by
   the fixture suite, not unreachable.
5. **`mypy --strict` does not enable exhaustive matching.** `exhaustive-match`
   is an optional error code, off by default; the recommendation now names
   `--enable-error-code exhaustive-match` or `assert_never`.

## Sources

- [Lean Programming Language](https://lean-lang.org/learn/)
- [Lean FRO Year 3 Roadmap](https://lean-lang.org/fro/roadmap/y3/)
- [leanprover/lean4-cli](https://github.com/leanprover/lean4-cli)
- [pandaman64/lean-regex](https://github.com/pandaman64/lean-regex)
- [Managing Toolchains with Elan](https://lean-lang.org/doc/reference/latest/Build-Tools-and-Distribution/Managing-Toolchains-with-Elan/)
- [Lean (proof assistant) — Wikipedia](https://en.wikipedia.org/wiki/Lean_(proof_assistant))
