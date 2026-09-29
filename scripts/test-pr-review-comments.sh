#!/usr/bin/env bash
# test-pr-review-comments.sh — hermetic tests for scripts/pr-review-comments.sh.
#
# A stub `gh` on PATH serves canned GraphQL responses keyed on which operation
# the query text names, and records every call: one summary line per call
# (`<op> <var=value ...>`, the query text dropped) in $CALLS, the full argv in
# $RAW, and a copy of any `-F body=@file` file in $STATE/body.<n>. Nothing here
# touches the network. Covers:
#   - usage errors exit 2 and make no gh call
#   - databaseId → node ID resolution, across a second reviewThreads page and
#     for a pending comment reachable only through its review
#   - edit/delete/reply/add send the right mutation with the right node IDs,
#     and the body arrives byte-for-byte (backticks, quotes, newlines)
#   - add reuses the viewer's pending review, or starts one when there is none
#   - add refuses an unanchored RIGHT line before any mutation; LEFT skips
#   - no call ever submits a review
#
# Run directly: bash scripts/test-pr-review-comments.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/pr-review-comments.sh"

command -v jq >/dev/null 2>&1 || {
  echo "test-pr-review-comments: jq is required but not found in PATH" >&2
  exit 2
}

BASE="$(mktemp -d 2>/dev/null \
  || mktemp -d "${TMPDIR:-/tmp}/pr-review-comments-test.XXXXXX" 2>/dev/null)"
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-pr-review-comments: could not create a temp dir" >&2
  exit 2
}
trap 'rm -rf "$BASE"' EXIT

fail=0
pass_count=0
fail_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  ✔ $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  ✘ $1" >&2
}

assert_eq() {
  # assert_eq <description> <expected> <actual>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected '$2', got '$3')"
  fi
}

assert_contains() {
  # assert_contains <haystack> <description> <needle>
  if grep -qF -- "$3" <<<"$1"; then
    ok "$2"
  else
    bad "$2 (did not find '$3')"
  fi
}

assert_not_contains() {
  # assert_not_contains <haystack> <description> <needle>
  if grep -qF -- "$3" <<<"$1"; then
    bad "$2 (unexpectedly found '$3')"
  else
    ok "$2"
  fi
}

# --- gh stub -------------------------------------------------------------------
BIN="$BASE/bin"
mkdir -p "$BIN"
cat >"$BIN/gh" <<'EOF'
#!/usr/bin/env bash
set -uo pipefail
printf '%s\n' "$*" >>"$PRC_STATE/raw"
if [ "$1 $2" = "repo view" ]; then
  echo "repo view" >>"$PRC_STATE/calls"
  echo "test/repo"
  exit 0
fi
if [ "$1 $2" = "pr diff" ]; then
  echo "pr diff $*" >>"$PRC_STATE/calls"
  cat "$PRC_FIX/diff"
  exit 0
fi
query=""
vars=""
after=""
for a in "$@"; do
  case "$a" in
    query=*) query="${a#query=}" ;;
    body=@*)
      n=$(($(ls "$PRC_STATE" | grep -c '^body\.') + 1))
      cp "${a#body=@}" "$PRC_STATE/body.$n"
      vars="$vars body=@file"
      ;;
    after=*)
      after="${a#after=}"
      vars="$vars $a"
      ;;
    *=*) vars="$vars $a" ;;
  esac
done
case "$query" in
  *"addPullRequestReviewThreadReply("*) op=reply ;;
  *"addPullRequestReviewThread("*) op=add-thread ;;
  *"addPullRequestReview("*) op=start-review ;;
  *"updatePullRequestReviewComment("*) op=update ;;
  *"deletePullRequestReviewComment("*) op=delete ;;
  *reviewThreads*) op=pr ;;
  *)
    echo "gh stub: unrecognized call: $*" >&2
    exit 1
    ;;
esac
echo "$op$vars" >>"$PRC_STATE/calls"
if [ "$op" = pr ] && [ -n "$after" ]; then
  cat "$PRC_FIX/pr-$after.json"
else
  cat "$PRC_FIX/$op.json"
fi
EOF
chmod +x "$BIN/gh"

# --- fixtures ------------------------------------------------------------------
# WITH: the viewer has pending review PRR_pend holding 2001 (also in thread
# PRRT_2) and 2002 (in no thread). Published 1001 is on page 1, 3001 on page 2.
WITH="$BASE/with-pending"
WITHOUT="$BASE/without-pending"
mkdir -p "$WITH" "$WITHOUT"

