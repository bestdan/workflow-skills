#!/bin/bash
# Run one reviewer/model pair against the fixture, mirroring co-review's
# built-in invocation for that reviewer (same pointer, flags, neutral cwd).
#   bash run.sh <agy|devin|crush|codex> <model> <tag>
# Needs: the reviewer CLI logged in, network, and REPO pointing at a
# workflow-skills checkout (for review_prompt.md and crush-readonly.json).
# Writes $WORK/out/<tag>.txt (stdout+stderr) and $WORK/out/<tag>.meta (rc, secs).
# Run crush models one at a time: two concurrent `crush run`s on a fresh data
# dir race its SQLite migration and one dies with "duplicate column name".
WORK=${WORK:-/tmp/claude/cr-bench}
REPO=${REPO:?set REPO to a workflow-skills checkout}
IN="$WORK/in/input.txt"
mkdir -p "$WORK/in" "$WORK/out" "$WORK/neutral-devin" "$WORK/neutral-crush"
[ -s "$IN" ] || cat "$REPO/skills/co-review/review_prompt.md" "$WORK/bug.diff" >"$IN"
POINTER='Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.'
AGYPTR="Your entire input is the file at $IN (a review rubric followed by a diff). Read that file and review ONLY it. Do NOT explore any other file, run commands, or retrieve any prior conversation or memory. If that file is missing or empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only."
kind=$1 model=$2 tag=$3
start=$(date +%s)
case "$kind" in
  agy) agy --sandbox --add-dir "$WORK/in" -p "$AGYPTR" --model "$model" >"$WORK/out/$tag.txt" 2>&1 ;;
  devin) (cd "$WORK/neutral-devin" && devin -p --prompt-file "$IN" --permission-mode auto --respect-workspace-trust false --model "$model") >"$WORK/out/$tag.txt" 2>&1 ;;
  crush)
    cp "$REPO/skills/co-review/reviewers/assets/crush-readonly.json" "$WORK/neutral-crush/crush.json"
    crush run --cwd "$WORK/neutral-crush" -q -m "$model" "$POINTER" <"$IN" >"$WORK/out/$tag.txt" 2>&1
    ;;
  codex) codex exec --sandbox read-only --model "$model" "$POINTER" <"$IN" >"$WORK/out/$tag.txt" 2>&1 ;;
esac
rc=$?
echo "rc=$rc secs=$(($(date +%s) - start))" >"$WORK/out/$tag.meta"
