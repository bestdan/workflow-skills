#!/usr/bin/env bash
# agent-guidance-dir.sh — print the root of the installed agent-guidance
# plugin, so a skill can read that plugin's files without hardcoding a path.
#
# co-review's reviewers are cut off from repo context by design, so the only
# way to hand them the review conventions is to put the text into the
# assembled <INPUT>. That text lives in the agent-guidance plugin, which is
# installed separately from this one and can sit in several places at once:
# the version-pinned install under ~/.claude/plugins/cache/, the marketplace
# clone under ~/.claude/plugins/marketplaces/, and on its author's machines a
# working checkout that is meant to run ahead of both.
#
# Usage:
#   scripts/agent-guidance-dir.sh
#
# Prints exactly one line, the plugin root, and nothing else on stdout. The
# root is the directory holding .claude-plugin/plugin.json and portable.md.
#
# Resolution order — what the caller meant first, then the copy Claude Code
# actually loads, then progressively weaker guesses at it:
#   1. $AGENT_GUIDANCE_DIR, when set. Set-but-wrong is an error, never a
#      reason to keep looking.
#   2. installPath from ~/.claude/plugins/installed_plugins.json.
#   3. The newest directory under ~/.claude/plugins/cache/agent-guidance/.
#   4. The marketplace clone, ~/.claude/plugins/marketplaces/agent-guidance.
#
# Exit status:
#   0  resolved; the root is on stdout
#   1  AGENT_GUIDANCE_DIR is set but does not name a plugin root — a
#      misconfiguration, so it is loud (stderr) and must not be papered over
#   2  usage error
#   3  the plugin is not installed anywhere this script knows to look. This is
#      a normal outcome, not a failure: stdout stays empty, stderr lists what
#      was tried, and a caller that can work without the plugin should branch
#      on this code and carry on
#
# The dotfiles repo carries a resolver of the same name for its own callers;
# this one is self-contained so the plugin works on a machine without it.
set -uo pipefail

case "${1:-}" in
  "") ;;
  -h | --help)
    sed -n '2,38p' "$0"
    exit 0
    ;;
  *)
    echo "agent-guidance-dir: unknown argument: $1" >&2
    exit 2
    ;;
esac

# A plugin root, not merely a directory with a markdown file in it. portable.md
# is the file every copy of the plugin ships and the one its consumers read,
# so its presence is the marker; the convention files co-review wants are
# newer than some installed copies and would reject a real install.
is_plugin_root() { # dir
  [ -n "$1" ] && [ -f "$1/.claude-plugin/plugin.json" ] && [ -f "$1/portable.md" ]
}

tried=""
note() { tried="$tried  - $1"$'\n'; }

# 1. An explicit override: the escape hatch for a `--plugin-dir` install, a
#    working checkout, and a headless host whose layout matches nothing below.
#    Someone who exported this variable meant it, and silently resolving
#    elsewhere would hand them a different plugin while looking like success.
if [ -n "${AGENT_GUIDANCE_DIR:-}" ]; then
  if is_plugin_root "$AGENT_GUIDANCE_DIR"; then
    printf '%s\n' "$AGENT_GUIDANCE_DIR"
    exit 0
  fi
  printf 'agent-guidance-dir: AGENT_GUIDANCE_DIR is set but is not a plugin root:\n' >&2
  printf '  %s\n' "$AGENT_GUIDANCE_DIR" >&2
  printf 'Unset it to fall back to the installed copy, or point it at a\n' >&2
  printf 'directory containing .claude-plugin/plugin.json and portable.md.\n' >&2
  exit 1
fi

# 2. The copy Claude Code actually loads, named by its own install manifest.
#    Only installPath identifies that directory by construction; every other
#    candidate is a guess that happens to be right today. The manifest is an
#    undocumented file whose format can move, so every way of failing to read
#    it — missing, unparseable, no agent-guidance entry, a path that no longer
#    exists — falls through silently to the candidates below.
installed_root() {
  local manifest="$HOME/.claude/plugins/installed_plugins.json"
  [ -f "$manifest" ] || return 1
  python3 - "$manifest" <<'PY' 2>/dev/null
import json
import sys

try:
    data = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)

# Keys look like "agent-guidance@agent-guidance"; the marketplace half varies
# by how the plugin was installed, so match on the plugin name alone. A name
# can carry several records, one per install scope, and the file lists project
# scopes before the user scope — so a stale project install whose directory
# still exists would win a first-record pick. Print every candidate, user scope
# first, and let the caller take the first that is a real plugin root.
candidates = []
for name, records in (data.get("plugins") or {}).items():
    if name.split("@", 1)[0] != "agent-guidance":
        continue
    if isinstance(records, dict):
        records = [records]
    for record in records or []:
        path = (record or {}).get("installPath")
        if path:
            candidates.append(((record or {}).get("scope") != "user", path))
for _, path in sorted(candidates, key=lambda c: c[0]):
    print(path)
sys.exit(0 if candidates else 1)
PY
}

while IFS= read -r candidate; do
  if is_plugin_root "$candidate"; then
    printf '%s\n' "$candidate"
    exit 0
  fi
done <<EOF_MANIFEST
$(installed_root)
EOF_MANIFEST
note "$HOME/.claude/plugins/installed_plugins.json -> installPath (the copy Claude Code loads)"

# 3. The installed copy found by scanning, for when the manifest could not be
#    read. Installs land under cache/, one directory per resolved version. The
#    plugin declares no version, so those names are whatever Claude Code
#    resolved — commit prefixes today — and `sort -V` is only a guess at
#    newest. Two things sharpen it: a directory Claude Code has stamped with
#    .orphaned_at is one no manifest record references any more (observed on
#    a live cache, 2026-09-12, beside the entry the manifest did name), so it
#    is skipped; and a directory that is not a plugin root — a half-removed
#    upgrade leaves exactly that — is skipped rather than chosen for sorting
#    highest.
cache="$HOME/.claude/plugins/cache/agent-guidance/agent-guidance"
if [ -d "$cache" ]; then
  newest=""
  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    candidate="${candidate%/}"
    [ -e "$candidate/.orphaned_at" ] && continue
    is_plugin_root "$candidate" && newest="$candidate"
  done <<EOF_CACHE
$(ls -d "$cache"/*/ 2>/dev/null | sort -V)
EOF_CACHE
  if [ -n "$newest" ]; then
    printf '%s\n' "$newest"
    exit 0
  fi
fi
note "$cache/<version> (installed copy)"

# 4. The marketplace clone, last. Version-agnostic and kept current by
#    `claude plugin update`, but it is the source an install is built FROM,
#    not the install — so it routinely sits ahead of what Claude Code loads.
#    Reading conventions Claude was never given is the drift the tiers above
#    exist to avoid, so the clone is the fallback of last resort.
marketplace="$HOME/.claude/plugins/marketplaces/agent-guidance"
if is_plugin_root "$marketplace"; then
  printf '%s\n' "$marketplace"
  exit 0
fi
note "$marketplace (marketplace clone)"

printf 'agent-guidance-dir: the agent-guidance plugin is not installed. Tried:\n' >&2
printf '%s' "$tried" >&2
printf 'Install it with:\n' >&2
printf '  claude plugin marketplace add bestdan/agent-guidance\n' >&2
printf '  claude plugin install agent-guidance@agent-guidance\n' >&2
printf 'or export AGENT_GUIDANCE_DIR to an existing checkout.\n' >&2
exit 3
