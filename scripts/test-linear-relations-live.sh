#!/usr/bin/env bash
# Live smoke test for commands/handlers/assets/linear-relations.py.
#
# Unlike the other scripts/test-*.sh harnesses, this one hits the REAL Linear
# GraphQL API, so it is OPT-IN and only runs when a key is resolvable: a raw
# $LINEAR_API_KEY, an op:// ref in $LINEAR_API_KEY_REF, or `linear.api_key_ref`
# from the repo config — the last two resolved via `op read`.
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

# Resolve an op:// ref to its secret WITHOUT ever hanging. `op read` BLOCKS on a
# locked 1Password desktop session (a biometric prompt it can't answer in this
# non-interactive subshell), which would stall check.sh — so run it in the
# background and hard-kill it after ~6s. Prints the secret on success; non-zero
# and empty on failure/timeout. (macOS ships no `timeout`, hence the manual bound.)
op_read_bounded() {
  command -v op >/dev/null 2>&1 || return 1
  local tmp; tmp="$(mktemp)"
  op read "$1" >"$tmp" 2>/dev/null &
  local pid=$! i=0
  while kill -0 "$pid" 2>/dev/null; do
    i=$((i + 1))
    if [ "$i" -gt 60 ]; then
      kill "$pid" 2>/dev/null; sleep 0.2; kill -9 "$pid" 2>/dev/null # TERM then KILL, so a TERM-ignoring op can't wedge the wait
      wait "$pid" 2>/dev/null; rm -f "$tmp"; return 1
    fi
    sleep 0.1
  done
  if wait "$pid"; then cat "$tmp"; rm -f "$tmp"; return 0; fi
  rm -f "$tmp"; return 1
}

# --- resolve a key (opt-in) ---------------------------------------------------
# Precedence: a raw $LINEAR_API_KEY; else an op:// ref in $LINEAR_API_KEY_REF;
# else linear.api_key_ref from the repo config. A ref is resolved with `op read`,
# which only works non-interactively when op is signed in (an authorized
# terminal) or $OP_SERVICE_ACCOUNT_TOKEN is set. Anything unresolved -> the test
# WARNs (loud locally, quiet in CI) and exits 0 — it never fails the suite, so
# keyless devs and CI stay green and the full-account key never lands in CI.
KEY="${LINEAR_API_KEY:-}"
REF_SRC=""
if [ -z "$KEY" ]; then
  REF="${LINEAR_API_KEY_REF:-}"; [ -n "$REF" ] && REF_SRC="\$LINEAR_API_KEY_REF"
  # Prefer the gitignored local override, then the committed config. A personal
  # op://Private/... ref belongs in .task-config.local.yml — never committed to
  # this public repo — so read it first.
  for cfg in "$LOCAL_CONFIG" "$CONFIG"; do
    if [ -z "$REF" ] && [ -f "$cfg" ]; then
      REF="$(sed -n 's/^[[:space:]]*api_key_ref:[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$cfg" | head -1)"
      REF="${REF%\"}"; REF="${REF#\"}"; REF="${REF%\'}"; REF="${REF#\'}"
      [ -n "$REF" ] && REF_SRC="linear.api_key_ref (${cfg##*/})"
    fi
  done
  if [ -n "$REF" ]; then
    KEY="$(op_read_bounded "$REF" || true)"
  fi
fi

if [ -z "$KEY" ]; then
  if [ -n "${CI:-}" ]; then
    echo "test-linear-relations-live: SKIP — no key (expected in CI; keeps CI keyless)"
  elif [ -n "$REF_SRC" ]; then
    echo "WARNING: test-linear-relations-live DID NOT RUN — $REF_SRC is set but 'op read' could not resolve it non-interactively." >&2
    echo "         Install + sign in op in this terminal, or set \$OP_SERVICE_ACCOUNT_TOKEN — or export \$LINEAR_API_KEY directly." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
  else
    echo "WARNING: test-linear-relations-live DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
    echo "         Export a key or set linear.api_key_ref to enable it." >&2
  fi
  exit 0
fi
export LINEAR_API_KEY="$KEY"

# --- resolve the team ($LINEAR_TEAM, else linear.team from the config) --------
TEAM="${LINEAR_TEAM:-}"
if [ -z "$TEAM" ] && [ -f "$CONFIG" ]; then
  TEAM="$(sed -n 's/^[[:space:]]*team:[[:space:]]*\([^#[:space:]]*\).*/\1/p' "$CONFIG" | head -1)"
  # Strip surrounding quotes so a quoted `team: "ENG"` / `team: 'ENG'` resolves
  # the same as unquoted (the sed capture keeps the quote chars).
  TEAM="${TEAM%\"}"; TEAM="${TEAM#\"}"
  TEAM="${TEAM%\'}"; TEAM="${TEAM#\'}"
fi
if [ -z "$TEAM" ]; then
  echo "WARNING: test-linear-relations-live has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

HAPPY_OUT="$(mktemp)"; HAPPY_ERR="$(mktemp)"
BAD_OUT="$(mktemp)"; BAD_ERR="$(mktemp)"
ENUM_OUT="$(mktemp)"
trap 'rm -f "$HAPPY_OUT" "$HAPPY_ERR" "$BAD_OUT" "$BAD_ERR" "$ENUM_OUT"' EXIT

# Happy path: the real inherited key.
python3 "$SCRIPT" --team "$TEAM" --limit 50 >"$HAPPY_OUT" 2>"$HAPPY_ERR"; HAPPY_RC=$?

# Enum-drift guard: introspect the IssueRelationType enum and confirm every value
# is one derive_edges() actually handles ({blocks, related, duplicate}). Without
# this, a renamed/added enum value is silently dropped to empty edge lists and
# slips past the list-shape checks below — the exact drift the header claims to
# surface. Field-NAME drift already fails via the happy-path rc!=0 (GraphQL 400);
# this closes the enum-VALUE gap.
curl -s -X POST https://api.linear.app/graphql \
  -H "Authorization: $LINEAR_API_KEY" -H 'Content-Type: application/json' \
  --data '{"query":"query{__type(name:\"IssueRelationType\"){enumValues{name}}}"}' \
  >"$ENUM_OUT" 2>/dev/null || true

# Bad-key path: a bogus key with the op:// ref unset so it can't fall back and
# accidentally succeed. Exercises the fail-closed exit `linear-reoptimize.md`
# falls back on.
env -u LINEAR_API_KEY_REF LINEAR_API_KEY="lin_api_BOGUS_000000000000000000000000" \
  python3 "$SCRIPT" --team "$TEAM" --limit 50 >"$BAD_OUT" 2>"$BAD_ERR"; BAD_RC=$?

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
KNOWN_TYPES = {"blocks", "related", "duplicate"}
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
