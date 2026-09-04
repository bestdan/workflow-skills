#!/usr/bin/env bash
# Lint gate for this repo's Python — the complement to scripts/typecheck.sh.
#
# mypy answers "do the types line up"; it says nothing about an unused import, a
# name that only exists on a path nobody walked, or an `except` that swallows
# what it meant to raise. Nothing here covered that class at all until this
# script landed: `rg ruff scripts/ justfile` returned only dprint's ruff plugin,
# which is the FORMATTER. `ruff check` ran nowhere.
#
# Pinned and fetched by uvx for the same reasons typecheck.sh pins mypy — see
# that script's header for the uvx-over-mise rationale and the UV_TOOL_DIR
# sandbox note, both of which apply verbatim here.
#
# RULE SELECTION: ruff's defaults (E4, E7, E9, F) and nothing else.
#
# That is a deliberate floor, not a placeholder. The whole repo was already
# clean under it when this landed, so the gate started green and every future
# finding is a real regression rather than a backlog someone has to burn down.
# Two families were measured and left off:
#
#   E501 (line-too-long)  173 of the 184 findings in a broader run, almost all
#                         in comments and docstrings that ruff-format leaves
#                         alone by design. Gating on it would mean reflowing
#                         prose to satisfy a linter.
#
#   B / SIM / C4          11 findings, all minor. Worth reading once; not worth
#                         blocking a merge. One argues against the family
#                         outright: SIM102 wants the nested `if has_next:` in
#                         gh-issue-rollups.py collapsed, but the comment naming
#                         the defect that guard closes sits between the two
#                         conditions, and collapsing them buries it.
#
# Widening the selection is a deliberate edit here, with the backlog it creates
# cleared in the same PR.
#
# Run directly: bash scripts/lint-python.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

RUFF_VERSION="0.14.2"

# See typecheck.sh: uvx's default tool dir is not necessarily writable under a
# command sandbox that grants only the uv cache.
export UV_TOOL_DIR="${UV_TOOL_DIR:-${UV_CACHE_DIR:-$HOME/.cache/uv}/tools}"

# Both trees that hold Python. scripts/local-review/ is excluded from dprint
# (see dprint.json) but not from linting — that exclusion is about formatting a
# vendored-looking tree, not about leaving its code unchecked.
echo "→ ruff check: scripts/ commands/handlers/assets/"
if uvx "ruff@${RUFF_VERSION}" check scripts/ commands/handlers/assets/; then
  echo "lint-python: OK"
else
  echo "lint-python: FAIL" >&2
  exit 1
fi
