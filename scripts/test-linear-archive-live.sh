#!/usr/bin/env bash
# Live smoke test for commands/handlers/assets/linear-archive.py.
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
# project-id fallback below queries the API directly and resolves in-process,
# the same carve-out test-linear-relations-live.sh makes for its enum guard.
#
# With an APPROVAL-BASED resolver (e.g. opx) that means SIX dialogs per run —
# the probe, the project-id fallback, and each of the four dry-run invocations
# of the script under test — since each resolve is separately approved and the
# session is invalidated between them. That is the resolver working as designed,
# not a bug; use `LINEAR_API_KEY=… bash <this>` to run it with a single
# pre-resolved key instead. (The bad-key run passes its own bogus key inline, so
# it never resolves.)
# With no key it SKIPS and exits 0 — this keeps `check.sh` green for keyless
# devs and keeps CI keyless *by construction*: a Linear personal API key is a
# full-account bearer token that must never live in CI secrets (see
# commands/handlers/linear-config.md "Archive key" and linear-claim.md's
# security-boundary note). On skip it prints a WARNING (loud outside CI, quiet
# in CI) so a missing key never silently reads as "all green".
#
# SAFETY: linear-archive.py MUTATES (it archives issues). Every invocation in
# this harness MUST omit --apply, which makes it DRY RUN — never pass --apply
# here. The whole-team assertions include a loud check that no run ever printed
# an "Archiving" line, as a backstop against that ever changing by accident.
#
# It asserts the DRY-RUN output CONTRACT (state is mutable, so this checks
# shape, not workspace values): the `Cutoff: ...` header (team, and — when
# scoped — `projects=`), the "N candidate(s):"/"Nothing to archive." shapes,
# and the repeatable `--project` contract (PRE-416): passing the same project
# twice must not duplicate it in the header or the candidate set, and passing
# two different projects must union their candidates. It would have caught the
# PRE-567 declared-but-unused-`$project` regression on the first run — a
# stubbed unit test can't see an HTTP 400 that only a real wire round-trip
# provokes.
#
# Run directly: LINEAR_API_KEY=… bash scripts/test-linear-archive-live.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/commands/handlers/assets/linear-archive.py"
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
    echo "test-linear-archive-live: SKIP — $CATEGORY (expected in CI; keeps CI keyless)"
  elif [ -n "$BRIDGE_SRC" ]; then
    echo "WARNING: test-linear-archive-live DID NOT RUN — $BRIDGE_SRC is set but did not resolve ($CATEGORY)." >&2
    echo "         See dev_docs/auth_key_access.md (Diagnostics) for what '$CATEGORY' means and how to fix it." >&2
    echo "         Linear API contract drift will NOT be detected on this run." >&2
  else
    echo "WARNING: test-linear-archive-live DID NOT RUN — no \$LINEAR_API_KEY / \$LINEAR_API_KEY_REF and no linear.api_key_ref in config ($CATEGORY)." >&2
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
  echo "WARNING: test-linear-archive-live has a key but no team (\$LINEAR_TEAM unset, none in $CONFIG) — skipping." >&2
  exit 0
fi

# --- resolve two project ids for the multi-project (PRE-416) assertions ------
# Prefer configured `linear.projects` (local override wins wholesale over the
# committed file, matching the api_key/team precedent above) over a live
# lookup — a live lookup can't tell a stale/decommissioned project apart from
# one actually meant for this repo. Only falls back to querying Linear itself
# when fewer than two are configured. `yaml_leaf` only grabs one leaf, so this
# is a small dedicated extractor for the `projects:` list.
#
# Whichever pair comes back, the header assertions below hold regardless of what
# is in them — those are the load-bearing ones. The union/subset check is only as
# strong as the pair's data: two projects with no terminal issues satisfy it with
# empty sets. Left that way on purpose, since picking a pair known to have
# candidates would mean a second query and a data-dependent harness.
PROJECT_IDS="$(
  python3 - "$LOCAL_CONFIG" "$CONFIG" "$TEAM" "$ROOT" <<'PY'
import os
import re
import sys

local_cfg, committed_cfg, team, root = sys.argv[1:5]


