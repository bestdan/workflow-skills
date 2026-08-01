#!/usr/bin/env bash
# Live smoke test for commands/handlers/assets/linear-relations.py.
#
# Unlike the other scripts/test-*.sh harnesses, this one hits the REAL Linear
# GraphQL API, so it is OPT-IN and only runs when a key is resolvable, per the
# shared secret/pointer/resolver contract in dev_docs/auth_key_access.md: a
# raw $LINEAR_API_KEY, a raw `linear.api_key` (local config only), an op://
# ref in $LINEAR_API_KEY_REF or `linear.api_key_ref` (merged config), resolved
# by whatever `linear.api_key_resolver` names (local config only; `op` by
# default). This harness only ever BRIDGES config values onto the environment
# of the script under test and asks the shared helper whether the result
# resolves — it never resolves a secret itself, with one narrow exception: the
# enum-drift guard below queries the API directly and resolves in-process.
#
# With an APPROVAL-BASED resolver (e.g. opx) that means THREE dialogs per run —
# the probe, the script under test, and the enum guard — since each resolve is
# separately approved and the session is invalidated between them. That is the
# resolver working as designed, not a bug; use `LINEAR_API_KEY=… bash <this>` to
# run it with a single pre-resolved key instead.
# With no key it SKIPS and exits 0 — this keeps `check.sh` green for keyless
# devs and keeps CI keyless *by construction*: a Linear personal API key is a
# full-account bearer token that must never live in CI secrets (see
# commands/handlers/linear-config.md "Archive key" and linear-claim.md's
# security-boundary note). On skip it prints a WARNING (loud outside CI, quiet
# in CI) so a missing key never silently reads as "all green".
#
# It asserts the API CONTRACT, not workspace values (state is mutable): the
# {meta, issues} shape, each issue carrying `description` and the four
# relation-edge lists (`blockedBy`/`blocks`/`relatedTo`/`duplicateOf`) as
# lists, plus the fail-closed non-zero exit on a bad key (the fallback
# trigger `linear-reoptimize.md`'s "Load — build the graph" relies on). It
# would surface drift in the `relations`/`inverseRelations` field names or
# `type` enum values — see this script's SCHEMA NOTE — on the first live run.
#
# Run directly: LINEAR_API_KEY=… bash scripts/test-linear-relations-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/commands/handlers/assets/linear-relations.py"
CONFIG="$ROOT/dev_docs/tasks/.task-config.yml"
LOCAL_CONFIG="$ROOT/dev_docs/tasks/.task-config.local.yml" # gitignored personal override

# Extract a YAML leaf `key: value` from $1, first match, trimmed and unquoted.
# Values may contain internal spaces — 1Password item titles routinely do — so
# this only strips a trailing ` # comment` and a matching pair of surrounding
# quotes; it never truncates at the first space the way a `[^[:space:]]*`
# capture would (that bug used to silently chop `op://Private/PreThink Linear/…`
# down to `op://Private/PreThink`).
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
# Per dev_docs/auth_key_access.md, the secret/pointer and resolver ladders are
# resolved independently and this harness only ever BRIDGES — it never resolves
# a secret itself, and never lets a config value clobber an already-inherited
# env var. `api_key` / `api_key_resolver` are machine-scoped and refused from the
# committed config: found there, that's a WARNING and an ignore, not a failure.
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
    echo "test-linear-relations-live: SKIP — $CATEGORY (expected in CI; keeps CI keyless)"
  elif [ -n "$BRIDGE_SRC" ]; then
    echo "WARNING: test-linear-relations-live DID NOT RUN — $BRIDGE_SRC is set but did not resolve ($CATEGORY)." >&2
    echo "         See dev_docs/auth_key_access.md (Diagnostics) for what '$CATEGORY' means and how to fix it." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
  else
    echo "WARNING: test-linear-relations-live DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config ($CATEGORY)." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
    echo "         Export a key or set linear.api_key_ref to enable it." >&2
  fi
  exit 0
fi

