# Python type checking: mypy, four tiers, a 3.9 consumer floor

Why the gate runs mypy rather than pyright or `ty`, why tiers are split by who
executes a file rather than by where it lives, and what would legitimately
change either answer.

Measured 2026-09-04 against mypy 1.18.2, pyright 1.1.411, ty 0.0.78. Every
number below came from running the tool, not from reading its docs.

## The problem

The gate ran mypy, so the repo looked type-checked. It was not.

`scripts/typecheck.sh` ran `--strict` on the one annotated file and **default
settings** on everything else — including all 14 consumer-executed handler
assets. Default mypy **skips the bodies of unannotated functions**, and every
file on that tier is unannotated. So the tier covering every file a consumer
actually executes checked signatures that do not exist and read no function body
at all. `typecheck: OK` there meant almost nothing.

That is the same failure shape as the fenced-shell-block gap
([Logic goes in a typed file](../../CONTRIBUTING.md#logic-goes-in-a-typed-file)):
a check reporting success while covering nothing. Turning on
`--check-untyped-defs` surfaced **14 findings in 6 files**, two of them real
latent defects.

## The decision

1. **mypy**, pinned, fetched by `uvx`. Not pyright, not `ty`.
2. **Four tiers split by who runs the file** — `strict`, consumer (3.9), dev
   (3.11), tests — each with `--python-version` pinned.
3. **`ruff check` at default rules only** as a lint floor.
4. **No runtime validation dependency.** Shape checking is `_shape.expect()`,
   stdlib.

## Why the split is by executor, not by directory

The assets run as bare `python3` on **other people's machines**. Which Python
they must survive was a claim in prose that nothing enforced, so
`--python-version 3.9` now enforces it.

Directory is the wrong axis and we got this wrong twice in one PR:
`scripts/local-review/server.py` and `scripts/coreview-rule-drift.py` live under
`scripts/` and are consumer code — launched through `${CLAUDE_PLUGIN_ROOT}` by a
skill and a command. Both were pinned to the contributor floor. Separately,
`bump-version.py` and `coreview-rule-drift.py` matched no tier at all, so mypy
read neither.

Both mistakes were invisible for the same reason: tier membership was a
hand-maintained list nothing compared to reality. `scripts/tier-coverage.py` now
requires the tier arrays plus a declared `EXCLUDED_FROM_TYPECHECK` to
**partition** every `.py` file. A file in no tier fails; so does a file in two,
since the tiers pin different floors and one verdict would be discarded silently.

The floor is not merely asserted. The system `python3` on a stock macOS box is
3.9.6, and the hermetic suites plus the assets they import run under it.

## Why not pyright

Not a toolchain problem — that was checked and is not the reason. CI's `quality`
job already runs `npm install -g` on `ubuntu-latest` with Node preinstalled.

pyright is **marginally the better checker for unannotated code**: it reads
unannotated bodies with no flag, and on the assets it found 10 things to mypy's
0 (at 0.77s vs 0.91s). But of those 10, eight are `__doc__.splitlines()` on
`str | None` — correct and useless here, needing a config entry to silence — one
is the known `importlib` idiom, and **one is a genuine annotation gap**.

One real finding, against a second toolchain pin, a config file, and a rewrite of
the tier script. The reason to want pyright — reading unannotated bodies — is
already bought by `--check-untyped-defs`.

**Do not run both.** They disagree on narrowing, and the first disagreement
forces an ignore comment that the other flags as unused.

## Why not `ty`

`ty` is fast (0.08s) and its diagnostics are good. It is excluded for a reason
independent of its 0.0.x status and its "breaking changes between any two
versions" policy:

**It does not enforce the consumer floor.** `isinstance(v, int | str)` is legal
3.9 _syntax_ that raises `TypeError` at 3.9 _runtime_ — exactly the form a
contributor writes by habit. At `--python-version 3.9`, mypy and pyright both
reject it; ty 0.0.78 reports "All checks passed".

Since enforcing that floor is the whole point of the consumer tier, a checker
that cannot do it is disqualified regardless of its other merits.

## Why not pydantic

A category error that was worth taking seriously anyway: pydantic is runtime
validation, not static checking, and several assets do hand-roll shape checks
over untrusted JSON.

It loses on a hard constraint — the assets are stdlib-only and execute as bare
`python3` on consumers' machines, so a runtime third-party import is a new
install step on someone else's computer.

The named stdlib alternatives do not deliver what pydantic would: `TypedDict` is
erased at runtime and validates nothing, and a `dataclass` with `__post_init__`
is the same `isinstance` chain wearing a class.

**The premise was also partly wrong.** The five `linear-*` assets had _no_ shape
validation at all — `return payload["data"]`, then index — so the gap was in the
files pydantic would not have touched. `_shape.expect()` closes it in stdlib, and
because it returns `T`, every checker narrows at the call site.

## Why not `TypeGuard`

The first reason given for rejecting it was **wrong**, and the correction
matters because the wrong version is the intuitive one.

`TypeGuard` is 3.10+, so it looks like it raises the consumer floor. It does
not: under `if TYPE_CHECKING:` with `from __future__ import annotations` it is
erased at runtime. Verified on CPython 3.9.25 with `typing_extensions` **not
installed** — the module imports and runs.

What actually rules it out is the floor check itself. At `--python-version 3.9`
mypy refuses the symbol (`Module "typing" has no attribute "TypeGuard"`) and
stops narrowing. `typing_extensions.TypeGuard` _does_ work there and needs no
extra install, since mypy already depends on it — so the trade is real but not
forced. `expect()` gets the same narrowing by returning `T`, with neither.

## Why ruff at default rules only

`ruff check` ran nowhere before this; dprint's ruff plugin is the _formatter_.
Default rules (E4, E7, E9, F) were chosen because the repo **already passed
them**, so the gate started green and any finding is a regression rather than a
backlog to burn down.

Measured and rejected: `E501` is 173 of 184 findings in a broader run, almost all
in comments and docstrings that ruff-_format_ leaves alone by design. `B`/`SIM`/
`C4` add 11 minor findings; one of them (`SIM102`) wants a nested guard collapsed
whose intervening comment explains the defect it closes.

## What would change this

Concrete triggers, so a future contributor can recognise one rather than
re-litigate the whole question. **Any one is sufficient.**

1. **`ty` reaches 1.0 with a diagnostics-stability policy, AND rejects
   `isinstance(v, int | str)` at `--python-version 3.9`.** Both conditions. The
   second is a 30-second check — write that expression to a file and run
   `uvx ty check --python-version 3.9` on it. If it passes clean, ty is still
   disqualified no matter what its version number says.
2. **The consumer floor moves off 3.9.** If the project decides to require a
   newer Python, ty's disqualifier may evaporate and the `TypeGuard` question
   reopens on its own merits. Raise `CONSUMER_PYTHON` only by deciding to drop
   support — never to make a diagnostic go away.
3. **The assets get annotated end to end** (a return type on every `def`). Then
   mypy's `Any`-blindness disappears and pyright's inference edge disappears with
   it — which _favours staying_, but makes promoting the consumer tier to
   `--strict` the obvious next move. At strict, pyright's `basic`/`strict` split
   is cleaner than mypy's flag set, so revisit only if that migration turns
   painful.
4. **Contributors' editors run pyright or `ty` and disagree with the gate more
   than about once a month.** Two checkers disagreeing on narrowing is the cost
   this decision avoids; if it arrives from the editor side anyway, converge on
   whatever the editors run.
5. **A mypy pin bump costs more than a session of ignore-comment churn** while
   pyright at the same date passes clean. Measure it on the bump PR rather than
   arguing it.

**What is _not_ sufficient:** pyright finding one or two more things than mypy.
That was already true when this was decided and did not carry the day.

## Consequences

- The consumer tier is checked against a floor nothing else in the repo asserts,
  so a contributor writing modern syntax in an asset gets a confusing-looking
  error naming Python 3.9. That is the check working; the header explains it.
- `--check-untyped-defs` on the test tier would cost ~48 `type: ignore` comments
  on one importlib idiom, so that tier disables `attr-defined` instead. A
  misspelled attribute in a test crashes the test anyway.
- Adding any `.py` file now requires a tier decision. That is deliberate — the
  cost of the alternative was two consumer-executed files going unchecked.

## Deferred

- **Annotating the assets end to end.** The leverage is ~10 lines per file, not
  every parameter; see CONTRIBUTING.md's "Writing Python the checker can follow".
- **A typed loader shim** returning a Protocol, which would let the test tier run
  `--check-untyped-defs` without the ignore comments.
- **Three `linear-*` assets have no dedicated test file**, and are covered only
  by the shared `gql()` seam test.
