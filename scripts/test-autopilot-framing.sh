#!/usr/bin/env bash
# test-autopilot-framing.sh — the auto-pilot surface describes an UNATTENDED
# run, never a nocturnal one (#767).
#
# The property every auto-pilot guarantee serves is that no human is attached
# to the process, which holds at 10am as much as at night. Time-of-day framing
# produced real design errors, so it is guarded rather than just edited out.
#
# skills/auto-pilot/ is scanned as a DIRECTORY, not a file list: a reference
# file added later is covered without anyone remembering to register it.
#
# Run directly: bash scripts/test-autopilot-framing.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

pattern='overnight|tonight|3am|goes to bed|user wakes to|whole night'

if hits="$(grep -rniwE "$pattern" \
  skills/auto-pilot/ \
  commands/auto-pilot.md \
  dev_docs/auto-pilot.md \
  dev_docs/auto-pilot-hardening.md)"; then
  echo "FAIL: time-of-day framing in the auto-pilot surface — say what the" >&2
  echo "      run needs with no human attached instead (#767):" >&2
  echo "$hits" >&2
  exit 1
fi

echo "ok: auto-pilot surface carries no time-of-day framing"