# --- resolve the team ($LINEAR_TEAM, else linear.team from the config) --------
TEAM="${LINEAR_TEAM:-}"
if [ -z "$TEAM" ] && [ -f "$CONFIG" ]; then
  TEAM="$(sed -n 's/^[[:space:]]*team:[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$CONFIG" | head -1)"
  # Strip surrounding quotes so a quoted `team: "ENG"` / `team: 'ENG'` resolves
  # the same as unquoted (the sed capture keeps the quote chars).
  TEAM="${TEAM%\"}"
  TEAM="${TEAM#\"}"
  TEAM="${TEAM%\'}"
  TEAM="${TEAM#\'}"
fi
if [ -z "$TEAM" ]; then
  echo "WARNING: test-linear-relations-live has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

HAPPY_OUT="$(mktemp)"
HAPPY_ERR="$(mktemp)"
BAD_OUT="$(mktemp)"
BAD_ERR="$(mktemp)"
ENUM_OUT="$(mktemp)"
trap 'rm -f "$HAPPY_OUT" "$HAPPY_ERR" "$BAD_OUT" "$BAD_ERR" "$ENUM_OUT"' EXIT

# Happy path: the real inherited key.
python3 "$SCRIPT" --team "$TEAM" --limit 50 >"$HAPPY_OUT" 2>"$HAPPY_ERR"
HAPPY_RC=$?

# Enum-drift guard: introspect the IssueRelationType enum and confirm every value
# is one derive_edges() actually handles ({blocks, related, duplicate, similar}).
# Without
# this, a renamed/added enum value is silently dropped to empty edge lists and
# slips past the list-shape checks below — the exact drift the header claims to
# surface. Field-NAME drift already fails via the happy-path rc!=0 (GraphQL 400);
# this closes the enum-VALUE gap.
# Resolved in-process via the shared helper rather than with `curl -H
# "Authorization: $LINEAR_API_KEY"`: this harness BRIDGES a ref/resolver and
# never holds the key itself (so the old form died under `set -u` once the
# bridge landed), and a key passed as a curl argument is visible in `ps`.
python3 - "$ENUM_OUT" "$ROOT/commands/handlers/assets" <<'PY' 2>/dev/null || true
import json, sys, urllib.request
sys.path.insert(0, sys.argv[2])  # absolute: this harness is runnable from any cwd
from _secret_resolve import resolve_key

req = urllib.request.Request(
    "https://api.linear.app/graphql",
    data=json.dumps(
        {"query": 'query{__type(name:"IssueRelationType"){enumValues{name}}}'}
    ).encode(),
    headers={"Authorization": resolve_key("LINEAR_API_KEY"), "Content-Type": "application/json"},
)
with open(sys.argv[1], "wb") as fh:
    fh.write(urllib.request.urlopen(req, timeout=15).read())
PY

# Bad-key path: a bogus key with the op:// ref/resolver unset so it can't fall
# back and accidentally succeed. Exercises the fail-closed exit
# `linear-reoptimize.md` falls back on.
env -u LINEAR_API_KEY_REF -u LINEAR_API_KEY_RESOLVER LINEAR_API_KEY="lin_api_BOGUS_000000000000000000000000" \
  python3 "$SCRIPT" --team "$TEAM" --limit 50 >"$BAD_OUT" 2>"$BAD_ERR"
BAD_RC=$?

python3 - "$HAPPY_OUT" "$HAPPY_ERR" "$HAPPY_RC" "$BAD_OUT" "$BAD_ERR" "$BAD_RC" "$ENUM_OUT" <<'PY'
import json, sys
happy_out, happy_err, happy_rc, bad_out, bad_err, bad_rc, enum_out = sys.argv[1:8]
happy_rc, bad_rc = int(happy_rc), int(bad_rc)
fails = 0
def ok(m):  print("ok   - " + m)
def bad(m, x=""):
    global fails; fails += 1
    print("FAIL - " + m + (("  " + x) if x else ""))

