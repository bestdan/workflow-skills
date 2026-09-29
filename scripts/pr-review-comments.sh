#!/usr/bin/env bash
# pr-review-comments.sh — list, edit, delete, add, and reply to PR review
# comments by databaseId, the same way whether a comment is pending (in an
# unsubmitted review) or published.
#
# Why this exists: REST cannot see a pending comment. GET, PATCH and DELETE on
# repos/<o>/<r>/pulls/comments/<id> all 404 while its review is pending, and
# REST cannot add a comment to an existing pending review. GraphQL can do all
# of it, but only by node ID. This script resolves the databaseId a caller
# holds to the node IDs GraphQL needs, so the caller never handles one.
#
# Usage:
#   scripts/pr-review-comments.sh list   --pr <n> [--pending] [--repo <o/r>]
#   scripts/pr-review-comments.sh edit   <databaseId> --pr <n> --body-file <f> [--repo <o/r>]
#   scripts/pr-review-comments.sh delete <databaseId> --pr <n> [--repo <o/r>]
#   scripts/pr-review-comments.sh add    --pr <n> --path <p> --line <l> [--side RIGHT|LEFT]
#                                        --body-file <f> [--repo <o/r>]
#   scripts/pr-review-comments.sh reply  <databaseId> --pr <n> --body-file <f> [--repo <o/r>]
#
#   --pr         PR number. Required everywhere: a databaseId is found by
#                searching that PR's review threads, since GitHub has no
#                lookup by databaseId that sees pending comments.
#   --repo       owner/name. Default: `gh repo view` of the current directory.
#   --pending    list: only comments in a pending review.
#   --path       add: file path in the PR's head tree.
#   --line       add: line number on --side.
#   --side       add: RIGHT (new file, the default) or LEFT (old file). The
#                anchor pre-check (below) runs only for RIGHT.
#   --body-file  edit/add/reply: the comment body, read verbatim from this file
#                (backticks, quotes and newlines survive). A body is never
#                taken from the command line.
#
# stdout — one parseable line per action, nothing else:
#   list    one line per comment, tab-separated columns:
#             databaseId  nodeId  path  line  state  first-line-of-body
#           line is "-" when GitHub has none (e.g. an outdated comment); state
#           is PENDING or SUBMITTED; tabs in the body become spaces.
#   edit    updated <databaseId>
#   delete  deleted <databaseId>
#   add     added <newDatabaseId> line=<l>
#   reply   replied <newDatabaseId> to=<parentDatabaseId>
#
# Exit status: 0 ok; 1 operation failed, comment not found, or the add anchor
# is not in the diff; 2 usage error or missing dependency. Errors go to stderr.
#
# Behaviour worth knowing:
#   - It never submits a review. `add` attaches to the viewer's pending review
#     when there is one, and otherwise opens a new one (addPullRequestReview
#     with no event, which GitHub leaves PENDING) and adds the thread to it.
#     `reply` likewise lands in the viewer's pending review when one exists.
#   - `add` with --side RIGHT fetches `gh pr diff` and runs
#     scripts/diff-anchor-check.py first; an unanchored line exits 1 before any
#     mutation is sent. LEFT skips the check (the checker knows only the right
#     side) and says so on stderr.
#   - Lookup pages the PR's reviewThreads 100 at a time and reads each
#     thread's first 100 comments, plus the first 100 comments of each of the
#     viewer's pending reviews. A comment past the 100th in one thread is not
#     found. A pending comment found only through its review (no thread) can
#     be edited or deleted but not replied to.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ANCHOR_CHECK="$SCRIPT_DIR/diff-anchor-check.py"

usage() {
  echo "pr-review-comments: $*" >&2
  echo "usage: pr-review-comments.sh list|edit|delete|add|reply ... (see the header of this script)" >&2
  exit 2
}

fail() {
  echo "pr-review-comments: $*" >&2
  exit 1
}

command -v jq >/dev/null 2>&1 || usage "jq is required but not found in PATH"
command -v gh >/dev/null 2>&1 || usage "gh is required but not found in PATH"

# --- GraphQL documents ---------------------------------------------------------

