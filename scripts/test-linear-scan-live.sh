#!/usr/bin/env bash
# Live smoke test for commands/handlers/assets/linear-scan.py.
#
# Unlike the other scripts/test-*.sh harnesses, this one hits the REAL Linear
# GraphQL API, so it is OPT-IN and only runs when a key is resolvable, per the
# shared secret/pointer/resolver contract in dev_docs/auth_key_access.md: a
# raw $LINEAR_API_KEY, a raw `linear.api_key` (local config only), an op://
# ref in $LINEAR_API_KEY_REF or `linear.api_key_ref` (merged config), resolved
# by whatever `linear.api_key_resolver` names (local config only; `op` by
# default). This harness only ever BRIDGES config values onto the environment
# of the script under test and asks the shared helper whether the result
# resolves — it never resolves a secret itself.
# With no key it SKIPS and exits 0 — this keeps `check.sh` green for keyless
# devs and keeps CI keyless *by construction*: a Linear personal API key is a
# full-account bearer token that must never live in CI secrets (see
# commands/handlers/linear-config.md "Archive key" and linear-claim.md's
# security-boundary note). On skip it prints a WARNING (loud outside CI, quiet
# in CI) so a missing key never silently reads as "all green".
#
# It asserts the API CONTRACT, not workspace values (state is mutable): the
# {meta, issues} shape, that every issue carries an `attachments` url list and
# explicitly no `description`, and the fail-closed non-zero exit on a bad key
# (the fallback trigger `/sweep-for-complete` and `/reconcile-tasks` rely on).
#
# Run directly: LINEAR_API_KEY=… bash scripts/test-linear-scan-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/commands/handlers/assets/linear-scan.py"
CONFIG="$ROOT/dev_docs/tasks/.task-config.yml"
LOCAL_CONFIG="$ROOT/dev_docs/tasks/.task-config.local.yml" # gitignored personal override

# Extract a YAML leaf `key: value` from $1, first match, trimmed and unquoted.
# Values may contain internal spaces — 1Password item titles routinely do — so
# this only strips a trailing ` # comment` and a matching pair of surrounding
# quotes; it never truncates at the first space the way a `[^[:space:]]*`
# capture would (that bug used to silently chop `op://TestVault/Item With Spaces/…`
# down to `op://TestVault/Item`).
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
    echo "test-linear-scan-live: SKIP — $CATEGORY (expected in CI; keeps CI keyless)"
  elif [ -n "$BRIDGE_SRC" ]; then
    echo "WARNING: test-linear-scan-live DID NOT RUN — $BRIDGE_SRC is set but did not resolve ($CATEGORY)." >&2
    echo "         See dev_docs/auth_key_access.md (Diagnostics) for what '$CATEGORY' means and how to fix it." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
  else
    echo "WARNING: test-linear-scan-live DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config ($CATEGORY)." >&2
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
  echo "WARNING: test-linear-scan-live has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

HAPPY_OUT="$(mktemp)"
HAPPY_ERR="$(mktemp)"
BAD_OUT="$(mktemp)"
BAD_ERR="$(mktemp)"
trap 'rm -f "$HAPPY_OUT" "$HAPPY_ERR" "$BAD_OUT" "$BAD_ERR"' EXIT

# Happy path: the real inherited key.
python3 "$SCRIPT" --team "$TEAM" >"$HAPPY_OUT" 2>"$HAPPY_ERR"
HAPPY_RC=$?

# Bad-key path: a bogus key with the op:// ref/resolver unset so it can't fall
# back and accidentally succeed. Exercises the fail-closed exit
# `/sweep-for-complete` and `/reconcile-tasks` fall back on.
env -u LINEAR_API_KEY_REF -u LINEAR_API_KEY_RESOLVER LINEAR_API_KEY="lin_api_BOGUS_000000000000000000000000" \
  python3 "$SCRIPT" --team "$TEAM" >"$BAD_OUT" 2>"$BAD_ERR"
BAD_RC=$?

python3 - "$HAPPY_OUT" "$HAPPY_ERR" "$HAPPY_RC" "$BAD_OUT" "$BAD_ERR" "$BAD_RC" <<'PY'
import json, sys
happy_out, happy_err, happy_rc, bad_out, bad_err, bad_rc = sys.argv[1:7]
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
        # Validate the top-level shape HERE, in the parse block — a valid `null`
        # payload decodes to None and would otherwise collide with the "not
        # parsed" state and silently skip every check below.
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
    st = m.get("states") or []
    if st and all(isinstance(s, dict) and {"id", "name", "type"} <= set(s) for s in st):
        ok("meta.states populated with id/name/type (%d)" % len(st))
    else:
        bad("meta.states populated with id/name/type")

    EXP = {"id", "identifier", "title", "url", "state", "attachments", "project", "scope"}
    issues = d.get("issues", [])
    if not isinstance(issues, list):
        bad("issues is a list", "got %s" % type(issues).__name__); issues = []
    non_obj = [i for i in issues if not isinstance(i, dict)]
    if non_obj:
        bad("every issue is a JSON object", "%d non-object element(s)" % len(non_obj))
        issues = [i for i in issues if isinstance(i, dict)]
    wrong = [i.get("identifier") for i in issues if set(i) != EXP]
    ok("every issue has the exact field set") if not wrong else bad("every issue has the exact field set", str(wrong[:5]))
    ok("no issue carries `description`") if all("description" not in i for i in issues) else bad("no issue carries `description`")
    attach = [i.get("identifier") for i in issues if not isinstance(i.get("attachments"), list) or not all(isinstance(u, str) for u in i["attachments"])]
    ok("every issue carries an attachments url list") if not attach else bad("every issue carries an attachments url list", str(attach[:5]))
    state_missing = [i.get("identifier") for i in issues if not isinstance(i.get("state"), dict) or "type" not in i.get("state", {})]
    ok("every issue carries state.type") if not state_missing else bad("every issue carries state.type", str(state_missing[:5]))
    # `project` must be the issue's own {id, name} (or null) — never the scope
    # name. A whole-team scan is the case that regressed: it used to stamp every
    # issue with the team name, leaving per-project lookup nothing to key on.
    # This shape check is what catches that regression, since the stamped value
    # was a bare string.
    proj_bad = [i.get("identifier") for i in issues
                if i.get("project") is not None
                and (not isinstance(i.get("project"), dict) or not {"id", "name"} <= set(i["project"]))]
    ok("every issue's project is {id, name} or null") if not proj_bad else bad("every issue's project is {id, name} or null", str(proj_bad[:5]))
    # The regression itself, named directly: `project` holding the value of its
    # own `scope`. Comparing sets of ids against sets of scope names cannot see
    # it — the regressed `project` is a string, so it never reaches an id set —
    # and under `--project <uuid>` scope IS the project id, so that comparison
    # would fail on correct output instead.
    proj_is_scope = [i.get("identifier") for i in issues if i.get("project") == i.get("scope")]
    ok("no issue's project is just its scope") if not proj_is_scope else bad("no issue's project is just its scope", str(proj_is_scope[:5]))

# --- bad-key fallback contract ------------------------------------------------
ok("bad key: exits non-zero") if bad_rc != 0 else bad("bad key: exits non-zero", "rc=0")
ok("bad key: stdout is empty") if not open(bad_out).read().strip() else bad("bad key: stdout is empty")
ok("bad key: error on stderr") if open(bad_err).read().strip() else bad("bad key: error on stderr")

sys.exit(1 if fails else 0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "test-linear-scan-live: OK (team=$TEAM)"
else
  echo "test-linear-scan-live: FAIL" >&2
fi
exit "$rc"
