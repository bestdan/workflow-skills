#!/usr/bin/env bash
# land-pr-shadow.sh — the merge lander in shadow only; the logic is land-pr.py.
#
# `land-pr-shadow.sh <pr> --repo <owner/name>` runs `land-pr.py --dry-run
# --if-policy`: it posts the would-land comment, or skips a repo with no merge
# policy, and it never merges. Both flags are appended after the caller's
# arguments, and neither can be switched off from the command line, so no
# argument reaches the landing path. That is what lets /co-review's
# allowed-tools grant this script without granting the power to merge, which
# land-pr.sh carries.
set -euo pipefail

exec python3 "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/land-pr.py" "$@" --dry-run --if-policy