cat >"$WITH/pr.json" <<'EOF'
{"data":{"repository":{"pullRequest":{
  "id":"PR_1","headRefOid":"abc123",
  "reviews":{"nodes":[{"id":"PRR_pend","viewerDidAuthor":true,"comments":{"nodes":[
    {"id":"PRRC_2001","fullDatabaseId":"2001","path":"src/app.py","line":3,"originalLine":3,"state":"PENDING","body":"pending one"},
    {"id":"PRRC_2002","fullDatabaseId":"2002","path":"src/app.py","line":4,"originalLine":4,"state":"PENDING","body":"orphan pending"}
  ]}}]},
  "reviewThreads":{"pageInfo":{"hasNextPage":true,"endCursor":"C1"},"nodes":[
    {"id":"PRRT_1","comments":{"nodes":[
      {"id":"PRRC_1001","fullDatabaseId":"1001","path":"src/app.py","line":2,"originalLine":2,"state":"SUBMITTED","body":"first\tline\nsecond line","pullRequestReview":{"id":"PRR_pub"}}]}},
    {"id":"PRRT_2","comments":{"nodes":[
      {"id":"PRRC_2001","fullDatabaseId":"2001","path":"src/app.py","line":3,"originalLine":3,"state":"PENDING","body":"pending one","pullRequestReview":{"id":"PRR_pend"}}]}}
  ]}
}}}}
EOF
cat >"$WITH/pr-C1.json" <<'EOF'
{"data":{"repository":{"pullRequest":{
  "id":"PR_1","headRefOid":"abc123",
  "reviews":{"nodes":[]},
  "reviewThreads":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[
    {"id":"PRRT_3","comments":{"nodes":[
      {"id":"PRRC_3001","fullDatabaseId":"3001","path":"src/other.py","line":null,"originalLine":7,"state":"SUBMITTED","body":"outdated","pullRequestReview":{"id":"PRR_pub"}}]}}
  ]}
}}}}
EOF
cat >"$WITHOUT/pr.json" <<'EOF'
{"data":{"repository":{"pullRequest":{
  "id":"PR_1","headRefOid":"abc123",
  "reviews":{"nodes":[]},
  "reviewThreads":{"pageInfo":{"hasNextPage":false,"endCursor":null},"nodes":[
    {"id":"PRRT_1","comments":{"nodes":[
      {"id":"PRRC_1001","fullDatabaseId":"1001","path":"src/app.py","line":2,"originalLine":2,"state":"SUBMITTED","body":"first","pullRequestReview":{"id":"PRR_pub"}}]}}
  ]}
}}}}
EOF
for d in "$WITH" "$WITHOUT"; do
  cat >"$d/diff" <<'EOF'
diff --git a/src/app.py b/src/app.py
index 1111111..2222222 100644
--- a/src/app.py
+++ b/src/app.py
@@ -1,3 +1,4 @@
 line1
+added2
 line3
 line4
EOF
  echo '{"data":{"updatePullRequestReviewComment":{"pullRequestReviewComment":{"id":"X"}}}}' >"$d/update.json"
  echo '{"data":{"deletePullRequestReviewComment":{"pullRequestReviewComment":{"id":"X"}}}}' >"$d/delete.json"
  echo '{"data":{"addPullRequestReviewThreadReply":{"comment":{"id":"PRRC_5001","fullDatabaseId":"5001"}}}}' >"$d/reply.json"
  echo '{"data":{"addPullRequestReview":{"pullRequestReview":{"id":"PRR_new","state":"PENDING"}}}}' >"$d/start-review.json"
  echo '{"data":{"addPullRequestReviewThread":{"thread":{"id":"PRRT_9","comments":{"nodes":[{"id":"PRRC_6001","fullDatabaseId":"6001"}]}}}}}' >"$d/add-thread.json"
done

# A body that shell quoting would mangle.
BODY="$BASE/body.md"
cat >"$BODY" <<'EOF'
Use `jq -r '.x'` here, not "$(cat file)".
It's a 'quoted' \back\slash line; and a $HOME that must stay literal.

Trailing paragraph.
EOF
EMPTY="$BASE/empty.md"
: >"$EMPTY"

STATE="$BASE/state"
ALL_RAW="$BASE/all-raw"
: >"$ALL_RAW"

run() {
  # run <fixture-dir> <args...> — sets $out, $err, $rc, $calls.
  local fix="$1"
  shift
  rm -rf "$STATE"
  mkdir -p "$STATE"
  : >"$STATE/calls"
  : >"$STATE/raw"
  out="$(PATH="$BIN:$PATH" PRC_FIX="$fix" PRC_STATE="$STATE" "$SCRIPT" "$@" 2>"$STATE/stderr")"
  rc=$?
  err="$(cat "$STATE/stderr")"
  calls="$(cat "$STATE/calls")"
  cat "$STATE/raw" >>"$ALL_RAW"
}

