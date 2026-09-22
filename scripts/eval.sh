#!/usr/bin/env bash
# Behavioral skill-triggering eval harness (opt-in, non-blocking).
#
# For each row in evals/manifest.tsv, run the naive prompt through the claude
# CLI headless and assert Claude auto-invoked the expected Skill. Adapted from
# obra/superpowers' tests/skill-triggering. This checks *routing* (did the right
# skill fire), not output quality — see evals/README.md for the LLM-judge
# extension point.
#
# Costs API tokens and is nondeterministic — that's why it's flag-gated
# (`scripts/check.sh --with-evals` / `just eval`) and never a blocking PR check.
# Needs an authenticated claude CLI: in CI via the ANTHROPIC_API_KEY secret;
# locally a logged-in CLI (OAuth) works too.
#
# Usage: scripts/eval.sh [skill ...]   # default: every row in the manifest
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

MANIFEST="evals/manifest.tsv"
MAX_TURNS_DEFAULT=6
PLUGIN_NAME="workflow-skills"
# Per-row wall clock. Named because a row killed here still reports a routing
# verdict, and whether it passed depended on when the Skill call happened to
# land — so the number has to be visible in the summary, not buried in a flag.
CAP=300

# A failing row used to delete its own run log, so `skills invoked: none` was
# the entire record of the failure — and the suite is nondeterministic, so
# re-running does not reproduce the row that failed. Keep the log of every miss
# under an ignored directory (temp/ is in .gitignore) and print the path.
RUN_DIR="temp/evals/$(date +%Y%m%d-%H%M%S)"

command -v claude >/dev/null || {
  echo "✘ claude CLI not found on PATH" >&2
  exit 2
}
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  echo "note: ANTHROPIC_API_KEY not set — relying on a logged-in claude CLI." >&2
fi

# `timeout` is GNU coreutils — present on Linux/CI, but not on a stock macOS
# (where it's `gtimeout` via `brew install coreutils`, if at all). Find whichever
# exists; if neither does, run without a wall-clock cap rather than failing every
# row with "command not found".
TIMEOUT="$(command -v timeout || command -v gtimeout || true)"
if [[ -z "$TIMEOUT" ]]; then
  echo "note: no timeout/gtimeout on PATH — running without a per-eval time cap." >&2
fi

pass=0
fail=0
failed=()
causes=()
truncated=()
dirtied=()
dirty_count=0

# Each case runs against a writable checkout with permissions skipped, so a
# realistic prompt can write into the repo under test — and one did: the
# review-facts case wrote a report under skills/analysis-pipeline/example/ and
# staged it, while the suite reported the row as a pass. Detect that and fail
# the row, so residue is never folded silently into whatever is in flight.
#
# Compared per case against a rolling baseline rather than against "clean".
# The suite is routinely run from a worktree with work already in progress, so
# only the delta a case introduces is the harness's doing; blaming a row for
# pre-existing edits would make the check useless exactly where it is needed.
# A ROOT that is not a git checkout yields empty on both sides, so this
# degrades to a no-op rather than failing every row.
#
# Two signals, because `git status --porcelain` reports status codes and paths
# but not content. A case that edits a file already showing as ` M` leaves the
# porcelain line byte-identical, and that is the likeliest miss here: the
# rolling baseline exists to support running with work in flight, which is
# exactly when paths are already dirty. The content hash catches it.
#
# `-uall` is load-bearing, not tidiness. By default porcelain collapses an
# untracked directory to a single `?? dir/` entry, so a second file written
# inside it leaves the output byte-identical — measured, not assumed — and
# `diff HEAD` does not see untracked content at all. That is the shape of the
# case actually observed (a generated report written into a directory), and it
# would have been invisible had the target directory been untracked. `-uall`
# lists untracked files individually, so any new file moves this signal.
#
# Known limit, now narrow: rewriting a file that was *already* in the untracked
# list moves neither signal, since its `?? path` entry is unchanged and
# `diff HEAD` skips it.
tree_paths() {
  git -C "$ROOT" status --porcelain -uall 2>/dev/null
}
tree_hash() {
  git -C "$ROOT" diff HEAD 2>/dev/null | shasum 2>/dev/null
}
baseline_paths="$(tree_paths)"
baseline_hash="$(tree_hash)"