# One page of review threads, plus the PR's node ID, head commit and the
# viewer's pending reviews (a pending review is visible only to its author).
PR_QUERY='query($owner: String!, $name: String!, $pr: Int!, $after: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      id
      headRefOid
      reviews(states: PENDING, first: 100) {
        nodes {
          id
          viewerDidAuthor
          comments(first: 100) {
            nodes { id fullDatabaseId path line originalLine state body }
          }
        }
      }
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          comments(first: 100) {
            nodes {
              id fullDatabaseId path line originalLine state body
              pullRequestReview { id }
            }
          }
        }
      }
    }
  }
}'

UPDATE_MUTATION='mutation($id: ID!, $body: String!) {
  updatePullRequestReviewComment(input: {pullRequestReviewCommentId: $id, body: $body}) {
    pullRequestReviewComment { id }
  }
}'

DELETE_MUTATION='mutation($id: ID!) {
  deletePullRequestReviewComment(input: {id: $id}) {
    pullRequestReviewComment { id }
  }
}'

START_REVIEW_MUTATION='mutation($pullRequestId: ID!, $commitOID: GitObjectID) {
  addPullRequestReview(input: {pullRequestId: $pullRequestId, commitOID: $commitOID}) {
    pullRequestReview { id state }
  }
}'

ADD_THREAD_MUTATION='mutation($review: ID!, $path: String!, $line: Int!, $side: DiffSide!, $body: String!) {
  addPullRequestReviewThread(input: {pullRequestReviewId: $review, path: $path, line: $line, side: $side, body: $body}) {
    thread { id comments(first: 1) { nodes { id fullDatabaseId } } }
  }
}'

REPLY_MUTATION='mutation($thread: ID!, $review: ID, $body: String!) {
  addPullRequestReviewThreadReply(input: {pullRequestReviewThreadId: $thread, pullRequestReviewId: $review, body: $body}) {
    comment { id fullDatabaseId }
  }
}'

# --- argument parsing ----------------------------------------------------------

[ $# -ge 1 ] || usage "missing subcommand"
cmd="$1"
shift
case "$cmd" in
  list) allowed="--pr --repo --pending" ;;
  edit) allowed="--pr --repo --body-file" ;;
  delete) allowed="--pr --repo" ;;
  add) allowed="--pr --repo --path --line --side --body-file" ;;
  reply) allowed="--pr --repo --body-file" ;;
  -h | --help)
    sed -n '2,/^set -uo pipefail$/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *) usage "unknown subcommand '$cmd'" ;;
esac

pr=""
repo=""
pending_only=0
path=""
line=""
side="RIGHT"
body_file=""
target=""

while [ $# -gt 0 ]; do
  flag="$1"
  case "$flag" in
    -*)
      case " $allowed " in
        *" $flag "*) ;;
        *) usage "$cmd does not take $flag" ;;
      esac
      ;;
  esac
  case "$flag" in
    --pending)
      pending_only=1
      shift
      ;;
    --pr | --repo | --path | --line | --side | --body-file)
      [ $# -ge 2 ] || usage "missing value for $flag"
      case "$flag" in
        --pr) pr="$2" ;;
        --repo) repo="$2" ;;
        --path) path="$2" ;;
        --line) line="$2" ;;
        --side) side="$2" ;;
        --body-file) body_file="$2" ;;
      esac
      shift 2
      ;;
    *)
      [ -z "$target" ] || usage "unexpected argument '$flag'"
      target="$flag"
      shift
      ;;
  esac
done

is_uint() {
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

[ -n "$pr" ] || usage "$cmd requires --pr"
is_uint "$pr" || usage "--pr must be a number, got '$pr'"

case "$cmd" in
  edit | delete | reply)
    [ -n "$target" ] || usage "$cmd requires a comment databaseId"
    is_uint "$target" || usage "databaseId must be a number, got '$target'"
    ;;
  *) [ -z "$target" ] || usage "unexpected argument '$target'" ;;
esac

case "$cmd" in
  edit | add | reply)
    [ -n "$body_file" ] || usage "$cmd requires --body-file"
    [ -f "$body_file" ] && [ -r "$body_file" ] || usage "cannot read body file '$body_file'"
    [ -s "$body_file" ] || usage "body file '$body_file' is empty"
    ;;
esac

