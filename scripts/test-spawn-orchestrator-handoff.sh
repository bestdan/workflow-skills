#!/usr/bin/env bash
# shellcheck disable=SC1010,SC2034,SC2154 # Fixtures intentionally contain shell source, retain outputs for diagnostics, and read names the prelude defines.
# The run handoff: `handoff` itself, and the supervisor writing it on a
# terminal exit (and only then).
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: the prelude builds it a private fixture
# tree and the isolation guards, the epilogue asserts they held. See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share.
#
# Run directly: bash scripts/test-spawn-orchestrator-handoff.sh
set -uo pipefail
SO_PART=handoff
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

HO="$BASE/handoff"
# A run dir with the three run files. run_id carries the template's inline
# comment, which must not reach the filename.
ho_run() { # <dir> [status]
  local d="$1" st="${2:-active}"
  mkdir -p "$d/.auto-pilot"
  {
    printf -- '---\n'
    printf 'run_id: 2026-10-15-night # bare id\n'
    printf 'work_source: plan:dev_docs/tasks/x_plan\n'
    printf 'base_branch: main\n'
    printf 'status: %s\n' "$st"
    printf 'pause_reason: \n'
    printf 'exit_reason_detail: %s\n' "${HO_STALE_DETAIL:-}"
    printf -- '---\n'
  } >"$d/.auto-pilot/RUN.md"
  {
    printf '# Report\n\n## Outcomes\n\n- T-1 handed-off\n\n#123 is prose, not a heading\n\n'
    printf '```\n# a comment, not a heading\n```\n\n'
    printf '## For you\n\n1. Check Q1\n'
  } >"$d/.auto-pilot/REPORT.md"
  printf '## Q1 — T-1 — which key name\n\n- **Call:** flat\n' >"$d/.auto-pilot/QUESTIONS.md"
}

# --- the subcommand ----------------------------------------------------------
ho_run "$HO/run"
out="$("$SCRIPT" handoff --dir "$HO/run" --reason deadline --out-dir "$HO/notes" 2>&1)"
today="$(date +%Y-%m-%d)"
f="$HO/notes/$today-auto-pilot-2026-10-15-night.md"
if [ -f "$f" ]; then ok "handoff: written as <date>-auto-pilot-<run_id>.md"; else bad "handoff: written as <date>-auto-pilot-<run_id>.md" "$out"; fi
body="$(cat "$f" 2>/dev/null)"
have "handoff: front matter names the run-state branch" 'branch: auto-pilot/2026-10-15-night' "$body"
have "handoff: front matter carries an expires condition" 'expires: "when every PR' "$body"
have "handoff: deadline headline" 'stopped at its --until deadline' "$body"
have "handoff: deadline gives the full resume command, source included" '/auto-pilot dev_docs/tasks/x_plan --resume' "$body"
have "handoff: REPORT.md headings demoted under ## Report" '#### Outcomes' "$body"
have "handoff: For you section carried" '#### For you' "$body"
n="$(grep -c '^## Report$' <<<"$body")"
if [ "$n" = 1 ]; then ok "handoff: REPORT.md's own H1 nests rather than duplicating ## Report"; else bad "handoff: REPORT.md's own H1 nests rather than duplicating ## Report" "found $n"; fi
if grep -q '^#123 is prose' <<<"$body"; then ok "handoff: an issue ref at line start is left alone"; else bad "handoff: an issue ref at line start is left alone"; fi
have "handoff: a fenced # line is left alone" '# a comment, not a heading' "$body"
lack "handoff: a fenced # line is not demoted" '## a comment, not a heading' "$body"
have "handoff: the full decision log is carried" '#### Q1 — T-1 — which key name' "$body"
have "handoff: ends by telling the reader to delete it" 'delete this file' "$body"

# A later terminal exit for the same run supersedes, never adds a second note.
: >"$HO/notes/2026-01-01-auto-pilot-2026-10-15-night.md"
"$SCRIPT" handoff --dir "$HO/run" --reason done --out-dir "$HO/notes" >/dev/null 2>&1
n="$(find "$HO/notes" -name '*-auto-pilot-2026-10-15-night.md' | wc -l | tr -d ' ')"
if [ "$n" = 1 ]; then ok "handoff: one note per run (older one superseded)"; else bad "handoff: one note per run (older one superseded)" "found $n"; fi
have "handoff: done next step is /sweep-for-complete" '/sweep-for-complete' "$(cat "$f" 2>/dev/null)"