# --- happy path ---------------------------------------------------------------
if happy_rc == 0:
    ok("happy path: script exits 0")
else:
    bad("happy path: script exits 0", "rc=%d %s" % (happy_rc, open(happy_err).read()[:200]))

d = None
if happy_rc == 0:
    try:
        parsed = json.load(open(happy_out))
    except Exception as e:
        bad("happy path: stdout is valid JSON", str(e))
    else:
        ok("happy path: stdout is valid JSON")
        if isinstance(parsed, dict) and set(parsed) == {"meta", "issues"}:
            ok("top-level object is exactly {meta, issues}")
            d = parsed
        else:
            bad("top-level object is exactly {meta, issues}",
                "got %s%s" % (type(parsed).__name__, (" keys=" + str(sorted(parsed))) if isinstance(parsed, dict) else ""))

if d is not None:
    m = d.get("meta", {})
    ok("meta.viewer.id present") if m.get("viewer", {}).get("id") else bad("meta.viewer.id present")
    ok("meta.team.id present") if m.get("team", {}).get("id") else bad("meta.team.id present")

    EXP = {"id", "identifier", "title", "description", "state", "priority",
           "estimate", "updatedAt", "project", "labels", "blockedBy", "blocks",
           "relatedTo", "duplicateOf"}
    issues = d.get("issues", [])
    if not isinstance(issues, list):
        bad("issues is a list", "got %s" % type(issues).__name__); issues = []
    non_obj = [i for i in issues if not isinstance(i, dict)]
    if non_obj:
        bad("every issue is a JSON object", "%d non-object element(s)" % len(non_obj))
        issues = [i for i in issues if isinstance(i, dict)]
    wrong = [i.get("identifier") for i in issues if set(i) != EXP]
    ok("every issue has the exact field set") if not wrong else bad("every issue has the exact field set", str(wrong[:5]))
    ok("`description` key present on every issue") if all("description" in i for i in issues) else bad("`description` key present on every issue")

    EDGE_FIELDS = ("blockedBy", "blocks", "relatedTo", "duplicateOf")
    bad_edges = [i.get("identifier") for i in issues if any(not isinstance(i.get(f), list) for f in EDGE_FIELDS)]
    ok("blockedBy/blocks/relatedTo/duplicateOf are all lists") if not bad_edges else bad("blockedBy/blocks/relatedTo/duplicateOf are all lists", str(bad_edges[:5]))
else:
    issues = []

if not issues and d is not None:
    print("note - happy-path scope returned 0 issues; edge-shape assertions above still ran on an empty set")

# --- enum-drift guard ---------------------------------------------------------
# derive_edges() in linear-relations.py only recognizes these IssueRelationType
# values; anything else it silently drops. Fail loud if the live schema carries a
# value we don't handle (renamed/added enum), which is exactly the drift the
# script header claims this test surfaces.
KNOWN_TYPES = {"blocks", "related", "duplicate", "similar"}
try:
    enum_vals = {e["name"] for e in json.load(open(enum_out))["data"]["__type"]["enumValues"]}
except Exception as e:
    bad("IssueRelationType enum introspected", str(e))
else:
    unknown = enum_vals - KNOWN_TYPES
    if unknown:
        bad("IssueRelationType enum fully handled by derive_edges",
            "unrecognized value(s) silently dropped: %s" % sorted(unknown))
    else:
        ok("IssueRelationType enum fully handled by derive_edges (%s)" % sorted(enum_vals))

# --- bad-key fallback contract ------------------------------------------------
ok("bad key: exits non-zero") if bad_rc != 0 else bad("bad key: exits non-zero", "rc=0")
ok("bad key: stdout is empty") if not open(bad_out).read().strip() else bad("bad key: stdout is empty")
ok("bad key: error on stderr") if open(bad_err).read().strip() else bad("bad key: error on stderr")

sys.exit(1 if fails else 0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "test-linear-relations-live: OK (team=$TEAM)"
else
  echo "test-linear-relations-live: FAIL" >&2
fi
exit "$rc"
