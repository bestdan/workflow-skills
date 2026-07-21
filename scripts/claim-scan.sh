#!/usr/bin/env bash
# claim-scan.sh — the single, tested implementation of the claim/WIP query.
#
# The whole-line `Claims-task: <slug>` matching rule was restated by hand in
# repo-pr-execute.md (the Claim protocol, the WIP count, the remote-dispatch
# prompt), do-tasks.md, and doctor.md's stale-claim check — four independent
# spots that could each re-derive the check differently. One of those
# restatements already had to call out, explicitly, that a NAIVE substring
# test lets slug `task_1` falsely match a `Claims-task: task_13` line. This
# script is the ONE place that logic lives for every ORCHESTRATOR-SIDE
# consumer, so the bug can only be introduced (or fixed) once.
#
# The remote-dispatch prompt (repo-pr-execute.md's `claude --remote "..."`
# block) is the one exception: the remote VM does not have this plugin
# installed, so it cannot call this script and MUST keep its inline prose
# copy of the same rule. Do not wire that block to this script.
#
# Usage:
#   scripts/claim-scan.sh [--repo <owner/name>] [--task-dir <dir>] \
#     [--gh <path>] [--limit <n>]
#
#   --repo      owner/name passed to `gh pr list --repo`. Default: gh infers
#               it from the current directory's git remote.
#   --task-dir  Directory to scan (recursively) for task files with
#               `status: in_progress`, used for WIP dedupe and the
#               stale-claim heuristic. Default: dev_docs/tasks (not a
#               git-root lookup — pass the resolved path, as task-scan.py
#               documents for the same reason).
#   --gh        Path to the `gh` binary. Default: gh on PATH. Mockable —
#               tests point this at a stub so the suite is hermetic.
#   --limit     `gh pr list --limit`. Default: 100 (an active repo's open
#               PRs must not be truncated past the default 30 — a missed
#               claim marker would let a second agent double-claim).
#
# Output — one line per record, stable "key=value" field order, parseable:
#
#   CLAIMED label=<task-claim|task-loop|task-blocked> pr=<n> slug=<slug> \
#     match=<body|branch> updated=<ISO8601>
#     One line per open PR (across the three labels) that claims a slug, via
#     a whole-line `Claims-task: <slug>` body marker OR headRefName ==
#     task/<slug>. A labeled PR with neither signal is omitted — it isn't
#     recognized as a valid claim by the protocol either.
#
#   STALE pr=<n> slug=<slug> updated=<ISO8601>
#     A task-claim CLAIMED PR whose slug has no matching --task-dir file at
#     status: in_progress. HEURISTIC, not proof: a fresh clone of `main`
#     cannot see another session's status flip (it lives on that session's
#     unmerged branch — see the Claim protocol in repo-pr-execute.md), so
#     this signal is only conclusive for a same-checkout scan. Consumers
#     (doctor's stale-claim check) should corroborate with the `updated=`
#     age before treating a STALE hit as abandoned, not act on it alone.
#
#   WIP_COUNT: <n>
#   WIP_SLUGS: <comma-separated sorted unique slugs, empty if none>
#     Distinct in-flight slugs, deduped across open task-claim PRs, open
#     task-loop PRs, and --task-dir files at status: in_progress.
#     task-blocked is EXCLUDED — a blocked claim is parked for a human, not
#     "in flight" (matches repo-pr-execute.md step 4.2).
#
# Consumers (all orchestrator-side; see the module comment above for the one
# exception):
#   - WIP count (repo-pr-execute.md step 4.2): read WIP_COUNT.
#   - Pre-claim check (Claim protocol step 1, and --local): grep CLAIMED for
#     " slug=<candidate> " (the trailing space matters — see the whole-line
#     note below).
#   - doctor's stale-claim check: read STALE lines.
#
# Exit status: 0 on a completed scan (even with zero claims/WIP). Non-zero
# only on a hard failure (missing gh/jq, a `gh pr list` error, a bad --limit).
set -uo pipefail

die() {
  echo "claim-scan: $*" >&2
  exit 2
}

repo=""
task_dir="dev_docs/tasks"
gh_bin="gh"
limit=100

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      repo="$2"
      shift 2
      ;;
    --task-dir)
      task_dir="$2"
      shift 2
      ;;
    --gh)
      gh_bin="$2"
      shift 2
      ;;
    --limit)
      limit="$2"
      shift 2
      ;;
    -h | --help)
      sed -n '2,60p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $1" ;;
  esac
done

# Non-integer limit would make `gh pr list --limit` fail opaquely — reject up
# front with an actionable message instead.
case "$limit" in *[!0-9]* | "") die "--limit must be a non-negative integer" ;; esac
command -v "$gh_bin" >/dev/null 2>&1 || die "gh CLI ('$gh_bin') is required but not found in PATH"
command -v jq >/dev/null 2>&1 || die "jq is required but not found in PATH"

repo_args=()
[ -n "$repo" ] && repo_args=(--repo "$repo")

