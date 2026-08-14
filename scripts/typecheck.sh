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
# TWO TIERS, deliberately.
#
#   strict   scripts/research-spike.py — the one file already annotated end to
#            end (93/93 return types, 0/187 untyped parameters), so --strict
#            costs nothing to hold and catches the class of defect this repo
#            actually hits: a well-typed value that is None on a path nobody
#            walked. It is also the script that ships to consumers through
#            ${CLAUDE_PLUGIN_ROOT} and runs on machines nobody here controls.
#
#   default  the other three. They carry unannotated parameters
#            (validate.py 7, task-scan.py 3, plan-graph.py 1), and --strict
#            would demand those annotations as the price of any type checking
#            at all. Default settings still catch real defects — every finding
#            on this tier at adoption was a genuine one — and raising a file to
#            the strict tier is then a small, self-contained follow-up rather
#            than a precondition.
#
# Both tiers always run, and the exit code is the OR of the two, so one
# tier failing never hides the other's findings.
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

MYPY_VERSION="1.18.2"
# Stub-only package for the `import yaml` in the default-tier scripts. Without
# it mypy reports `import-untyped` on all three and checks none of their yaml
# usage.
STUBS="types-PyYAML"

STRICT_FILES=(scripts/research-spike.py)
DEFAULT_FILES=(scripts/plan-graph.py scripts/task-scan.py scripts/validate.py)

rc=0

echo "→ mypy --strict: ${STRICT_FILES[*]}"
uvx --with "$STUBS" "mypy@${MYPY_VERSION}" --strict "${STRICT_FILES[@]}" || rc=1

echo "→ mypy: ${DEFAULT_FILES[*]}"
uvx --with "$STUBS" "mypy@${MYPY_VERSION}" "${DEFAULT_FILES[@]}" || rc=1

if [[ "$rc" == 0 ]]; then
  echo "typecheck: OK"
else
  echo "typecheck: FAIL" >&2
fi
exit "$rc"