def ids_from_projects_block(path):
    """Collect the `id:` values of every entry under a `projects:` list key."""
    try:
        lines = open(path).read().splitlines()
    except OSError:
        return []
    out = []
    in_block = False
    key_indent = None
    for line in lines:
        if not in_block:
            m = re.match(r'^(\s*)projects:\s*(#.*)?$', line)
            if m:
                in_block = True
                key_indent = len(m.group(1))
            continue
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(' '))
        if indent <= key_indent:
            break  # dedented out of the projects: block
        m = re.match(r'^\s*-?\s*id:\s*(.+?)\s*(#.*)?$', line)
        if m:
            val = m.group(1).strip()
            if len(val) >= 2 and val[0] in '"\'' and val[-1] == val[0]:
                val = val[1:-1]
            if val:
                out.append(val)
    return out


ids = []
for path in (local_cfg, committed_cfg):
    for i in ids_from_projects_block(path):
        if i not in ids:
            ids.append(i)
    if len(ids) >= 2:
        break

if len(ids) < 2:
    # Fall back to Linear itself, mirroring how linear-archive.py builds a
    # request (same team-name-vs-id detection, same bare-key auth header).
    sys.path.insert(0, os.path.join(root, "commands/handlers/assets"))
    try:
        import json
        import urllib.error
        import urllib.request

        from _secret_resolve import resolve_key

        key = resolve_key("LINEAR_API_KEY")
        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
        )
        team_field = "id" if uuid_re.match(team) else "name"
        query = (
            "query($team: String!) { teams(filter: { %s: { eq: $team } }) "
            "{ nodes { projects(first: 50) { nodes { id } } } } }"
        ) % team_field
        body = json.dumps({"query": query, "variables": {"team": team}}).encode()
        req = urllib.request.Request(
            "https://api.linear.app/graphql",
            data=body,
            headers={"Authorization": key, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            payload = json.loads(r.read())
        if isinstance(payload, dict) and "errors" not in payload:
            for node in payload.get("data", {}).get("teams", {}).get("nodes", []):
                for p in node.get("projects", {}).get("nodes", []):
                    pid = p.get("id")
                    if pid and pid not in ids:
                        ids.append(pid)
                    if len(ids) >= 2:
                        break
                if len(ids) >= 2:
                    break
    except Exception as e:
        # Name the failure on stderr rather than swallowing it. stdout is the id
        # list, so this cannot corrupt it. Reporting matters most under an
        # approval-based resolver: `opx` invalidates the `op` session after each
        # read (dev_docs/auth_key_access.md), so the earlier --probe can consume
        # the approval and this resolve then raises — and the skip note below
        # would otherwise blame an empty config that is not the reason.
        print("project lookup failed: %s: %s" % (type(e).__name__, e), file=sys.stderr)

for i in ids[:2]:
    print(i)
PY
)"
P1="$(printf '%s\n' "$PROJECT_IDS" | sed -n '1p')"
P2="$(printf '%s\n' "$PROJECT_IDS" | sed -n '2p')"
MULTI_OK=1
if [ -z "$P1" ] || [ -z "$P2" ]; then
  MULTI_OK=0
fi

TEAM_OUT="$(mktemp)"
TEAM_ERR="$(mktemp)"
ONE_OUT="$(mktemp)"
ONE_ERR="$(mktemp)"
DUPE_OUT="$(mktemp)"
DUPE_ERR="$(mktemp)"
MULTI_OUT="$(mktemp)"
MULTI_ERR="$(mktemp)"
BAD_OUT="$(mktemp)"
BAD_ERR="$(mktemp)"
trap 'rm -f "$TEAM_OUT" "$TEAM_ERR" "$ONE_OUT" "$ONE_ERR" "$DUPE_OUT" "$DUPE_ERR" "$MULTI_OUT" "$MULTI_ERR" "$BAD_OUT" "$BAD_ERR"' EXIT

# --older-than 1 keeps the candidate set wide. --apply is NEVER passed anywhere
# below — every one of these runs is DRY RUN by construction.
python3 "$SCRIPT" --team "$TEAM" --older-than 1 >"$TEAM_OUT" 2>"$TEAM_ERR"
TEAM_RC=$?

ONE_RC=1
DUPE_RC=1
MULTI_RC=1
if [ "$MULTI_OK" -eq 1 ]; then
  python3 "$SCRIPT" --team "$TEAM" --older-than 1 --project "$P1" >"$ONE_OUT" 2>"$ONE_ERR"
  ONE_RC=$?
  python3 "$SCRIPT" --team "$TEAM" --older-than 1 --project "$P1" --project "$P1" >"$DUPE_OUT" 2>"$DUPE_ERR"
  DUPE_RC=$?
  python3 "$SCRIPT" --team "$TEAM" --older-than 1 --project "$P1" --project "$P2" >"$MULTI_OUT" 2>"$MULTI_ERR"
  MULTI_RC=$?
fi

# Bad-key path: a bogus key with the op:// ref/resolver unset so it can't fall
# back and accidentally succeed. Exercises the fail-closed exit a caller
# depends on to never treat "no candidates printed" as "archived nothing".
env -u LINEAR_API_KEY_REF -u LINEAR_API_KEY_RESOLVER LINEAR_API_KEY="lin_api_BOGUS_000000000000000000000000" \
  python3 "$SCRIPT" --team "$TEAM" --older-than 1 >"$BAD_OUT" 2>"$BAD_ERR"
BAD_RC=$?

python3 - \
  "$TEAM_OUT" "$TEAM_ERR" "$TEAM_RC" \
  "$MULTI_OK" "$P1" "$P2" \
  "$ONE_OUT" "$ONE_ERR" "$ONE_RC" \
  "$DUPE_OUT" "$DUPE_ERR" "$DUPE_RC" \
  "$MULTI_OUT" "$MULTI_ERR" "$MULTI_RC" \
  "$BAD_OUT" "$BAD_ERR" "$BAD_RC" \
  "$TEAM" <<'PY'
import re
import sys

(team_out, team_err, team_rc,
 multi_ok, p1, p2,
 one_out, one_err, one_rc,
 dupe_out, dupe_err, dupe_rc,
 multi_out, multi_err, multi_rc,
 bad_out, bad_err, bad_rc,
 team) = sys.argv[1:20]

team_rc, one_rc, dupe_rc, multi_rc, bad_rc = (
    int(team_rc), int(one_rc), int(dupe_rc), int(multi_rc), int(bad_rc)
)
multi_ok = multi_ok == "1"
fails = 0


def ok(m):
    print("ok   - " + m)


def skip(m):
    # Distinct from ok(): a section that never ran is not a section that passed.
    print("skip - " + m)


def bad(m, x=""):
    global fails
    fails += 1
    print("FAIL - " + m + (("  " + x) if x else ""))


# Two-space indent, identifier, date, two-space gap — mirrors the real
# `f"  {identifier:<10} {when}  {title[:70]}"` format exactly.
CAND_RE = re.compile(r'^  (\S+)\s+(\d{4}-\d{2}-\d{2})  ', re.M)
HEADER_RE = re.compile(
    # `projects=` is comma-joined, so the capture must not stop at the first
    # comma — it is bounded by the trailing `, Done + ...` anchor instead.
    r'^Cutoff: (\S+)  \(team=([^,]+)(?:, projects=([^)]+?))?, '
    r'Done \+ Canceled \+ Duplicate\)$',
    re.M,
)


def candidates(text):
    return {m.group(1) for m in CAND_RE.finditer(text)}


def header(text):
    return HEADER_RE.search(text)


# --- whole-team ----------------------------------------------------------------
team_text = open(team_out).read()
if team_rc == 0:
    ok("team: exits 0")
else:
    bad("team: exits 0", "rc=%d %s" % (team_rc, open(team_err).read()[:200]))

hm = header(team_text)
if hm:
    if hm.group(2) == team:
        ok("team: header names team=%s" % team)
    else:
        bad("team: header names team=%s" % team, "got %r" % hm.group(2))
    if hm.group(3) is None:
        ok("team: header names no projects= (unscoped run)")
    else:
        bad("team: header names no projects= (unscoped run)", "got projects=%s" % hm.group(3))
else:
    bad("team: header matches 'Cutoff: ... (team=..., Done + Canceled + Duplicate)'", team_text[:200])

if re.search(r'^\d+ candidate\(s\):$', team_text, re.M) or "Nothing to archive." in team_text:
    ok("team: stdout says N candidate(s) or Nothing to archive")
else:
    bad("team: stdout says N candidate(s) or Nothing to archive", team_text[:200])

# SAFETY: this is a DRY RUN harness. If this ever fails it means --apply
# leaked in somewhere above, or linear-archive.py archived without being
# asked to — either way, real workspace data may have just been mutated.
if "Archiving" in team_text:
    bad("SAFETY: team dry run must never print an Archiving line", team_text[:300])
else:
    ok("SAFETY: team dry run never printed an Archiving line")

team_cands = candidates(team_text)

# --- project scoping (PRE-416: repeatable --project) ----------------------------
if not multi_ok:
    skip("project-scoping assertions — fewer than two project ids resolvable. "
         "Either no linear.projects entries and no live team projects to fall "
         "back on, or the lookup itself failed; it prints its reason on stderr.")
else:
    one_text = open(one_out).read()
    dupe_text = open(dupe_out).read()
    multi_text = open(multi_out).read()

    if one_rc == 0:
        ok("one: exits 0")
    else:
        bad("one: exits 0", "rc=%d %s" % (one_rc, open(one_err).read()[:200]))
    hm1 = header(one_text)
    if hm1 and hm1.group(3) == p1:
        ok("one: header names projects=%s exactly" % p1)
    else:
        bad("one: header names projects=%s exactly" % p1,
            "got %r" % (hm1.group(3) if hm1 else one_text[:200]))

    if dupe_rc == 0:
        ok("dupe: exits 0")
    else:
        bad("dupe: exits 0", "rc=%d %s" % (dupe_rc, open(dupe_err).read()[:200]))
    hm2 = header(dupe_text)
    dupe_projects = hm2.group(3).split(",") if hm2 and hm2.group(3) else None
    if dupe_projects == [p1]:
        ok("dupe: header names %s once, not twice" % p1)
    else:
        bad("dupe: header names %s once, not twice" % p1, "got %r" % (dupe_projects,))

    one_cands = candidates(one_text)
    dupe_cands = candidates(dupe_text)
    if dupe_cands == one_cands:
        ok("dupe: candidate identifier set equals one's")
    else:
        bad("dupe: candidate identifier set equals one's",
            "one=%s dupe=%s" % (sorted(one_cands)[:5], sorted(dupe_cands)[:5]))

    if multi_rc == 0:
        ok("multi: exits 0")
    else:
        bad("multi: exits 0", "rc=%d %s" % (multi_rc, open(multi_err).read()[:200]))
    hm3 = header(multi_text)
    multi_projects = hm3.group(3).split(",") if hm3 and hm3.group(3) else None
    if multi_projects == [p1, p2]:
        ok("multi: header names projects=%s,%s in order" % (p1, p2))
    else:
        bad("multi: header names projects=%s,%s in order" % (p1, p2), "got %r" % (multi_projects,))

    multi_cands = candidates(multi_text)
    if one_cands <= multi_cands <= team_cands:
        ok("multi: candidate set is a superset of one's and a subset of team's")
    else:
        bad("multi: candidate set is a superset of one's and a subset of team's",
            "one-multi=%s multi-team=%s" % (
                sorted(one_cands - multi_cands)[:5], sorted(multi_cands - team_cands)[:5]))

    # PRE-567: a project-scoped query that declares $project but never uses it
    # (or vice versa) surfaces as an HTTP 400 — a stubbed unit test can't see
    # that; only a real wire round-trip can.
    for label, text, err_path in (
        ("one", one_text, one_err), ("dupe", dupe_text, dupe_err), ("multi", multi_text, multi_err),
    ):
        stderr_text = open(err_path).read()
        if not stderr_text.strip():
            ok("%s: stderr is empty (no GraphQL error)" % label)
        else:
            bad("%s: stderr is empty (no GraphQL error)" % label, stderr_text[:200])
        if not re.search(r'\berror\b|\b400\b', text, re.I):
            ok("%s: stdout has no error/400 text" % label)
        else:
            bad("%s: stdout has no error/400 text" % label, text[:200])

# --- bad-key fallback contract ------------------------------------------------
if bad_rc != 0:
    ok("bad key: exits non-zero")
else:
    bad("bad key: exits non-zero", "rc=0")
if not open(bad_out).read().strip():
    ok("bad key: stdout is empty")
else:
    bad("bad key: stdout is empty")
if open(bad_err).read().strip():
    ok("bad key: stderr is non-empty")
else:
    bad("bad key: stderr is non-empty")

sys.exit(1 if fails else 0)
PY
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "test-linear-archive-live: OK (team=$TEAM)"
else
  echo "test-linear-archive-live: FAIL" >&2
fi
exit "$rc"
