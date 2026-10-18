#!/usr/bin/env bash
# test-secret-resolve.sh — hermetic tests for _secret_resolve.py's key resolution.
#
# Wraps scripts/test_secret_resolve.py (stdlib unittest, no network: the
# resolver binary is stubbed via PATH) so the secret/pointer and resolver
# ladders, the allow-list, the reference grammar, and redaction are exercised
# by the same `run scripts/test-*.sh` gate as the other hermetic tests. Never
# invokes a real `op` or `opx`.
#
# Run directly: bash scripts/test-secret-resolve.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$ROOT/scripts/test_secret_resolve.py"