while IFS=$'\t' read -r skill prompt_file max_turns; do
  [[ -z "${skill// /}" || "$skill" == \#* ]] && continue
  # optional skill filter from argv. A row's first field may name several
  # accepted skills separated by `|` (see scripts/eval-triage.py), so the
  # filter matches if argv names ANY of them — `scripts/eval.sh task` must
  # still select an `add-task|task` row.
  if [[ -n "${1:-}" ]] \
    && ! grep -qxF -f <(printf '%s\n' "$@") <<<"${skill//|/$'\n'}"; then
    continue
  fi
  # `|` is legal in a filename but reads as a pipe everywhere else.
  slug="${skill//|/-or-}"
  mt="${max_turns:-$MAX_TURNS_DEFAULT}"
  prompt="$(cat "evals/$prompt_file")"
  log="$(mktemp)"
  echo "→ ${skill}  (prompt: ${prompt_file}, max-turns: ${mt})"
  started=$SECONDS
  ${TIMEOUT:+"$TIMEOUT" "$CAP"} claude -p "$prompt" \
    --plugin-dir "$ROOT" \
    --dangerously-skip-permissions \
    --max-turns "$mt" \
    --output-format stream-json --verbose \
    >"$log" 2>&1
  rc=$?
  elapsed=$((SECONDS - started))

  after_paths="$(tree_paths)"
  after_hash="$(tree_hash)"
  residue=""
  if [[ "$after_paths" != "$baseline_paths" || "$after_hash" != "$baseline_hash" ]]; then
    # Symmetric (comm -3), not just additions: a case that *reverts* an entry
    # has also written to the checkout, and reading that as clean would let it
    # delete work someone had in flight. comm prefixes its second column with a
    # tab, which the sed strips.
    residue="$(comm -3 <(printf '%s\n' "$baseline_paths" | sort -u) \
      <(printf '%s\n' "$after_paths" | sort -u) | sed -e 's/^\t//' -e '/^$/d')"
    if [[ -z "$residue" ]]; then
      residue="(content changed under a path that was already modified)"
    fi
    # Roll the baseline forward either way, so one dirtying case does not
    # convict every row after it.
    baseline_paths="$after_paths"
    baseline_hash="$after_hash"
  fi

  # The routing verdict and, on a miss, the cause. Parsed from the event stream
  # rather than grepped: the old `"(skill|name)":"<skill>"` pattern matched the
  # name anywhere in the log, and a bare `skills invoked: none` could not tell a
  # killed run from a clean run that chose nothing. See scripts/eval-triage.py.
  #
  # Run unconditionally, ahead of the residue branch, so a row that dirtied the
  # checkout still reports what it routed to. That verdict is free once the log
  # exists, and a residue failure is the case where knowing what the case was
  # *trying* to do matters most.
  triage="$(python3 scripts/eval-triage.py \
    --log "$log" --skill "$skill" --rc "$rc" --plugin "$PLUGIN_NAME")"
  triage_rc=$?

  if [[ -n "$residue" ]]; then
    # Reported ahead of the routing verdict on purpose: a case that wrote into
    # the checkout has already broken the run, because a later case sees a tree
    # this one changed. Whether it also picked the right skill is beside that.
    echo "  ❌ FAIL — wrote into the repo under test:"
    printf '%s\n' "$residue" | sed 's/^/       /'
    echo "$triage" | sed 's/^  [❌✅⚠]/     also/'
    fail=$((fail + 1))
    failed+=("$skill")
    dirtied+=("$skill")
    dirty_count=$((dirty_count + 1))
    causes+=("dirtied-repo")
    keep_log=1
  else
    echo "$triage"
    if [[ $triage_rc -eq 0 ]]; then
      pass=$((pass + 1))
      # A pass on a truncated run is a latent failure, not a result: the row was
      # killed and passed only because the Skill call happened to land before
      # the kill. Keep that log too, or the population the misses are drawn from
      # is invisible and the failure rate cannot be measured.
      if grep -q '⚠' <<<"$triage"; then
        truncated+=("$skill")
        keep_log=1
      else
        keep_log=0
      fi
    else
      fail=$((fail + 1))
      failed+=("$skill")
      causes+=("$(sed -n 's/.*(cause: \([a-z-]*\)).*/\1/p' <<<"$triage" | head -1)")
      keep_log=1
    fi
  fi
  echo "     ${elapsed}s"

  if [[ $keep_log -eq 1 ]]; then
    # RUN_DIR is under temp/, which .gitignore covers — and that is load-bearing
    # rather than tidiness: the tree guard above reads `git status --porcelain`,
    # which does not list ignored paths, so writing a log here cannot make the
    # harness convict the next row of the harness's own residue. Moving this
    # directory anywhere tracked would.
    mkdir -p "$RUN_DIR"
    kept="$RUN_DIR/${slug}-$(basename "$prompt_file" .txt).jsonl"
    mv "$log" "$kept"
    echo "     log: $kept"
  else
    rm -f "$log"
  fi
done <"$MANIFEST"

echo
echo "evals: ${pass} passed, ${fail} failed"
if [[ ${#truncated[@]} -ne 0 ]]; then
  # Reported even on an all-green run, because it is the only warning anyone
  # gets that the suite is running against the wall. A row that passes at the
  # cap fails the next time the skill call lands a few seconds later, and that
  # is the shape of a rotating cast of `skills invoked: none` failures.
  echo "passed on a truncated run (at the ${CAP}s cap): ${truncated[*]}" >&2
  echo "raise the cap or shorten those prompts — a pass here is luck, not a result." >&2
fi
if [[ $dirty_count -ne 0 ]]; then
  echo "dirtied the repo under test: ${dirtied[*]}" >&2
  echo "those changes are still in the checkout — inspect and remove them before" >&2
  echo "committing, and treat any row after the first as an unreliable result." >&2
fi
if [[ $fail -ne 0 ]]; then
  echo "failed: ${failed[*]}" >&2
  # Tally the causes. The failing rows rotate between runs, so which skill
  # failed is the least useful thing about a run — the per-cause count is what
  # a fix has to move, and a single row passing a few times in a row is noise
  # against a ~14% per-row rate. Bash 3.2 has no associative arrays, so this is
  # a sort/uniq over the collected causes.
  echo "causes: $(printf '%s\n' "${causes[@]}" | sort | uniq -c \
    | awk '{printf "%s×%s ", $1, $2}')" >&2
  echo "logs kept under: $RUN_DIR" >&2
  exit 1
fi
