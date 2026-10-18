#!/usr/bin/env bash
# Can this environment read a GitHub issue dependency edge?
#
# Written for a cloud session or a routine, where `gh` is usually ABSENT: it
# arrives from the dotfiles setup script, so a session sourcing any other repo
# has no gh binary at all (dev_docs/decisions/2026-09-05-*.md, finding 1).
# So this probe uses curl and the ambient $GH_TOKEN, and only mentions gh to
# report whether it happened to be there.
#
# Ground truth: bestdan/workflow-skills#691 IS blocked_by #692, verified on the
# live board 2026-09-15. An empty answer here is a FAILURE, not a pass -- which
# is why this names a known edge instead of asking whether a repo has any.
#
# Usage:  bash scripts/probe-cloud-deps.sh
# Exit:   0 = the edge was read.  1 = it was not.  2 = could not run.

set -u

REPO="${PROBE_REPO:-bestdan/workflow-skills}"
ISSUE="${PROBE_ISSUE:-691}"
EXPECT="${PROBE_EXPECT:-692}"
API="${PROBE_API:-https://api.github.com}"

pass=0
fail=0
# The VERDICT turns on this alone, not on the pass/fail counters. The baseline
# check is a diagnostic that disambiguates a step-2 refusal; letting it into the
# verdict lets the script print "PASS read the edge" and then report that the
# environment cannot read edges.
edge_ok=0
note() { printf '  %s\n' "$1"; }
ok() {
  printf 'PASS  %s\n' "$1"
  pass=$((pass + 1))
}
no() {
  printf 'FAIL  %s\n' "$1"
  fail=$((fail + 1))
}

echo "=== environment ==="
echo "repo         : $REPO"
echo "ground truth : #$ISSUE is blocked_by #$EXPECT"
echo "gh binary    : $(command -v gh || echo '(absent -- expected off dotfiles)')"

# Never print a token value. Whether it is the proxy placeholder or a real
# credential is itself the finding: it decides whether this capability travels
# off the proxy egress path, so the probe always reports it.
# PROBE_TOKEN_FILE lets a caller hand the token over without putting it in argv
# or in the environment of every child. A cloud session needs none of this --
# it just has GH_TOKEN set already.
#
# An explicit-but-unreadable path is INCONCLUSIVE, never a fallback. Falling
# through to the ambient token would answer confidently about a credential the
# caller did not ask about -- and the whole output of this script is a verdict
# about which credential works, so that is a wrong answer, not a missing one.
if [ -n "${PROBE_TOKEN_FILE:-}" ]; then
  if [ ! -r "$PROBE_TOKEN_FILE" ]; then
    echo "token        : PROBE_TOKEN_FILE is set but not readable"
    note "path: $PROBE_TOKEN_FILE"
    echo
    echo "RESULT: INCONCLUSIVE -- refusing to fall back to the ambient token."
    exit 2
  fi
  tok="$(tr -d '\r\n' <"$PROBE_TOKEN_FILE")"
else
  tok="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
fi
if [ -z "$tok" ]; then
  echo "token        : (neither GH_TOKEN nor GITHUB_TOKEN is set)"
  echo
  echo "RESULT: INCONCLUSIVE -- no token to try."
  exit 2
elif [ "$tok" = "proxy-injected" ]; then
  echo "token        : literal 'proxy-injected' (len ${#tok})"
  note "if the read below SUCCEEDS, the egress proxy is substituting a real"
  note "credential -- so the capability is tied to this proxy path."
else
  # Report the token TYPE, not four raw bytes. For every current GitHub format
  # the leading characters are a fixed, entropy-free prefix -- but a legacy
  # 40-hex PAT has no prefix at all, so slicing it would print secret material
  # under a comment promising not to.
  case "$tok" in
    ghp_*) ttype="classic PAT (ghp_)" ;;
    gho_*) ttype="OAuth token (gho_)" ;;
    ghs_*) ttype="server-to-server / Actions (ghs_)" ;;
    ghu_*) ttype="user-to-server (ghu_)" ;;
    ghr_*) ttype="refresh token (ghr_)" ;;
    github_pat_*) ttype="fine-grained PAT (github_pat_)" ;;
    *) ttype="unrecognised — no known GitHub prefix" ;;
  esac
  echo "token        : real value, type $ttype, len ${#tok}"
fi
echo

if ! command -v curl >/dev/null 2>&1; then
  echo "RESULT: INCONCLUSIVE -- no curl."
  exit 2
