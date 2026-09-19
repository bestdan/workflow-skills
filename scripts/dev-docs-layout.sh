#!/usr/bin/env bash
# dev-docs-layout.sh — run the agent-guidance plugin's dev_docs layout checker
# over this repo.
#
# The layout convention (dev_docs_layout.md at the plugin root) is delivered by
# the plugin, and so is the checker that enforces it. Each consumer wires it
# into the check suite it already has; this wrapper is that wiring.
#
# Usage:
#   scripts/dev-docs-layout.sh
#
# Exit status:
#   0  the layout is clean
#   1  violations, listed on stdout; or the plugin root could not be resolved,
#      whether because AGENT_GUIDANCE_DIR names something that is not one or
#      because no copy of the plugin is installed
#   2  the installed plugin is too old to ship the checker
#
# A missing plugin FAILS rather than skips. A check that skips is green while
# checking nothing, which is the failure mode this wrapper exists to avoid. CI
# without a plugin install clones bestdan/agent-guidance and exports
# AGENT_GUIDANCE_DIR (see .github/workflows/ci.yml).
set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

# Resolve through agent-guidance-dir.sh rather than reading AGENT_GUIDANCE_DIR
# here: the resolver honours that variable as its own first step AND validates
# it, so a stale or mistyped value is reported as what it is. Reading it
# directly skipped that check and blamed the plugin's version instead.
root="$("$here/agent-guidance-dir.sh")"
rc=$?
case "$rc" in
  0) ;;
  # Set-but-wrong is a misconfiguration the resolver refuses to paper over.
  1)
    printf 'dev-docs-layout: AGENT_GUIDANCE_DIR does not name a plugin root\n' >&2
    exit 1
    ;;
  # Exit 3 means "not installed", which the resolver treats as a normal outcome
  # for a caller that can work without the plugin. This one cannot.
  *)
    printf 'dev-docs-layout: the agent-guidance plugin is required for this check\n' >&2
    exit 1
    ;;
esac

checker="$root/scripts/dev-docs-layout.py"
if [ ! -f "$checker" ]; then
  printf 'dev-docs-layout: %s has no scripts/dev-docs-layout.py — the installed agent-guidance predates it\n' \
    "$root" >&2
  exit 2
fi

exec python3 "$checker" "$repo"
