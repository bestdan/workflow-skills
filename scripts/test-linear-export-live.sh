#!/usr/bin/env bash
# Live smoke test for commands/handlers/assets/linear-export.py.
#
# Hits the REAL Linear GraphQL API, so it is OPT-IN and only runs when a key is
# resolvable, per the shared secret/pointer/resolver contract in
# dev_docs/auth_key_access.md. With no key it SKIPS and exits 0, which keeps
# `check.sh` green for keyless devs and keeps CI keyless *by construction*: a
# Linear personal API key is a full-account bearer token that must never live
# in CI secrets. On skip it prints a WARNING (loud outside CI, quiet in CI) so a
# missing key never silently reads as "all green".
#
# `scripts/check.sh` excludes `scripts/test-*-live.sh` outright, so THIS TEST
# NEVER RUNS IN THE GATE. A green `just check` is not evidence the export
# works; only a real run of this script is.
#
# With an APPROVAL-BASED resolver (e.g. opx) each resolve is separately approved
# and the session is invalidated between them, so this raises two dialogs — the
# probe and the script under test. Use `LINEAR_API_KEY=… bash <this>` to run it
# with a single pre-resolved key instead.
#
# It exports to a TEMP directory, never to the real `$HOME/src/linear-export/`:
# a test must not be able to overwrite the provenance record it is testing.
#
# What it asserts is the API CONTRACT plus one floor on scale: the {exported_at,
# team, issue_count, issues} document shape, that archived issues are actually
# present (the half that cannot be recovered any other way), that relations and
# comments arrive at all — which is where drift in the `relations` /
# `inverseRelations` field names would surface, per the script's SCHEMA NOTE —
# and that the count is above 700. The floor is deliberately a floor and not an
# equality: the workspace was at 781 on 2026-08-24 and ≥835 by 2026-09-12, so
# any literal would be wrong by the time it was written.
#
# Run directly: LINEAR_API_KEY=… bash scripts/test-linear-export-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/commands/handlers/assets/linear-export.py"
CONFIG="$ROOT/dev_docs/tasks/.task-config.yml"
LOCAL_CONFIG="$ROOT/dev_docs/tasks/.task-config.local.yml" # gitignored personal override