# --- usage errors --------------------------------------------------------------
echo "usage errors"
run "$WITH"
assert_eq "no subcommand exits 2" "2" "$rc"
run "$WITH" frobnicate --pr 1 --repo test/repo
assert_eq "unknown subcommand exits 2" "2" "$rc"
run "$WITH" edit 1001 --repo test/repo --body-file "$BODY"
assert_eq "edit without --pr exits 2" "2" "$rc"
run "$WITH" edit abc --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "non-numeric databaseId exits 2" "2" "$rc"
run "$WITH" edit 1001 --pr 7 --repo test/repo
assert_eq "edit without --body-file exits 2" "2" "$rc"
run "$WITH" edit 1001 --pr 7 --repo test/repo --body-file "$BASE/missing.md"
assert_eq "unreadable body file exits 2" "2" "$rc"
run "$WITH" edit 1001 --pr 7 --repo test/repo --body-file "$EMPTY"
assert_eq "empty body file exits 2" "2" "$rc"
run "$WITH" delete --pr 7 --repo test/repo
assert_eq "delete without a databaseId exits 2" "2" "$rc"
run "$WITH" list --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "list rejects --body-file" "2" "$rc"
run "$WITH" add --pr 7 --repo test/repo --path src/app.py --line 2 --side MIDDLE --body-file "$BODY"
assert_eq "add with a bad --side exits 2" "2" "$rc"
run "$WITH" add --pr 7 --repo test/repo --path src/app.py --line 0 --body-file "$BODY"
assert_eq "add with --line 0 exits 2" "2" "$rc"
run "$WITH" add --pr 7 --repo test/repo --line 2 --body-file "$BODY"
assert_eq "add without --path exits 2" "2" "$rc"
run "$WITH" list --pr 7 --repo noslash
assert_eq "malformed --repo exits 2" "2" "$rc"
run "$WITH" list --pr
assert_eq "flag with no value exits 2" "2" "$rc"
assert_eq "usage errors make no gh call" "" "$(cat "$ALL_RAW")"

# --- list ----------------------------------------------------------------------
echo "list"
run "$WITH" list --pr 7 --repo test/repo
assert_eq "list exits 0" "0" "$rc"
assert_eq "list prints one line per distinct comment (pending duplicate collapsed)" "4" "$(printf '%s\n' "$out" | grep -c .)"
assert_contains "$out" "published comment row: tab-separated columns, first body line, tab flattened" \
  "$(printf '1001\tPRRC_1001\tsrc/app.py\t2\tSUBMITTED\tfirst line')"
assert_contains "$out" "pending comment row" "$(printf '2001\tPRRC_2001\tsrc/app.py\t3\tPENDING\tpending one')"
assert_contains "$out" "outdated comment on page 2 falls back to originalLine" \
  "$(printf '3001\tPRRC_3001\tsrc/other.py\t7\tSUBMITTED\toutdated')"
assert_contains "$calls" "second reviewThreads page requested with the cursor" "after=C1"
assert_contains "$calls" "PR query carries owner and name" "owner=test name=repo pr=7"
run "$WITH" list --pr 7 --repo test/repo --pending
assert_eq "--pending lists only pending comments" "2001 2002" "$(printf '%s\n' "$out" | cut -f1 | tr '\n' ' ' | sed 's/ $//')"
run "$WITH" list --pr 7
assert_eq "list without --repo exits 0" "0" "$rc"
assert_contains "$calls" "--repo defaults to gh repo view" "repo view"
assert_contains "$calls" "default repo is split into owner/name" "owner=test name=repo"

# --- edit ----------------------------------------------------------------------
echo "edit"
run "$WITH" edit 3001 --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "edit exits 0" "0" "$rc"
assert_eq "edit prints the documented line" "updated 3001" "$out"
assert_contains "$calls" "edit resolves a page-2 databaseId to its node ID" "update id=PRRC_3001 body=@file"
if cmp -s "$BODY" "$STATE/body.1"; then ok "edit sends the body file byte-for-byte"; else bad "edit body differs from the file"; fi
run "$WITH" edit 2002 --pr 7 --repo test/repo --body-file "$BODY"
assert_contains "$calls" "edit resolves a pending comment known only through its review" "update id=PRRC_2002"
assert_eq "pending edit prints the documented line" "updated 2002" "$out"
run "$WITH" edit 9999 --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "edit of an unknown databaseId exits 1" "1" "$rc"
assert_contains "$err" "unknown databaseId says so on stderr" "no review comment 9999"
assert_not_contains "$calls" "unknown databaseId sends no mutation" "update"