fi
# python3 parses the response below. Check it HERE rather than letting the parse
# fail: its stderr is suppressed, so a missing interpreter would leave `found`
# empty and take the empty-200 branch -- reporting a denial the probe never saw.
if ! command -v python3 >/dev/null 2>&1; then
  echo "RESULT: INCONCLUSIVE -- no python3 to parse the response."
  exit 2
fi

STATUS=""
BODY=""
# Sets the globals STATUS and BODY. Deliberately NOT called via $( ), which
# would run it in a subshell and silently drop STATUS -- the caller would then
# read an empty status and misreport every outcome.
req() { # req <path>
  local out
  out="$(curl -sS -w '\n%{http_code}' \
    -H "Authorization: Bearer $tok" \
    -H "Accept: application/vnd.github+json" \
    "$API/$1" 2>&1)"
  STATUS="$(printf '%s' "$out" | tail -1)"
  BODY="$(printf '%s' "$out" | sed '$d')"
}

echo "=== 1. baseline: can ANY authenticated read succeed? ==="
# Separates "the API is closed to me" from "the dependency endpoint is closed".
# Without this a 403 in step 2 is ambiguous.
req "repos/$REPO"
if [ "$STATUS" = "200" ]; then
  ok "plain repo read works (HTTP 200)"
else
  no "plain repo read failed (HTTP $STATUS)"
  note "$(printf '%s' "$BODY" | head -3)"
fi
echo

echo "=== 2. the real question: read the dependency edge ==="
# per_page=100 stands in for --paginate: a default page stops at 30, and an
# unseen edge reads as absent, which is the most expensive way to be wrong.
req "repos/$REPO/issues/$ISSUE/dependencies/blocked_by?per_page=100"
if [ "$STATUS" != "200" ]; then
  no "the dependency endpoint refused the call (HTTP $STATUS)"
  note "$(printf '%s' "$BODY" | head -4)"
  case "$STATUS" in
    401) note "-> 401: the token is not valid for this API at all." ;;
    403)
      note "-> 403: permission or policy. If the body names the Claude GitHub"
      note "   App, connecting that app is the remedy it is asking for."
      ;;
    404) note "-> 404 can mean no permission rather than no endpoint. Treat as denied." ;;
  esac
else
  found="$(printf '%s' "$BODY" | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if isinstance(d, list):
    for i in d:
        print(i.get("number"))' 2>/dev/null)"

  if [ -z "$found" ]; then
    # THE IMPORTANT BRANCH, and it has two causes, not one. An empty 200 means
    # either a silent denial OR that the fixture edge is gone -- the ground
    # truth is live board state and nothing pins it. Separate them here rather
    # than reporting the alarming one by default: a false "CANNOT read" sends
    # the reader after a credential problem that does not exist.
    req "repos/$REPO/issues/$ISSUE"
    if [ "$STATUS" = "404" ]; then
      echo
      echo "RESULT: INCONCLUSIVE -- the fixture issue #$ISSUE is not readable"
      echo "        (HTTP 404), so an empty edge list says nothing about access."
      echo "        Re-point the probe with PROBE_ISSUE/PROBE_EXPECT at an edge"
      echo "        you have confirmed exists."
      exit 2
    fi
    no "HTTP 200 but NO edges returned -- and #$ISSUE is expected to have one"
    note "two explanations, and this probe cannot separate them further:"
    note "  1. a silent denial -- the likely one, and why this branch fails"
    note "  2. the fixture edge was removed from the board since 2026-09-15"
    note "check #$ISSUE on the board, or re-run with PROBE_ISSUE/PROBE_EXPECT"
    note "pointed at an edge you have just confirmed."
  elif grep -qx "$EXPECT" <<<"$found"; then
    ok "read the edge: #$ISSUE is blocked_by #$EXPECT"
    edge_ok=1
  else
    no "returned edges, but not the expected one"
    note "expected $EXPECT, got: $(printf '%s' "$found" | tr '\n' ' ')"
  fi
fi
echo

echo "=== result ==="
echo "passed: $pass   failed: $fail"
if [ "$edge_ok" -eq 1 ]; then
  if [ "$fail" -ne 0 ]; then
    note "(the edge read succeeded; the baseline check did not -- read the"
    note " diagnostics above before trusting either.)"
  fi
  echo "VERDICT: this environment CAN read dependency edges."
  echo "         An unattended agent here does not need the Blocked-by footer."
  exit 0
fi
echo "VERDICT: this environment CANNOT read dependency edges."
echo "         Note what is NOT thereby shown: $REPO must be this session's"
echo "         source repo or an attached one. The connector is repo-scoped and"
echo "         this endpoint appears to be too, so a refusal on an unattached"
echo "         repo says nothing about the environment's access in general."
exit 1