# Extract a YAML leaf `key: value` from $1, first match, trimmed and unquoted.
# Values may contain internal spaces — 1Password item titles routinely do — so
# this only strips a trailing ` # comment` and a matching pair of surrounding
# quotes; it never truncates at the first space.
yaml_leaf() {
  local file="$1" key="$2" val
  [ -f "$file" ] || return 1
  val="$(sed -n "s/^[[:space:]]*${key}:[[:space:]]*//p" "$file" | head -1)"
  [ -z "$val" ] && return 1
  val="$(printf '%s' "$val" | sed -E 's/[[:space:]]+#.*$//; s/[[:space:]]+$//')"
  case "$val" in
    \"*\") val="${val#\"}" val="${val%\"}" ;;
    \'*\') val="${val#\'}" val="${val%\'}" ;;
  esac
  [ -z "$val" ] && return 1
  printf '%s' "$val"
}

# --- bridge config onto the environment (opt-in) -------------------------------
# Per dev_docs/auth_key_access.md this harness only ever BRIDGES — it never
# resolves a secret itself, and never lets a config value clobber an
# already-inherited env var. `api_key` / `api_key_resolver` are machine-scoped
# and refused from the committed config: found there, that's a WARNING and an
# ignore, not a failure.
BRIDGE_SRC=""

# Rung 0: a raw key, local config only.
if [ -z "${LINEAR_API_KEY:-}" ] && RAW="$(yaml_leaf "$LOCAL_CONFIG" api_key)"; then
  export LINEAR_API_KEY="$RAW"
  BRIDGE_SRC="linear.api_key (.task-config.local.yml)"
fi
if yaml_leaf "$CONFIG" api_key >/dev/null 2>&1; then
  echo "WARNING: linear.api_key is set in the COMMITTED .task-config.yml — refused per" >&2
  echo "         dev_docs/auth_key_access.md (Provenance); ignoring it. Move it to" >&2
  echo "         .task-config.local.yml and rotate the key." >&2
fi

# Rung 2/3: a pointer — env first, else the merged config (local override, then
# the committed file).
if [ -n "${LINEAR_API_KEY_REF:-}" ]; then
  BRIDGE_SRC="\$LINEAR_API_KEY_REF"
elif [ -z "${LINEAR_API_KEY:-}" ]; then
  for cfg in "$LOCAL_CONFIG" "$CONFIG"; do
    if REF="$(yaml_leaf "$cfg" api_key_ref)"; then
      export LINEAR_API_KEY_REF="$REF"
      BRIDGE_SRC="linear.api_key_ref (${cfg##*/})"
      break
    fi
  done
fi

# Resolver: env first, else local config only.
if [ -z "${LINEAR_API_KEY_RESOLVER:-}" ] && RESOLVER="$(yaml_leaf "$LOCAL_CONFIG" api_key_resolver)"; then
  export LINEAR_API_KEY_RESOLVER="$RESOLVER"
fi
if yaml_leaf "$CONFIG" api_key_resolver >/dev/null 2>&1; then
  echo "WARNING: linear.api_key_resolver is set in the COMMITTED .task-config.yml —" >&2
  echo "         refused per dev_docs/auth_key_access.md (Provenance); ignoring it." >&2
  echo "         Move it to .task-config.local.yml." >&2
fi

# Ask the shared helper, which honors whatever resolver is configured (op, opx,
# ...) instead of calling `op read` directly — so a non-default resolver doesn't
# read as a false SKIP. It never prints the secret; only a reason category.
CATEGORY="$(python3 "$ROOT/commands/handlers/assets/_secret_resolve.py" --probe LINEAR_API_KEY 2>&1 >/dev/null)"
PROBE_RC=$?

if [ "$PROBE_RC" -ne 0 ]; then
  if [ -n "${CI:-}" ]; then
    echo "test-linear-export-live: SKIP — $CATEGORY (expected in CI; keeps CI keyless)"
  elif [ -n "$BRIDGE_SRC" ]; then
    echo "WARNING: test-linear-export-live DID NOT RUN — $BRIDGE_SRC is set but did not resolve ($CATEGORY)." >&2
    echo "         See dev_docs/auth_key_access.md (Diagnostics) for what '$CATEGORY' means and how to fix it." >&2
    echo "         NOTHING WAS EXPORTED on this run." >&2
  else
    echo "WARNING: test-linear-export-live DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config ($CATEGORY)." >&2
    echo "         NOTHING WAS EXPORTED on this run." >&2
    echo "         Export a key or set linear.api_key_ref to enable it." >&2
  fi
  exit 0
fi

# --- resolve the team ($LINEAR_TEAM, else linear.team from the config) --------
TEAM="${LINEAR_TEAM:-}"
if [ -z "$TEAM" ] && [ -f "$CONFIG" ]; then
  TEAM="$(yaml_leaf "$CONFIG" team)" || TEAM=""
fi
if [ -z "$TEAM" ]; then
  echo "WARNING: test-linear-export-live has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

OUT_DIR="$(mktemp -d)"
SUMMARY="$(mktemp)"
ERR="$(mktemp)"
trap 'rm -rf "$OUT_DIR" "$SUMMARY" "$ERR"' EXIT

python3 "$SCRIPT" --team "$TEAM" --out "$OUT_DIR" --json >"$SUMMARY" 2>"$ERR"
RC=$?

python3 - "$SUMMARY" "$ERR" "$RC" <<'PY'
import json, sys
summary_path, err_path, rc = sys.argv[1], sys.argv[2], int(sys.argv[3])
fails = 0
def ok(m):  print("ok   - " + m)
def bad(m, x=""):
    global fails; fails += 1
    print("FAIL - " + m + (("  " + x) if x else ""))

if rc == 0:
    ok("export exits 0")
else:
    bad("export exits 0", "rc=%d %s" % (rc, open(err_path).read()[:300]))
    sys.exit(1)

try:
    info = json.load(open(summary_path))
except Exception as e:
    bad("--json summary is valid JSON", str(e)); sys.exit(1)
ok("--json summary is valid JSON")

try:
    doc = json.load(open(info["path"]))
except Exception as e:
    bad("export file is readable JSON", str(e)); sys.exit(1)
ok("export file is readable JSON")

if sorted(doc) == ["exported_at", "issue_count", "issues", "team"]:
    ok("document is exactly {exported_at, team, issue_count, issues}")
else:
    bad("document is exactly {exported_at, team, issue_count, issues}", str(sorted(doc)))

issues = doc.get("issues", [])
if doc.get("issue_count") == len(issues):
    ok("issue_count matches the list length (%d)" % len(issues))
else:
    bad("issue_count matches the list length",
        "count=%r len=%d" % (doc.get("issue_count"), len(issues)))

# A floor, not an equality — the workspace grows. 781 on 2026-08-24, >=835 on
# 2026-09-12; anything under 700 means the pagination stopped early.
if len(issues) > 700:
    ok("more than 700 issues exported (%d)" % len(issues))
else:
    bad("more than 700 issues exported", "got %d — pagination likely stopped early" % len(issues))

archived = [i for i in issues if i.get("archivedAt")]
ok("archived issues are present (%d)" % len(archived)) if archived else \
    bad("archived issues are present", "none — includeArchived is not taking effect")

# Field-NAME drift in relations/inverseRelations would fail the run outright
# (GraphQL 400), so what is left to catch is a shape that parses but carries
# nothing: every issue in a workspace this size cannot have zero of everything.
if any(i.get("relations") or i.get("inverseRelations") for i in issues):
    ok("relation edges arrived")
else:
    bad("relation edges arrived", "every issue has empty relations — check the SCHEMA NOTE")
if any(i.get("comments") for i in issues):
    ok("comment bodies arrived")
else:
    bad("comment bodies arrived", "every issue has zero comments")

missing = [i.get("identifier") for i in issues if "description" not in i or "url" not in i]
ok("every issue carries description and url keys") if not missing else \
    bad("every issue carries description and url keys", str(missing[:5]))

sys.exit(1 if fails else 0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "test-linear-export-live: OK (team=$TEAM)"
else
  echo "test-linear-export-live: FAIL" >&2
fi
exit "$rc"