# --- delete --------------------------------------------------------------------
echo "delete"
run "$WITH" delete 1001 --pr 7 --repo test/repo
assert_eq "delete exits 0" "0" "$rc"
assert_eq "delete prints the documented line" "deleted 1001" "$out"
assert_contains "$calls" "delete sends deletePullRequestReviewComment with the node ID" "delete id=PRRC_1001"

# --- reply ---------------------------------------------------------------------
echo "reply"
run "$WITH" reply 1001 --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "reply exits 0" "0" "$rc"
assert_eq "reply prints the documented line" "replied 5001 to=1001" "$out"
assert_contains "$calls" "reply targets the comment's thread and the viewer's pending review" \
  "reply thread=PRRT_1 body=@file review=PRR_pend"
if cmp -s "$BODY" "$STATE/body.1"; then ok "reply sends the body file byte-for-byte"; else bad "reply body differs from the file"; fi
run "$WITHOUT" reply 1001 --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "reply with no pending review exits 0" "0" "$rc"
assert_not_contains "$calls" "reply with no pending review names none" "review="
run "$WITH" reply 2002 --pr 7 --repo test/repo --body-file "$BODY"
assert_eq "reply to a comment with no visible thread exits 1" "1" "$rc"
assert_not_contains "$calls" "threadless reply sends no mutation" "reply "

# --- add -----------------------------------------------------------------------
echo "add"
run "$WITH" add --pr 7 --repo test/repo --path src/app.py --line 2 --body-file "$BODY"
assert_eq "add with a pending review exits 0" "0" "$rc"
assert_eq "add prints the documented line" "added 6001 line=2" "$out"
assert_contains "$calls" "add checks the anchor against gh pr diff" "pr diff pr diff 7 --repo test/repo"
assert_not_contains "$calls" "add with a pending review never starts one" "start-review"
assert_contains "$calls" "add attaches the thread to the existing pending review" \
  "add-thread review=PRR_pend path=src/app.py line=2 side=RIGHT body=@file"
if cmp -s "$BODY" "$STATE/body.1"; then ok "add sends the body file byte-for-byte"; else bad "add body differs from the file"; fi

run "$WITHOUT" add --pr 7 --repo test/repo --path src/app.py --line 2 --body-file "$BODY"
assert_eq "add with no pending review exits 0" "0" "$rc"
assert_contains "$calls" "add with no pending review starts one on the head commit" \
  "start-review pullRequestId=PR_1 commitOID=abc123"
assert_contains "$calls" "add attaches the thread to the new pending review" "add-thread review=PRR_new"
assert_eq "add (new review) prints the documented line" "added 6001 line=2" "$out"

run "$WITHOUT" add --pr 7 --repo test/repo --path src/app.py --line 50 --body-file "$BODY"
assert_eq "add on an unanchored line exits 1" "1" "$rc"
assert_contains "$err" "unanchored add says so on stderr" "not on the right side"
assert_not_contains "$calls" "unanchored add starts no review" "start-review"
assert_not_contains "$calls" "unanchored add adds no thread" "add-thread"
assert_eq "unanchored add prints nothing on stdout" "" "$out"

run "$WITH" add --pr 7 --repo test/repo --path src/app.py --line 50 --side LEFT --body-file "$BODY"
assert_eq "add --side LEFT exits 0" "0" "$rc"
assert_not_contains "$calls" "add --side LEFT skips the diff fetch" "pr diff"
assert_contains "$err" "add --side LEFT notes the skipped check" "skipping the anchor check"
assert_contains "$calls" "add --side LEFT sends side=LEFT" "side=LEFT"

# A review GitHub opens in any state but PENDING is already published; the
# script must refuse to attach a comment to it.
PUBLISHED="$BASE/fix-published"
cp -R "$WITHOUT" "$PUBLISHED"
echo '{"data":{"addPullRequestReview":{"pullRequestReview":{"id":"PRR_new","state":"COMMENTED"}}}}' >"$PUBLISHED/start-review.json"
run "$PUBLISHED" add --pr 7 --repo test/repo --path src/app.py --line 2 --body-file "$BODY"
assert_eq "add refuses a new review that is not PENDING" "1" "$rc"
assert_contains "$err" "non-pending refusal says so on stderr" "not PENDING"
assert_not_contains "$calls" "non-pending review gets no thread" "add-thread"
assert_eq "non-pending refusal prints nothing on stdout" "" "$out"

# --- never submit --------------------------------------------------------------
echo "never submit"
assert_not_contains "$(cat "$ALL_RAW")" "no gh call in any test submitted a review" "submitPullRequestReview"
assert_not_contains "$(cat "$ALL_RAW")" "no gh call passed a review event" "event"

echo
echo "test-pr-review-comments: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-pr-review-comments: OK"
