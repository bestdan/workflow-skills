#!/usr/bin/env bash
# land-pr.sh — the merge lander's entry point; the logic is land-pr.py.
#
# Phase 0 (shadow): `land-pr.sh <pr> --repo <owner/name> --dry-run` computes
# whether the PR would land and posts the verdict as one PR comment. It never
# merges, and without --dry-run it exits 2. See land-pr.py for every check.
set -euo pipefail

exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/land-pr.py" "$@"
