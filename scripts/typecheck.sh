#!/usr/bin/env bash
# Static type gate for this repo's Python.
#
# Runs from scripts/check.sh, so CI and `just check` cannot drift. mypy is
# fetched by `uvx` at a pinned version rather than added to mise.toml: uv is
# already installed by both CI (.github/workflows/ci.yml) and the uv-run
# shebangs on scripts/validate.py and scripts/plan-graph.py, and mise has no
# first-party mypy backend, so uvx is the one route that adds no new
# installer. The version is pinned for the same reason mise.toml pins dprint:
# a type checker whose diagnostics move between releases turns a green gate
# red on a day nobody touched the code.
#
# THREE TIERS, deliberately.
#
#   strict   scripts/research-spike.py — the one file already annotated end to
#            end (93/93 return types, 0/187 untyped parameters), so --strict
#            costs nothing to hold and catches the class of defect this repo
#            actually hits: a well-typed value that is None on a path nobody
#            walked.
#
#   default  the other scripts/ entrypoints and the handler assets under
#            commands/handlers/assets/, plus --check-untyped-defs. Tiering here is
#            about annotation coverage, NOT about what ships — the assets are
#            invoked through ${CLAUDE_PLUGIN_ROOT} by the linear handlers and
#            run on consumers' machines just as research-spike.py does. They
#            are simply unannotated (validate.py 7 untyped parameters,
#            task-scan.py 3, plan-graph.py 1, the assets 83 between them and
#            not one return type), and --strict would demand every one of
#            those annotations as the price of any type checking at all.
#            Default settings still catch real defects — every finding on this
#            tier at adoption was genuine — and raising a file to the strict
#            tier is then a small, self-contained follow-up rather than a
#            precondition.
#
#            --check-untyped-defs is what makes this tier mean anything.
#            Without it mypy reads only signatures, and since none of these
#            files annotates one, it read no function BODY at all: the tier
#            covering every consumer-executed file was checking nothing. Turning
#            it on surfaced 14 findings in 6 files, all fixed in the PR that
#            added the flag.
#
#   tests    scripts/test_*.py at default settings WITHOUT
#            --check-untyped-defs. They were previously in no tier at all.
#            The flag is held off deliberately rather than forgotten: these
#            files stub seams on modules loaded through importlib
#            (`rollups.run_gh = fake`), which mypy sees only as ModuleType, so
#            the flag turns 30 fixable errors into 81 — 48 of them
#            attr-defined on that one pattern. Buying coverage with ~48
#            `type: ignore` comments is worse than the gap it closes; a typed
#            loader shim returning a Protocol is the way in, and is not this
#            PR. Default settings still catch the loader idiom itself, which is
#            why every one of these files now asserts its spec resolved.
#
# All three tiers always run, and the exit code is their OR, so one tier
# failing never hides another's findings.
#
# Why not `ty`: evaluated at adoption (dev_docs/research/) and it found the
# same substantive defects with better diagnostics, but it is 0.0.x with an
# explicit "breaking changes, including changes to diagnostics, may occur
# between any two versions" policy, and its exhaustive-match rule
# (astral-sh/ty#1060) — the main reason to want a checker over these enum-ish
# status strings — has not shipped. Good editor checker, wrong CI gate today.
# Revisit at 1.0.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

# uvx installs the tool under ~/.local/share/uv/tools by default, which a
# command sandbox that grants the uv *cache* is not obliged to grant — and
# there the whole gate fails at this stage with "Could not create temporary
# file … Operation not permitted", a red gate for a reason unrelated to the
# code. Pointed at the cache dir instead, so one warm (network-permitting) run
# leaves every later sandboxed `just check` green. Set here rather than in CI
# config so the two cannot drift, which is the same reason this script exists.
export UV_TOOL_DIR="${UV_TOOL_DIR:-${UV_CACHE_DIR:-$HOME/.cache/uv}/tools}"

MYPY_VERSION="1.18.2"
# Stub-only package for the `import yaml` in the default-tier scripts. Without
# it mypy reports `import-untyped` on all three and checks none of their yaml
# usage. Pinned as tightly as mypy itself: stubs are an input to the analysis,
# so a floating stub release can change diagnostics and turn CI red with no
# repository change — the exact drift the pin above exists to stop.
STUBS="types-PyYAML==6.0.12.20260724"

STRICT_FILES=(scripts/research-spike.py)

# The consumer floor. These are executed as bare `python3` on OTHER people's
# machines, so the Python they may use is whatever those machines have — the
# oldest version this project intends to support. That was a claim in prose and
# nothing enforced it, which is exactly the drift this repo writes checks for.
# --python-version 3.9 enforces it: verified to reject `isinstance(v, int | str)`
# (legal 3.9 SYNTAX, TypeError at 3.9 RUNTIME) and `X | Y` annotations without
# `from __future__ import annotations`. Raise this number only by deciding to
# drop support, never to make a diagnostic go away.
ASSET_FILES=(commands/handlers/assets/*.py)
ASSET_PYTHON=3.9

# Dev-only: these run under the contributor's or CI's interpreter, never a
# consumer's, so they are pinned to the floor validate.py already declares in
# its uv script header (requires-python >=3.11). Pinned rather than left to
# default, which would follow whatever interpreter uvx happened to resolve and
# make diagnostics differ between a laptop and CI.
DEV_FILES=(
  scripts/local-review/server.py
  scripts/plan-graph.py
  scripts/task-scan.py
  scripts/validate.py
)
DEV_PYTHON=3.11

# Globbed rather than listed: a new scripts/test_*.py is covered the day it
# lands. Being absent from a hand-maintained list is precisely why none of these
# files was checked at all until recently.
TEST_FILES=(scripts/test_*.py)

rc=0

echo "→ mypy --strict (py${DEV_PYTHON}): ${STRICT_FILES[*]}"
uvx --with "$STUBS" "mypy@${MYPY_VERSION}" --strict \
  --python-version "$DEV_PYTHON" "${STRICT_FILES[@]}" || rc=1

echo "→ mypy --check-untyped-defs (py${ASSET_PYTHON}, consumer floor): ${ASSET_FILES[*]}"
uvx --with "$STUBS" "mypy@${MYPY_VERSION}" --check-untyped-defs \
  --python-version "$ASSET_PYTHON" "${ASSET_FILES[@]}" || rc=1

echo "→ mypy --check-untyped-defs (py${DEV_PYTHON}): ${DEV_FILES[*]}"
uvx --with "$STUBS" "mypy@${MYPY_VERSION}" --check-untyped-defs \
  --python-version "$DEV_PYTHON" "${DEV_FILES[@]}" || rc=1

# attr-defined is disabled here and nowhere else. These files stub seams on
# modules loaded through importlib (`rollups.run_gh = fake`), which mypy sees
# only as ModuleType — 48 findings on that one idiom, versus 3 real ones once it
# is off. A misspelled attribute in a test crashes the test, so the code buys
# nothing here and would cost 48 ignore comments.
echo "→ mypy --check-untyped-defs (py${DEV_PYTHON}, no attr-defined): ${TEST_FILES[*]}"
uvx --with "$STUBS" "mypy@${MYPY_VERSION}" --check-untyped-defs \
  --python-version "$DEV_PYTHON" --disable-error-code=attr-defined \
  "${TEST_FILES[@]}" || rc=1

if [[ "$rc" == 0 ]]; then
  echo "typecheck: OK"
else
  echo "typecheck: FAIL" >&2
fi
exit "$rc"
