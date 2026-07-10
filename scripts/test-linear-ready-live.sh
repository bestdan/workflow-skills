#!/usr/bin/env bash
# Live smoke test for commands/handlers/assets/linear-ready.py.
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
# {meta, candidates, dropped} shape, the candidate field set (and the ABSENCE
# of `description`/`_updatedAt`), the gate invariants (estimate < max, no
# excluded labels, branchName present), the drop-reason string format, and the
# fail-closed non-zero exit on a bad key (the fallback trigger `/do-tasks`
# relies on). It would have caught the `$team` String!-vs-ID regression on the
# first run.
#
# Run directly: LINEAR_API_KEY=… bash scripts/test-linear-ready-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/commands/handlers/assets/linear-ready.py"
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
    echo "test-linear-ready-live: SKIP — no key (expected in CI; keeps CI keyless)"
  elif [ -n "$REF_SRC" ]; then
    echo "WARNING: test-linear-ready-live DID NOT RUN — $REF_SRC is set but 'op read' could not resolve it non-interactively." >&2
    echo "         Install + sign in op in this terminal, or set \$OP_SERVICE_ACCOUNT_TOKEN — or export \$LINEAR_API_KEY directly." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
  else
    echo "WARNING: test-linear-ready-live DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config." >&2
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
  echo "WARNING: test-linear-ready-live has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

HAPPY_OUT="$(mktemp)"; HAPPY_ERR="$(mktemp)"
BAD_OUT="$(mktemp)"; BAD_ERR="$(mktemp)"
trap 'rm -f "$HAPPY_OUT" "$HAPPY_ERR" "$BAD_OUT" "$BAD_ERR"' EXIT

# Happy path: the real inherited key.
python3 "$SCRIPT" --team "$TEAM" --max-estimate 3 >"$HAPPY_OUT" 2>"$HAPPY_ERR"; HAPPY_RC=$?

# Bad-key path: a bogus key with the op:// ref unset so it can't fall back and
# accidentally succeed. Exercises the fail-closed exit `/do-tasks` falls back on.
env -u LINEAR_API_KEY_REF LINEAR_API_KEY="lin_api_BOGUS_000000000000000000000000" \
  python3 "$SCRIPT" --team "$TEAM" --max-estimate 3 >"$BAD_OUT" 2>"$BAD_ERR"; BAD_RC=$?

python3 - "$HAPPY_OUT" "$HAPPY_ERR" "$HAPPY_RC" "$BAD_OUT" "$BAD_ERR" "$BAD_RC" <<'PY'
import json, re, sys
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
        if isinstance(parsed, dict) and set(parsed) == {"meta", "candidates", "dropped"}:
            ok("top-level object is exactly {meta, candidates, dropped}")
            d = parsed
        else:
            bad("top-level object is exactly {meta, candidates, dropped}",
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

    EXP = {"id", "identifier", "title", "priority", "estimate", "labels", "url", "branchName", "state", "project"}
    EXCL = {"auto-claimed", "human-approval-requested", "blocked"}
    cands = d.get("candidates", [])
    if not isinstance(cands, list):
        bad("candidates is a list", "got %s" % type(cands).__name__); cands = []
    non_obj = [c for c in cands if not isinstance(c, dict)]
    if non_obj:
        bad("every candidate is a JSON object", "%d non-object element(s)" % len(non_obj))
        cands = [c for c in cands if isinstance(c, dict)]
    wrong = [c.get("identifier") for c in cands if set(c) != EXP]
    ok("every candidate has the exact field set") if not wrong else bad("every candidate has the exact field set", str(wrong[:5]))
    ok("no candidate carries `description`") if all("description" not in c for c in cands) else bad("no candidate carries `description`")
    ok("no candidate carries internal `_updatedAt`") if all("_updatedAt" not in c for c in cands) else bad("no candidate carries internal `_updatedAt`")
    est = [c.get("identifier") for c in cands if not isinstance(c.get("estimate"), (int, float)) or c.get("estimate") >= 3]
    ok("every candidate estimate < max (3)") if not est else bad("every candidate estimate < max (3)", str(est[:5]))
    lab = [c.get("identifier") for c in cands if set(c.get("labels") or []) & EXCL]
    ok("no candidate carries an excluded label") if not lab else bad("no candidate carries an excluded label", str(lab[:5]))
    bn = [c.get("identifier") for c in cands if not c.get("branchName")]
    ok("every candidate carries branchName") if not bn else bad("every candidate carries branchName", str(bn[:5]))

    pat = re.compile(r"^(no estimate set|already auto-claimed|human-approval-requested|blocked|estimate \d+(?:\.\d+)? >= \d+(?:\.\d+)?|assigned to .+)$")
    dropped = d.get("dropped", [])
    if not isinstance(dropped, list):
        bad("dropped is a list", "got %s" % type(dropped).__name__); dropped = []
    off = [x for x in dropped if not isinstance(x, dict) or not pat.match(str(x.get("reason") or ""))]
    ok("every drop reason matches the canonical strings") if not off else bad("every drop reason matches the canonical strings", str(off[:5]))

# --- bad-key fallback contract ------------------------------------------------
ok("bad key: exits non-zero") if bad_rc != 0 else bad("bad key: exits non-zero", "rc=0")
ok("bad key: stdout is empty") if not open(bad_out).read().strip() else bad("bad key: stdout is empty")
ok("bad key: error on stderr") if open(bad_err).read().strip() else bad("bad key: error on stderr")

sys.exit(1 if fails else 0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "test-linear-ready-live: OK (team=$TEAM)"
else
  echo "test-linear-ready-live: FAIL" >&2
fi
exit "$rc"
