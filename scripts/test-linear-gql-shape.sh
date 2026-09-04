#!/usr/bin/env bash
# test-linear-gql-shape.sh — the gql() unwrap seam across every linear asset.
#
# Wraps scripts/test_linear_gql_shape.py (stdlib unittest; urlopen is stubbed,
# so no network and no API key). This is the only coverage three of the five
# linear assets have — linear-false-closures, linear-relations and linear-scan
# have no test file of their own.
#
# Run directly: bash scripts/test-linear-gql-shape.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_linear_gql_shape.py"
