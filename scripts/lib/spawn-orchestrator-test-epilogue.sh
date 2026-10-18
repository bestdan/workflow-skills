# shellcheck shell=bash
# shellcheck disable=SC2154 # The counters and assert helpers come from the prelude, which ShellCheck cannot see from this file alone.
# Shared epilogue for the scripts/test-spawn-orchestrator-*.sh parts.
#
# Every part sources this last. It holds the assertions that are about the RUN
# rather than about the orchestrator: that the notifier guard held, that the
# caller's repo is untouched, and that a run claiming seatbelt coverage got it.
# Each part reads $SO_PART for its name and leaves the shell's exit status set
# from its own failure count.
#
# Under the monolith these ran once, over the whole file. Most of them are at
# least as strong per-part, and one could not survive the move unchanged; see
# the tally below.

# --- the notifier guard held --------------------------------------------------
# The acceptance criterion for the desktop-spam bug: no test, present or FUTURE,
# may reach a real notifier. The structural half is per-part, because PATH is
# per-process and each part builds its own composite PATHs:
#   1. structural — `osascript`/`terminal-notifier` resolve INSIDE the guard dir,
#      so a binary outside it is unreachable by name for the whole part. (Nothing
#      in spawn-orchestrator.sh calls a notifier by absolute path; `_alarm_notify`
#      goes through `command -v`, which is exactly what this shadows.)
guard_osa="$(command -v osascript || true)"
case "$guard_osa" in
  "$GUARD"/*) ok "notifier guard: osascript resolves INSIDE the guard dir, never the real binary" ;;
  *) bad "notifier guard: osascript resolves INSIDE the guard dir" "resolved to: ${guard_osa:-(not found)}" ;;
esac
guard_tn="$(command -v terminal-notifier || true)"
case "$guard_tn" in
  "$GUARD"/*) ok "notifier guard: terminal-notifier resolves INSIDE the guard dir" ;;
  *) bad "notifier guard: terminal-notifier resolves INSIDE the guard dir" "resolved to: ${guard_tn:-(not found)}" ;;
esac
#   2. STRUCTURAL — the checks above only see the INHERITED PATH. Every fixture
#      that OVERRIDES PATH (`PATH="$STUB_PATH" "$SCRIPT" …`) escapes them entirely:
#      if that PATH omits $GUARD and the stub dir has no osascript of its own, the
#      alarm resolves /usr/bin/osascript and pops a REAL notification on the
#      developer's desktop. The guard log cannot see it — an escaped call never
#      reaches the guard, so the count stays plausible and the suite stays green
#      while leaking. That is exactly how this leak survived: a test that asserts
#      only what it catches can never report what got away. So assert it at the
#      source: under EVERY composite PATH this part builds, osascript must resolve
#      to something inside $BASE (a stub, or the guard) — never a real system
#      binary. Each name is optional because each part builds only its own: the
#      supervisor part has GT_PATH, the alarm part ALPATH/ALFAIL_PATH, the
#      exit-contract part STUB_PATH/STUBF_PATH, doctor PSSTUB_PATH,
#      status-report SRW_PATH/SRL_PATH. A part that builds none checks none.
#
#      Every name here must be the composite PATH a fixture RUNS UNDER, stored
#      whole at its own call site. Do not reconstruct one here: the guard dir
#      lives inside $BASE, so any reconstruction that re-injects $GUARD to make
#      itself pass can no longer fail, and one that omits it reports a leak at
#      call sites that are in fact safe. Either way the assertion stops tracking
#      the fixture — which is the failure this check exists to catch, wearing
#      the check's own clothes. (STUBF_PATH and ALFAIL_PATH were reconstructed
#      here until a review caught exactly that; they are stored variables now.)
#
#      status-report's leak probe is deliberately absent: it runs under
#      `PATH="$SR_LEAK:$PATH"`, i.e. the INHERITED PATH, which the prelude has
#      already prefixed with $GUARD. That shape is covered by the resolution
#      checks above, not by this loop, and pinning it here would only assert
#      the prelude's own export a second time.
#
#      THIS LIST IS THE WEAK POINT: it is enumerated, so a part that invents a
#      new composite PATH and does not add it here escapes the check silently —
#      which is the same shape as the original leak. The monolith could not
#      cover the status-report names at all (it asserted at a point in the file
#      BEFORE they were defined); the epilogue runs last in every part, so it
#      can. Keep it exhaustive:
#        grep -n 'PATH="\$' scripts/test-spawn-orchestrator-*.sh
for _pv in GT_PATH ALPATH ALFAIL_PATH STUB_PATH STUBF_PATH PSSTUB_PATH SRW_PATH SRL_PATH; do
  eval "_pval=\"\${$_pv:-}\""
  [ -n "$_pval" ] || continue
  _osa="$(PATH="$_pval" command -v osascript 2>/dev/null || true)"
  case "$_osa" in
    "$BASE"/*) ok "notifier guard: \$$_pv routes osascript inside the test tree, never a real binary" ;;
    *) bad "notifier guard: \$$_pv routes osascript to a REAL binary (a suite run would pop a desktop notification)" "resolved to: ${_osa:-(not found)}" ;;
  esac
done
#   3. behavioral — alarms really did route through the guard. This is the one
#      assertion the split could not keep per-part. It exists so a guard that has
#      quietly stopped covering the alarm path fails loudly instead of passing on
#      a code path it no longer touches, and it was written as "the log is
#      NON-EMPTY over the whole suite". Most parts raise no alarm at all, so
#      per-part it would assert something untrue. It is therefore reported here
#      and ASSERTED BY THE DRIVER over the sum: each part drops its count in
#      $SO_TEST_TALLY_DIR and scripts/test-spawn-orchestrator.sh fails the run if
#      every part together caught zero. Running one part directly reports its
#      count without asserting — the whole-suite claim needs the whole suite.
guard_hits="$(grep -c '^osascript: ' "$NOTIFY_GUARD_LOG" 2>/dev/null | tr -d ' ')"
case "$guard_hits" in '' | *[!0-9]*) guard_hits=0 ;; esac
if [ -n "${SO_TEST_TALLY_DIR:-}" ]; then
  printf '%s\n' "$guard_hits" >"$SO_TEST_TALLY_DIR/$SO_PART.hits"
else
  echo "note - notifier guard: $guard_hits incidental alarm(s) caught in this part (the >0 assertion is the driver's, over all parts)"
fi

# --- PRE-618 caller-repo integrity assertion ----------------------------------
# The regression test for the fixture escape (see the caller-repo snapshot in the
# prelude): after the part, the caller's repo must be what it was before — same
# HEAD, same current branch, same tracked working tree + index. If any moved, a
# fixture reached outside its own temp repo and corrupted the caller's checkout —
# the exact failure this harness exists to make impossible, and the check that
# was missing when it first happened. (Untracked files are excluded on purpose: a
# repo-local BASE fallback is trap-cleaned on exit and is not corruption.)
#
# Per-part detection is at least as strong as the whole-suite version it
# replaces. It does NOT reliably name the offender, though: the parts run
# concurrently, so a persistent escape fails every part whose epilogue runs
# after it, narrowing the culprit to a set rather than to one part.
if [ "$CALLER_IS_GIT" = 1 ]; then
  # The after-probes must observe the caller's repo the way the caller's own
  # git does — otherwise a pinned-config view of a tree checked out under the
  # developer's own config (core.autocrlf, core.fileMode, core.attributesFile
  # all affect `status --porcelain`) can report phantom modifications that were
  # never there. `env -u` CLEARS the three rather than restoring them, so this
  # reproduces the before-snapshot exactly when the caller had none of them set
  # — the normal case — and diverges for a caller who exports them in their own
  # shell. That divergence fails loud and safe (a false positive in this
  # assertion, never a missed escape), which is why it is not worth carrying the
  # save/restore machinery to close.
  caller_head_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" rev-parse HEAD 2>/dev/null || echo NONE)"
  caller_ref_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" rev-parse --abbrev-ref HEAD 2>/dev/null || echo NONE)"
  caller_tracked_after="$(env -u GIT_CONFIG_GLOBAL -u GIT_CONFIG_SYSTEM -u GIT_CEILING_DIRECTORIES git -C "$CALLER_REPO" status --porcelain --untracked-files=no 2>/dev/null)"
  [ "$caller_head_after" = "$CALLER_HEAD_BEFORE" ] \
    && ok "caller-repo safety: HEAD unmoved (no fixture committed into the caller's repo)" \
    || bad "caller-repo safety: HEAD MOVED — a fixture committed into the caller's repo" \
      "before=$CALLER_HEAD_BEFORE after=$caller_head_after"
  [ "$caller_ref_after" = "$CALLER_REF_BEFORE" ] \
    && ok "caller-repo safety: current branch unchanged" \
    || bad "caller-repo safety: current branch changed" "before=$CALLER_REF_BEFORE after=$caller_ref_after"
  [ "$caller_tracked_after" = "$CALLER_TRACKED_BEFORE" ] \
    && ok "caller-repo safety: tracked working tree + index unchanged" \
    || bad "caller-repo safety: tracked files changed under the caller's repo" \
      "before=[$CALLER_TRACKED_BEFORE] after=[$caller_tracked_after]"
fi

# --- opt-in strict mode: demand full seatbelt coverage --------------------
# Exists so a future macos-latest CI job can DEMAND the seatbelt-behavioral
# assertions actually ran, instead of quietly passing while skipping everything
# it was set up to run. Runs unconditionally (not folded into the probe in the
# prelude) so it always counts as a real failure when requested, rather than
# depending on where in the part the probe happens to sit.
#
# Gates on the SKIP COUNT, not on SEATBELT_OK. Gating on the capability alone
# left the flag asserting less than it advertises: a usable seatbelt with a
# missing host fixture (no distinct fixture binary, no sed/env, no launchctl)
# skips real assertions while SEATBELT_OK stays 1, so the job would pass having
# silently run fewer than it claims — precisely the "green while skipping what it
# exists to run" outcome this flag exists to make impossible. A zero skip count
# is the only thing that means full coverage, and SEATBELT_OK=0 already forces a
# non-zero skip count in the parts that carry seatbelt assertions, so it needs no
# arm here. Per-part gating is equivalent to the whole-suite version: the run
# fails if ANY part skipped, which is exactly "the total is non-zero".
if [ "${SO_TEST_REQUIRE_SEATBELT:-0}" = 1 ] && [ "$skip" -gt 0 ]; then
  bad "seatbelt coverage required (SO_TEST_REQUIRE_SEATBELT=1) but $skip seatbelt-behavioral assertions did not run (SEATBELT_OK=$SEATBELT_OK)"
fi

# A quiet skip count buries the fact that a whole behavioral layer went
# unexercised — loud enough to notice, not loud enough to fail a run that
# never claimed seatbelt coverage in the first place (that's what
# SO_TEST_REQUIRE_SEATBELT is for).
if [ "$skip" -gt 0 ] && [ "$SEATBELT_OK" != 1 ] && command -v sandbox-exec >/dev/null 2>&1; then
  echo
  echo "NOTE: $skip seatbelt-behavioral tests were SKIPPED — sandbox-exec cannot apply a"
  echo "      profile here (nested sandbox). Run this part unsandboxed for full coverage."
  echo
fi

echo "test-spawn-orchestrator/$SO_PART: $pass passed, $fail failed, $skip skipped"
if [ -n "${SO_TEST_TALLY_DIR:-}" ]; then
  printf '%s %s %s\n' "$pass" "$fail" "$skip" >"$SO_TEST_TALLY_DIR/$SO_PART.counts"
fi
[ "$fail" = 0 ]