# A quote or backslash in the work source must not break the YAML front matter.
ho_run "$HO/quoted"
sed -i.bak 's|^work_source: .*|work_source: gh-issue:label "a\\b"|' "$HO/quoted/.auto-pilot/RUN.md"
"$SCRIPT" handoff --dir "$HO/quoted" --reason done --out-dir "$HO/quoted-notes" >/dev/null 2>&1
have "handoff: task value is YAML-escaped" 'task: "gh-issue:label \"a\\b\""' \
  "$(cat "$HO/quoted-notes/"*-auto-pilot-*.md 2>/dev/null)"

# No dir configured: a no-op that exits 0 — a run launched without one is fine.
o="$(env -u AUTO_PILOT_HANDOFF_DIR "$SCRIPT" handoff --dir "$HO/run" --reason done 2>&1)"
c=$?
if [ "$c" = 0 ] && grep -qF 'skipped' <<<"$o"; then ok "handoff: no out dir is a no-op"; else bad "handoff: no out dir is a no-op" "exit=$c $o"; fi
o="$("$SCRIPT" handoff --dir "$HO/run" --reason done --out-dir rel/dir 2>&1)"
c=$?
if [ "$c" = 2 ] && grep -qF 'must be absolute' <<<"$o"; then ok "handoff: relative out dir fails closed"; else bad "handoff: relative out dir fails closed" "exit=$c $o"; fi

# --- write-launch bakes the dir ----------------------------------------------
ho_launch() { # <run-dir> [extra write-launch args...]
  local d="$1"
  shift
  "$SCRIPT" write-launch --profile "$BASE/cf.sb" --settings "$BASE/wl.json" \
    --workdir "$d" --log "$d/.auto-pilot/orchestrator.log" \
    --prompt-file "$BASE/prompt.txt" --label "com.autopilot.ho.$(basename "$d")" \
    --claude-bin "$BASE/ho-stub/claude" --path "$BASE/ho-stub:$GUARD:/usr/bin:/bin" \
    --out-script "$d/launch.sh" --out-plist "$d/job.plist" "$@"
}
mkdir -p "$BASE/ho-stub"
ho_run "$HO/wl"
ho_launch "$HO/wl" >/dev/null 2>&1
lack "write-launch: no --handoff-dir, no export" 'AUTO_PILOT_HANDOFF_DIR' "$(cat "$HO/wl/launch.sh" 2>/dev/null)"
ho_launch "$HO/wl" --handoff-dir "$HO/baked" >/dev/null 2>&1
have "write-launch: --handoff-dir is exported to the supervisor" "export AUTO_PILOT_HANDOFF_DIR=$HO/baked" "$(cat "$HO/wl/launch.sh" 2>/dev/null)"
o="$(ho_launch "$HO/wl" --handoff-dir rel/dir 2>&1)"
have "write-launch: relative --handoff-dir fails closed" 'must be absolute' "$o"

# --- the supervisor writes it on a terminal exit, and only then --------------
if command -v git >/dev/null 2>&1; then
  STUB="$BASE/ho-stub"
  cat >"$STUB/sandbox-exec" <<'SBEOF'
#!/usr/bin/env bash
[ "${1:-}" = -f ] && shift 2
exec "$@"
SBEOF
  cat >"$STUB/launchctl" <<'LCEOF'
#!/usr/bin/env bash
case "${1:-}" in print) exit 1 ;; esac
exit 0
LCEOF
  cat >"$STUB/claude" <<'CLEOF'
#!/usr/bin/env bash
printf '{"type":"assistant","message":{"content":[{"type":"text","text":"stub wake"}]}}\n'
if [ "${STUB_DECLARE:?}" = dead-login ]; then
  printf 'API Error: 401 {"type":"error","error":{"type":"authentication_error","message":"OAuth token has expired."}}\n'
  exit 1