if [ "$cmd" = add ]; then
  [ -n "$path" ] || usage "add requires --path"
  [ -n "$line" ] || usage "add requires --line"
  is_uint "$line" && [ "$line" -gt 0 ] || usage "--line must be a positive number, got '$line'"
  case "$side" in
    RIGHT | LEFT) ;;
    *) usage "--side must be RIGHT or LEFT, got '$side'" ;;
  esac
fi

if [ -z "$repo" ]; then
  repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)" || fail "could not determine the repo; pass --repo"
fi
case "$repo" in
  */*) ;;
  *) usage "--repo must be owner/name, got '$repo'" ;;
esac
owner="${repo%%/*}"
name="${repo#*/}"
[ -n "$owner" ] && [ -n "$name" ] && [ "${name#*/}" = "$name" ] || usage "--repo must be owner/name, got '$repo'"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pr-review-comments.XXXXXX")" || fail "could not create a temp dir"
trap 'rm -rf "$WORK"' EXIT

# --- PR lookup -----------------------------------------------------------------

PR_ID=""
HEAD_OID=""
PENDING_REVIEW=""

# Fetch every review comment on the PR into $WORK/comments.json, an array of
# {db, id, path, line, state, body, thread, review}, and set PR_ID, HEAD_OID
# and PENDING_REVIEW (the viewer's pending review node ID, or empty).
fetch_pr() {
  local after="" page=0 has_next
  local -a args
  : >"$WORK/raw.jsonl"
  while :; do
    args=(api graphql -f query="$PR_QUERY" -f owner="$owner" -f name="$name" -F pr="$pr")
    [ -z "$after" ] || args+=(-f after="$after")
    gh "${args[@]}" >"$WORK/page.json" || fail "could not read review threads of $repo#$pr"
    jq -e '.data.repository.pullRequest.id' "$WORK/page.json" >/dev/null 2>&1 \
      || fail "no pull request $repo#$pr"
    if [ "$page" -eq 0 ]; then
      PR_ID="$(jq -r '.data.repository.pullRequest.id' "$WORK/page.json")"
      HEAD_OID="$(jq -r '.data.repository.pullRequest.headRefOid // empty' "$WORK/page.json")"
      PENDING_REVIEW="$(jq -r '[.data.repository.pullRequest.reviews.nodes[]? | select(.viewerDidAuthor)][0].id // empty' "$WORK/page.json")"
      jq -c '.data.repository.pullRequest.reviews.nodes[]? | select(.viewerDidAuthor) | .id as $r
        | .comments.nodes[]
        | {db: (.fullDatabaseId | tostring), id, path, line: (.line // .originalLine),
           state, body, thread: null, review: $r}' "$WORK/page.json" >>"$WORK/raw.jsonl" \
        || fail "unexpected response shape from GitHub"
    fi
    jq -c '.data.repository.pullRequest.reviewThreads.nodes[] | .id as $t
      | .comments.nodes[]
      | {db: (.fullDatabaseId | tostring), id, path, line: (.line // .originalLine),
         state, body, thread: $t, review: .pullRequestReview.id}' "$WORK/page.json" >>"$WORK/raw.jsonl" \
      || fail "unexpected response shape from GitHub"
    has_next="$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.hasNextPage' "$WORK/page.json")"
    [ "$has_next" = true ] || break
    after="$(jq -r '.data.repository.pullRequest.reviewThreads.pageInfo.endCursor' "$WORK/page.json")"
    page=$((page + 1))
  done
  # A pending comment can arrive twice (its thread and its review); keep the
  # record that knows its thread.
  jq -s 'group_by(.db) | map(sort_by(.thread == null) | .[0]) | sort_by(.path, (.line // 0), (.db | tonumber))' \
    "$WORK/raw.jsonl" >"$WORK/comments.json" || fail "could not collate review comments"
}

# find_comment <databaseId> — sets C_ID, C_THREAD; exits 1 when absent.
find_comment() {
  local rec
  rec="$(jq -c --arg db "$1" '[.[] | select(.db == $db)][0] // empty' "$WORK/comments.json")"
  [ -n "$rec" ] || fail "no review comment $1 on $repo#$pr"
  C_ID="$(jq -r '.id' <<<"$rec")"
  C_THREAD="$(jq -r '.thread // empty' <<<"$rec")"
}

# --- subcommands -----------------------------------------------------------------

fetch_pr

case "$cmd" in
  list)
    jq -r --argjson pending "$pending_only" '.[]
      | select($pending == 0 or .state == "PENDING")
      | [.db, .id, .path, (.line // "-" | tostring), .state,
         ((.body // "") | split("\n")[0] | gsub("\r"; "") | gsub("\t"; " "))]
      | join("\t")' "$WORK/comments.json" || fail "could not format the comment list"
    ;;

  edit)
    find_comment "$target"
    gh api graphql -f query="$UPDATE_MUTATION" -f id="$C_ID" -F body=@"$body_file" >"$WORK/out.json" \
      || fail "update of comment $target failed"
    jq -e '.data.updatePullRequestReviewComment.pullRequestReviewComment.id' "$WORK/out.json" >/dev/null 2>&1 \
      || fail "update of comment $target returned no comment"
    echo "updated $target"
    ;;

  delete)
    find_comment "$target"
    gh api graphql -f query="$DELETE_MUTATION" -f id="$C_ID" >"$WORK/out.json" \
      || fail "delete of comment $target failed"
    echo "deleted $target"
    ;;

  reply)
    find_comment "$target"
    [ -n "$C_THREAD" ] || fail "comment $target has no review thread visible on $repo#$pr; cannot reply"
    args=(api graphql -f query="$REPLY_MUTATION" -f thread="$C_THREAD" -F body=@"$body_file")
    [ -z "$PENDING_REVIEW" ] || args+=(-f review="$PENDING_REVIEW")
    gh "${args[@]}" >"$WORK/out.json" || fail "reply to comment $target failed"
    new_db="$(jq -r '.data.addPullRequestReviewThreadReply.comment.fullDatabaseId // empty | tostring' "$WORK/out.json")"
    [ -n "$new_db" ] || fail "reply to comment $target returned no comment"
    echo "replied $new_db to=$target"
    ;;

  add)
    if [ "$side" = RIGHT ]; then
      command -v python3 >/dev/null 2>&1 || usage "python3 is required for the anchor check"
      gh pr diff "$pr" --repo "$repo" >"$WORK/pr.diff" || fail "could not fetch the diff of $repo#$pr"
      jq -n --arg path "$path" --argjson line "$line" --rawfile body "$body_file" \
        '[{path: $path, line: $line, body: $body}]' >"$WORK/candidate.json"
      python3 "$ANCHOR_CHECK" --diff "$WORK/pr.diff" <"$WORK/candidate.json" >"$WORK/anchor.json" \
        || fail "anchor check failed to run"
      anchored="$(jq -r '.anchored | length' "$WORK/anchor.json")"
      [ "$anchored" = 1 ] || fail "$path:$line is not on the right side of any hunk in $repo#$pr; nothing sent"
    else
      echo "pr-review-comments: --side LEFT: skipping the anchor check (it knows only the right side)" >&2
    fi

    review="$PENDING_REVIEW"
    if [ -z "$review" ]; then
      args=(api graphql -f query="$START_REVIEW_MUTATION" -f pullRequestId="$PR_ID")
      [ -z "$HEAD_OID" ] || args+=(-f commitOID="$HEAD_OID")
      gh "${args[@]}" >"$WORK/review.json" || fail "could not start a pending review on $repo#$pr"
      review="$(jq -r '.data.addPullRequestReview.pullRequestReview.id // empty' "$WORK/review.json")"
      state="$(jq -r '.data.addPullRequestReview.pullRequestReview.state // empty' "$WORK/review.json")"
      [ -n "$review" ] || fail "starting a review on $repo#$pr returned no review"
      [ "$state" = PENDING ] || fail "new review $review on $repo#$pr is $state, not PENDING; not adding to it"
    fi
    gh api graphql -f query="$ADD_THREAD_MUTATION" -f review="$review" -f path="$path" \
      -F line="$line" -f side="$side" -F body=@"$body_file" >"$WORK/out.json" \
      || fail "adding a comment at $path:$line failed (pending review $review)"
    new_db="$(jq -r '.data.addPullRequestReviewThread.thread.comments.nodes[0].fullDatabaseId // empty | tostring' "$WORK/out.json")"
    [ -n "$new_db" ] || fail "adding a comment at $path:$line returned no comment (pending review $review)"
    echo "added $new_db line=$line"
    ;;
esac