# Whole-line `Claims-task:` extraction. `^...$` anchors BOTH ends against a
# TRIMMED line, so it captures the exact marker text and nothing else — this
# is why `task_1` and `task_13` can never bleed into each other. The slug
# extracted from a "Claims-task: task_13" line is the literal string
# "task_13", full stop; no downstream comparison ever tests one slug as a
# substring of another — every check below is a whole-line/whole-token
# equality (grep -Fxq / exact "slug=<x> " match), never `grep -F`/`contains`
# against the raw body.
read -r -d '' jq_filter <<'JQ' || true
def trimmed_lines:
  (.body // "") | split("\n") | map(sub("^[ \t\r]+"; "") | sub("[ \t\r]+$"; ""));
def slug_from_body:
  (trimmed_lines | map(select(test("^Claims-task: .+$"))))
  | if length > 0 then (.[0] | sub("^Claims-task: "; "")) else null end;
def slug_from_branch:
  (.headRefName // "") as $h
  | if ($h | test("^task/.+$")) then ($h | sub("^task/"; "")) else null end;
.[]
| (slug_from_body) as $bs
| (slug_from_branch) as $rs
| ($bs // $rs) as $slug
| select($slug != null)
| "CLAIMED label=" + $label + " pr=" + (.number | tostring) + " slug=" + $slug
  + " match=" + (if $bs != null then "body" else "branch" end)
  + " updated=" + (.updatedAt // "")
JQ

claimed_for_label() {
  # claimed_for_label <label> — emits one CLAIMED line per claiming PR.
  local label="$1" json
  json="$("$gh_bin" pr list "${repo_args[@]+"${repo_args[@]}"}" --state open --label "$label" \
    --limit "$limit" --json number,headRefName,body,updatedAt)" \
    || die "gh pr list --label $label failed"
  printf '%s' "$json" | jq -r --arg label "$label" "$jq_filter"
}

# `die` inside claimed_for_label runs in this command-substitution subshell, so
# its exit only kills the subshell — propagate a gh/jq failure to the top-level
# process (`|| exit $?`) so a failed `gh pr list` is a hard, non-zero failure
# (the documented fallback in repo-pr-execute.md), never a silent WIP_COUNT: 0.
claim_lines="$(claimed_for_label task-claim)" || exit $?
loop_lines="$(claimed_for_label task-loop)" || exit $?
blocked_lines="$(claimed_for_label task-blocked)" || exit $?

# --- in_progress task files (WIP dedupe + the stale-claim heuristic) ---
in_progress_slugs=""
if [ -n "$task_dir" ] && [ -d "$task_dir" ]; then
  while IFS= read -r -d '' file; do
    # Read only the frontmatter block (between the first two `---` lines) so
    # a `status:` mention in the task body can never masquerade as the field.
    fm="$(awk 'NR==1 && $0=="---"{f=1;next} f && $0=="---"{exit} f{print}' "$file")"
    status="$(printf '%s\n' "$fm" | sed -n 's/^status:[[:space:]]*//p' | head -n1 \
      | sed -e "s/^[\"']//" -e "s/[\"']\$//" -e 's/[[:space:]]*$//')"
    if [ "$status" = "in_progress" ]; then
      slug="$(basename "$file" .md)"
      in_progress_slugs="${in_progress_slugs}${slug}
"
    fi
  done < <(find "$task_dir" -name '*.md' -not -path '*/_archive/*' -print0)
fi

# --- Emit CLAIMED lines (all three labels) ---
[ -n "$claim_lines" ] && printf '%s\n' "$claim_lines"
[ -n "$loop_lines" ] && printf '%s\n' "$loop_lines"
[ -n "$blocked_lines" ] && printf '%s\n' "$blocked_lines"

# --- STALE: task-claim PRs with no matching in_progress file ---
while IFS= read -r line; do
  [ -z "$line" ] && continue
  slug="$(printf '%s\n' "$line" | sed -n 's/^.* slug=\([^ ]*\) match=.*$/\1/p')"
  pr="$(printf '%s\n' "$line" | sed -n 's/^.* pr=\([^ ]*\) slug=.*$/\1/p')"
  updated="$(printf '%s\n' "$line" | sed -n 's/^.* updated=\(.*\)$/\1/p')"
  # Whole-line match against the in_progress slug list — grep -Fxq, not a
  # substring test, for the same task_1/task_13 reason as the extraction
  # above.
  if ! printf '%s\n' "$in_progress_slugs" | grep -Fxq "$slug"; then
    echo "STALE pr=$pr slug=$slug updated=$updated"
  fi
done <<EOF
$claim_lines
EOF

# --- WIP: distinct slugs across task-claim + task-loop + in_progress files ---
extract_slugs() {
  printf '%s\n' "$1" | sed -n 's/^.* slug=\([^ ]*\) match=.*$/\1/p'
}

wip_slugs="$(
  {
    extract_slugs "$claim_lines"
    extract_slugs "$loop_lines"
    printf '%s\n' "$in_progress_slugs"
  } | sed '/^$/d' | sort -u
)"
wip_count=0
[ -n "$wip_slugs" ] && wip_count="$(printf '%s\n' "$wip_slugs" | sed '/^$/d' | wc -l | tr -d ' ')"

echo "WIP_COUNT: $wip_count"
wip_csv="$(printf '%s\n' "$wip_slugs" | sed '/^$/d' | tr '\n' ',' | sed 's/,$//')"
echo "WIP_SLUGS: $wip_csv"