fi
"${STUB_SELF:?}" exit-reason --dir "${STUB_RUN_DIR:?}" --reason "${STUB_DECLARE:?}" >/dev/null 2>&1
exit 0
CLEOF
  chmod +x "$STUB/sandbox-exec" "$STUB/launchctl" "$STUB/claude"

  ho_wake() { # <name> <declared reason> [RUN.md status]
    local d="$HO/wake-$1"
    ho_run "$d" "${3:-active}"
    (cd "$d" && git init -q && git add -A \
      && git -c user.name=t -c user.email=t@t commit -q -m init)
    : >"$d/.auto-pilot/orchestrator.log"
    ho_launch "$d" --handoff-dir "$HO/wake-notes-$1" >/dev/null 2>&1
    STUB_SELF="$SCRIPT" STUB_RUN_DIR="$d" STUB_DECLARE="$2" \
      bash "$d/launch.sh" >/dev/null 2>&1
  }
  ho_note() { find "$HO/wake-notes-$1" -name '*-auto-pilot-*.md' 2>/dev/null | head -1; }

  ho_wake done done
  if [ -n "$(ho_note done)" ]; then ok "supervisor: declared done writes the handoff"; else bad "supervisor: declared done writes the handoff"; fi
  ho_wake deadline deadline
  if [ -n "$(ho_note deadline)" ]; then ok "supervisor: declared deadline writes the handoff"; else bad "supervisor: declared deadline writes the handoff"; fi
  ho_wake systemic systemic
  sn="$(ho_note systemic)"
  have "supervisor: a halt writes the handoff, flagged HALTED" 'HALTED' "$(cat "$sn" 2>/dev/null)"
  have "supervisor: the halt's ALARM entry from REPORT.md reaches the handoff" 'ALARM — supervisor halt' "$(cat "$sn" 2>/dev/null)"
  # A login that dies on a later wake: the agent declares nothing, and the
  # detail an earlier wake left in RUN.md must not be reported as the cause.
  HO_STALE_DETAIL='context exhausted' ho_wake auth dead-login
  an="$(cat "$(ho_note auth)" 2>/dev/null)"
  have "supervisor: a fatal-auth halt writes the handoff" 'HALTED' "$an"
  have "supervisor: the fatal-auth handoff names the login failure" 'non-retryable auth failure' "$an"
  lack "supervisor: the fatal-auth handoff drops an earlier wake's detail" 'context exhausted' "$an"
  ho_wake continuing continuing
  if [ -z "$(ho_note continuing)" ]; then ok "supervisor: a relaunchable exit writes no handoff"; else bad "supervisor: a relaunchable exit writes no handoff"; fi
  ho_wake paused paused
  if [ -z "$(ho_note paused)" ]; then ok "supervisor: a paused exit writes no handoff"; else bad "supervisor: a paused exit writes no handoff"; fi
  # A wake that wrote `status: done` but died before supervisor-check: the next
  # wake's gate tears the job down before claude runs, and must still write it.
  # The detail left by an earlier wake is not this finished run's cause.
  HO_STALE_DETAIL='context exhausted' ho_wake gate continuing done
  if [ -n "$(ho_note gate)" ]; then ok "supervisor-gate: a done status found at wake start writes the handoff"; else bad "supervisor-gate: a done status found at wake start writes the handoff"; fi
  lack "supervisor-gate: an earlier wake's detail is not shown as the cause" 'context exhausted' "$(cat "$(ho_note gate)" 2>/dev/null)"

  # launch resolves the dir by itself: <worktree root>/<main checkout name>/.handoffs,
  # even when the run's workdir is a linked worktree with a different name.
  mkdir -p "$HO/lr-main" "$HO/lr-root"
  (cd "$HO/lr-main" && git init -q && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m init \
    && git worktree add -q "$HO/lr-run" -b run)
  lo="$(WORKFLOW_SKILLS_WORKTREE_ROOT="$HO/lr-root" "$SCRIPT" launch --dry-run --workdir "$HO/lr-run" \
    --out-script "$HO/lr.sh" --out-plist "$HO/lr.plist" --label com.x --handle "$HO/lr-h.txt" 2>&1)"
  have "launch: resolves the handoff dir from the main checkout's name" "handoff dir   -> $HO/lr-root/lr-main/.handoffs" "$lo"
  lo="$("$SCRIPT" launch --dry-run --workdir "$HO/lr-run" --handoff-dir "$HO/explicit" \
    --out-script "$HO/lr.sh" --out-plist "$HO/lr.plist" --label com.x --handle "$HO/lr-h.txt" 2>&1)"
  have "launch: an explicit --handoff-dir wins" "handoff dir   -> $HO/explicit" "$lo"
else
  echo "skip - handoff supervisor: git not available"
fi

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
