#!/usr/bin/env bash
# Live smoke test for skills/co-review/reviewers/assets/crush-readonly.json.
#
# scripts/validate.py's crush-roster check (see its "crush reviewer asset
# drift" section) only proves the PROSE and the ASSET agree with EACH OTHER —
# it never talks to upstream, so it cannot catch upstream drift. At v0.91.0
# the prose, the count, and the asset were all internally consistent at 26
# names while crush's real allToolNames() returned 29: Check A (validate.py)
# would have passed clean on that bug. THIS is the check that would have
# caught it, because it is the only one that re-derives the roster from the
# actual source of truth: the fetched internal/config/config.go at the
# pinned tag. Do not treat a green validate.py as proof the roster is right —
# it only proves the file is internally consistent.
#
# OPT-IN, network-dependent, NOT part of `just check` (scripts/check.sh
# globs scripts/test-*.sh but excludes scripts/test-*-live.sh on purpose —
# see that script's own comment). Skips cleanly with exit 0 when the fetch
# cannot connect (offline, sandboxed). A pin naming a nonexistent tag comes
# back as HTTP 404 and is a FAIL, not a SKIP — see the branch below.
#
# Run directly: bash scripts/test-crush-roster-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CRUSH_MD="$ROOT/skills/co-review/reviewers/crush.md"
CRUSH_ASSET="$ROOT/skills/co-review/reviewers/assets/crush-readonly.json"

# Read the pin out of crush.md rather than hardcoding it, so this test tracks
# whatever reviewers/crush.md says today, not whatever version this script
# was last edited against.
PIN="$(grep -oE 'pinned \*\*`crush version v[0-9]+\.[0-9]+\.[0-9]+`\*\*' "$CRUSH_MD" \
  | grep -oE '[0-9]+\.[0-9]+\.[0-9]+')"
if [ -z "$PIN" ]; then
  echo "test-crush-roster-live: FAIL — could not read the pinned version out of $CRUSH_MD" >&2
  exit 1
fi

URL="https://raw.githubusercontent.com/charmbracelet/crush/v${PIN}/internal/config/config.go"

# Bare `mktemp -d` (no template) ignores $TMPDIR on macOS, so the first arm
# isn't a real $TMPDIR attempt; try $TMPDIR explicitly before falling back to
# repo-local.
TMP="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/crush-roster-live.XXXXXX" 2>/dev/null \
  || mktemp -d "$ROOT/.crush-roster-live.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT
CONFIG_GO="$TMP/config.go"

# A 404 here means the pin names a tag that does not exist upstream, which is
# a real defect in crush.md and must not be laundered into a SKIP. So drop
# curl's -f (which collapses every HTTP error into one exit code) and branch
# on the status: a failed connection is a SKIP, an answered request that is
# not 200 is a FAIL.
HTTP_CODE="$(curl -sSL --max-time 15 -w '%{http_code}' "$URL" -o "$CONFIG_GO" 2>"$TMP/curl.err")"
CURL_RC=$?
if [ "$CURL_RC" -ne 0 ]; then
  echo "test-crush-roster-live: SKIP — could not reach $URL (offline or sandboxed)"
  sed 's/^/  /' "$TMP/curl.err"
  exit 0
fi
if [ "$HTTP_CODE" != "200" ]; then
  echo "test-crush-roster-live: FAIL — $URL returned HTTP $HTTP_CODE." >&2
  if [ "$HTTP_CODE" = "404" ]; then
    echo "  The pin 'v${PIN}' in $CRUSH_MD names no such tag upstream." >&2
  fi
  exit 1
fi

python3 - "$CONFIG_GO" "$CRUSH_ASSET" <<'PY'
import json
import re
import sys

config_path, asset_path = sys.argv[1], sys.argv[2]

text = open(config_path).read()
m = re.search(r"func allToolNames\(\) \[\]string \{(.*?)\n\}", text, re.DOTALL)
if not m:
    print(
        "test-crush-roster-live: FAIL — could not find allToolNames() in the "
        "fetched config.go (upstream shape changed — re-check the extraction "
        "regex in this script against the new source)"
    )
    sys.exit(1)
names = re.findall(r'"([a-zA-Z0-9_]+)"', m.group(1))
if not names:
    print("test-crush-roster-live: FAIL — allToolNames() body yielded no string literals")
    sys.exit(1)
roster = set(names)

asset = json.loads(open(asset_path).read())
disabled = set(asset.get("options", {}).get("disabled_tools", []))

# The dangerous direction: a tool allToolNames() returns that the asset does
# NOT disable ships enabled, silently. The reverse (asset disables a name
# upstream dropped) is over-cautious, not unsafe, so it's reported but never
# fails the check.
missing = sorted(roster - disabled)
if missing:
    print(
        "test-crush-roster-live: FAIL — allToolNames() returns tools the asset "
        "does NOT disable (they ship enabled):"
    )
    for n in missing:
        print(f"  - {n}")
    sys.exit(1)

extra = sorted(disabled - roster)
if extra:
    print(
        "test-crush-roster-live: note — asset disables tools upstream no "
        "longer lists (renamed or removed?):"
    )
    for n in extra:
        print(f"  - {n}")

print(
    f"test-crush-roster-live: OK — {len(roster)} upstream tools, all present "
    "in disabled_tools"
)
PY
