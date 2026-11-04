#!/usr/bin/env bash
# shellcheck disable=SC2016,SC2034,SC2154 # Fixtures hold literal shell source, retain outputs for diagnostics, and read names the prelude defines.
# The Linux jail (#1349): render-nono-profile's fixtures, render-settings
# --jail nono, write-launch --jail nono and its token command, and claude-token.
#
# One part of the orchestrator harness for scripts/spawn-orchestrator.sh. Every
# part is self-contained and offline: no real nono runs here (pre-flight's smoke
# is the behavioral proof, on a Linux host). See
# scripts/lib/spawn-orchestrator-test-prelude.sh for what the parts share.
#
# Run directly: bash scripts/test-spawn-orchestrator-nono.sh
set -uo pipefail
SO_PART=nono
SO_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib"
# shellcheck source=scripts/lib/spawn-orchestrator-test-prelude.sh
. "$SO_LIB/spawn-orchestrator-test-prelude.sh"

NO="$BASE/nono"
# A home with the credential file, a link to it, the rest of ~/.claude, gh's
# config and a git identity. Physical paths, because the renderer canonicalizes.
NH="$NO/home"
mkdir -p "$NH/.claude/projects" "$NH/.config/gh" "$NO/tmp" "$NO/hooks"
NH="$(cd "$NH" && pwd -P)"
printf '{"secret":1}\n' >"$NH/.claude/.credentials.json"
printf '{}\n' >"$NH/.claude/settings.json"
ln -s "$NH/.claude/.credentials.json" "$NH/.claude/cred-link"
printf '{}\n' >"$NH/.claude.json"
printf 'github.com:\n' >"$NH/.config/gh/hosts.yml"
printf '[user]\n\tname = t\n' >"$NH/.gitconfig"
NTMP="$(cd "$NO/tmp" && pwd -P)"
RW1="$(cd "$RUN_WT" && pwd -P)"
RW2="$(cd "$WORKER_WT" && pwd -P)"
RO1="$(cd "$REPO_RO" && pwd -P)"

# Render with the fixture home. Extra args go last.
nrender() { # <out> [args...]
  local out="$1"
  shift
  HOME="$NH" "$SCRIPT" render-nono-profile --rw "$RUN_WT" --rw "$WORKER_WT" --ro "$REPO_RO" \
    --cred-ro "$NH/.config/gh" --tmpdir "$NO/tmp" --out "$out" "$@"
}
# One `<key>\t<path>` line per filesystem grant, plus the scalar settings, so the
# assertions below read a parsed profile rather than grepping JSON text.
nflat() {
  python3 -I - "$1" <<'PY'
import json, sys
p = json.load(open(sys.argv[1]))
for k, v in p.get("filesystem", {}).items():
    for path in v:
        print(f"{k}\t{path}")
print("extends\t%s" % ("extends" in p))
print("command_policies\t%s" % ("command_policies" in p))
print("af_unix_mediation\t%s" % p.get("linux", {}).get("af_unix_mediation"))
print("network.block\t%s" % p.get("network", {}).get("block"))
print("groups.exclude\t%s" % ",".join(p.get("groups", {}).get("exclude", [])))
print("TMPDIR\t%s" % p.get("environment", {}).get("set_vars", {}).get("TMPDIR"))
PY
}

# --- render-nono-profile: §1's rows, with a token command ----------------------
if ! command -v python3 >/dev/null 2>&1; then
  echo "skip - render-nono-profile fixtures: python3 not available to parse the JSON"
else
  out="$(nrender "$NO/p.json" --claude-token 2>&1)"
  have "nono render: succeeds" "nono profile OK" "$out"
  flat="$(nflat "$NO/p.json" 2>&1)"
  have "nono render: the profile is valid JSON" "TMPDIR" "$flat"
  have "nono render: the run worktree is read-write" "$(printf 'allow\t%s' "$RW1")" "$flat"
  have "nono render: a worker worktree is read-write" "$(printf 'allow\t%s' "$RW2")" "$flat"
  have "nono render: the run TMPDIR is read-write" "$(printf 'allow\t%s' "$NTMP")" "$flat"
  have "nono render: the repo is read-only" "$(printf 'read\t%s' "$RO1")" "$flat"
  have "nono render: the gh credential store is read-only" "$(printf 'read\t%s' "$NH/.config/gh")" "$flat"
  have "nono render: ~/.claude's other files are read-only" "$(printf 'read_file\t%s' "$NH/.claude/settings.json")" "$flat"
  have "nono render: ~/.claude's subdirs are read-only" "$(printf 'read\t%s' "$NH/.claude/projects")" "$flat"
  have "nono render: the git identity is readable" "$(printf 'read_file\t%s' "$NH/.gitconfig")" "$flat"
  if awk -F'\t' -v c="$NH/.claude" '$2 == c { found = 1 } END { exit !found }' <<<"$flat"; then
    bad "nono render: ~/.claude itself is not granted"
  else
    ok "nono render: ~/.claude itself is not granted"
  fi
  lack "nono render: the credential file is in no grant" ".credentials.json" "$flat"
  lack "nono render: a link to the credential file is in no grant" "cred-link" "$flat"
  have "nono render: pathname AF_UNIX mediation is on" "$(printf 'af_unix_mediation\tpathname')" "$flat"
  have "nono render: egress parity first (network not blocked)" "$(printf 'network.block\tFalse')" "$flat"
  have "nono render: nono's /tmp write group is excluded" "$(printf 'groups.exclude\tsystem_write_linux')" "$flat"
  have "nono render: the profile pins the jail's TMPDIR" "$(printf 'TMPDIR\t%s' "$NTMP")" "$flat"
  have "nono render: vendored, never extends a registry profile" "$(printf 'extends\tFalse')" "$flat"
  have "nono render: no command_policies block (it would break toolchain exec)" "$(printf 'command_policies\tFalse')" "$flat"
  # No grant of any kind is $HOME or above it.
  home_hits="$(awk -F'\t' -v h="$NH" '$2 == h || (index(h, $2 "/") == 1) || $2 == "/"' <<<"$flat")"
  if [ -z "$home_hits" ]; then ok "nono render: no grant is \$HOME or an ancestor of it"; else bad "nono render: no grant is \$HOME or an ancestor of it" "$home_hits"; fi

  # The global hooks path git runs on every commit is granted read-only.
  printf '[core]\n\thooksPath = %s\n' "$NO/hooks" >"$NO/gitconfig-hooks"
  GIT_CONFIG_GLOBAL="$NO/gitconfig-hooks" nrender "$NO/p-hooks.json" --claude-token >/dev/null 2>&1
  have "nono render: the global git hooks path is read-only" \
    "$(printf 'read\t%s' "$(cd "$NO/hooks" && pwd -P)")" "$(nflat "$NO/p-hooks.json" 2>&1)"

  # Without a token command: the documented fallback, said out loud.
  out="$(nrender "$NO/p-fallback.json" 2>&1)"
  have "nono render: without a token, ~/.claude is writable (the fallback)" \
    "$(printf 'allow\t%s' "$NH/.claude")" "$(nflat "$NO/p-fallback.json" 2>&1)"
  have "nono render: without a token, the renderer warns" "/.claude is WRITABLE" "$out"
fi

# --- render-nono-profile --git-worktree (#1350) --------------------------------
# A commit in a linked worktree writes into the main checkout's common dir. Only
# what a commit needs is writable; hooks/ and config stay read-only.
if command -v python3 >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
  GM="$NO/gitmain"
  git init -q -b main "$GM"
  git -C "$GM" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
  git -C "$GM" worktree add -q "$NO/gitwt" -b run 2>/dev/null
  GC="$(cd "$GM/.git" && pwd -P)"
  out="$(nrender "$NO/p-git.json" --claude-token --git-worktree "$NO/gitwt" 2>&1)"
  have "nono render --git-worktree: succeeds" "nono profile OK" "$out"
  gflat="$(nflat "$NO/p-git.json" 2>&1)"
  for sub in objects refs logs worktrees/gitwt; do
    have "nono render --git-worktree: $sub is writable" "$(printf 'allow\t%s/%s' "$GC" "$sub")" "$gflat"
  done
  have "nono render --git-worktree: the common dir is read-only" "$(printf 'read\t%s' "$GC")" "$gflat"
  if awk -F'\t' -v c="$GC" '$1 == "allow" && $2 == c { found = 1 } END { exit !found }' <<<"$gflat"; then
    bad "nono render --git-worktree: the common dir itself is not writable"
  else
    ok "nono render --git-worktree: the common dir itself is not writable"
  fi
  lack "nono render --git-worktree: hooks/ is not writable" "$(printf 'allow\t%s/hooks' "$GC")" "$gflat"
  lack "nono render --git-worktree: config is not writable" "$(printf 'allow_file\t%s/config' "$GC")" "$gflat"
  if o="$(nrender "$NO/p-git2.json" --git-worktree "$GM" 2>&1)"; then
    bad "nono render --git-worktree: refuses a main checkout" "render succeeded"
  else
    have "nono render --git-worktree: refuses a main checkout" "must be a linked worktree" "$o"
  fi
  if o="$(nrender "$NO/p-git3.json" --git-worktree "$NO/tmp" 2>&1)"; then
    bad "nono render --git-worktree: refuses a non-checkout" "render succeeded"
  else
    have "nono render --git-worktree: refuses a non-checkout" "is not a git checkout" "$o"
  fi
else
  echo "skip - render-nono-profile --git-worktree: python3 or git not available"
fi

# --- render-nono-profile: refusals --------------------------------------------
nrefuse() { # <name> <expected text> <args...>
  local name="$1" want="$2" o
  shift 2
  if o="$(HOME="$NH" "$SCRIPT" render-nono-profile --tmpdir "$NO/tmp" --out "$NO/r.json" "$@" 2>&1)"; then
    bad "$name" "render succeeded"
  else
    have "$name" "$want" "$o"
  fi
}
nrefuse "nono render: refuses a write grant of \$HOME" "covers \$HOME" --rw "$NH"
nrefuse "nono render: refuses a write grant above \$HOME" "covers \$HOME" --rw "$(dirname "$NH")"
nrefuse "nono render: refuses a write grant of /" "covers \$HOME" --rw /
nrefuse "nono render: with a token, refuses a read grant of ~/.claude" "a grant covers" --claude-token --ro "$NH/.claude"
nrefuse "nono render: with a token, refuses a read grant of \$HOME" "a grant covers" --claude-token --ro "$NH"
nrefuse "nono render: with a token, refuses naming the credential file" "a grant names" --claude-token --cred-ro "$NH/.claude/.credentials.json"
nrefuse "nono render: refuses a --rw outside --confine-under" "outside every --confine-under" --confine-under "$RUN_WT" --rw "$WORKER_WT"
mkdir -p "$NO/we\"ird"
nrefuse "nono render: refuses a path JSON would need escaped" "quote, backslash" --rw "$NO/we\"ird"
nrefuse "nono render: requires --out" "requires --out" --rw "$RUN_WT" --out ""
if o="$(HOME="$NH" "$SCRIPT" render-nono-profile --rw "$RUN_WT" --out "$NO/r.json" 2>&1)"; then
  bad "nono render: requires --tmpdir" "render succeeded"
else
  have "nono render: requires --tmpdir" "requires --tmpdir" "$o"
fi

# --- render-settings --jail nono ----------------------------------------------
"$SCRIPT" render-settings --jail nono --source gh-issue --out "$NO/settings.json" >/dev/null 2>&1
have "settings --jail nono: the harness sandbox is off" '{"sandbox":{"enabled":false}}' "$(cat "$NO/settings.json" 2>/dev/null)"
if o="$("$SCRIPT" render-settings --jail bwrap --source plan --out "$NO/s2.json" 2>&1)"; then
  bad "settings: an unknown --jail is refused" "render succeeded"
else
  have "settings: an unknown --jail is refused" "--jail must be seatbelt or nono" "$o"
fi

# --- write-launch --jail nono -------------------------------------------------
printf 'run the graph\n' >"$NO/prompt.txt"
NBIN="$NO/bin/nono"
mkdir -p "$NO/bin"
# A stub nono: records the token it was handed (and nothing else), then exits 0.
printf '#!/bin/sh\nprintf "%%s\\n" "${CLAUDE_CODE_OAUTH_TOKEN:-<none>}" >>"%s/nono.calls"\nexit 0\n' "$NO" >"$NBIN"
chmod +x "$NBIN"
nlaunch() { # <tag> [args...]
  local tag="$1"
  shift
  "$SCRIPT" write-launch --jail nono --nono-bin "$NBIN" --profile "$NO/p.json" --settings "$NO/settings.json" \
    --workdir "$NO/wd" --log "$NO/wd/.auto-pilot/orchestrator.log" --prompt-file "$NO/prompt.txt" \
    --label "com.autopilot.test.nono.$tag" --claude-bin "$BIN" --path "/usr/bin:/bin" \
    --out-script "$NO/$tag.sh" --out-plist "$NO/$tag.plist" "$@"
}
mkdir -p "$NO/wd/.auto-pilot"
if [ -f "$NO/p.json" ]; then
  nlaunch plain >/dev/null 2>&1
  w="$(cat "$NO/plain.sh" 2>/dev/null)"
  have "write-launch nono: runs the pinned nono with the profile" "$NBIN run --silent --profile $NO/p.json --" "$w"
  have "write-launch nono: exports the profile's own TMPDIR" "export TMPDIR=$NTMP" "$w"
  have "write-launch nono: turns off nono's update check and migration" "export NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1" "$w"
  lack "write-launch nono: no sandbox-exec" "sandbox-exec" "$w"
  lack "write-launch nono: no token command without --claude-token-cmd" "claude-token" "$w"

  nlaunch tok --claude-token-cmd 'printf tok-123' >/dev/null 2>&1
  w="$(cat "$NO/tok.sh" 2>/dev/null)"
  have "write-launch nono: the token command runs through claude-token" "claude-token --dir $NO/wd --label com.autopilot.test.nono.tok --cmd" "$w"
  have "write-launch nono: the token goes to the jail line only" 'CLAUDE_CODE_OAUTH_TOKEN="$tok" '"$NBIN run" "$w"
  lack "write-launch nono: the token is never exported" "export CLAUDE_CODE_OAUTH_TOKEN" "$w"
  have "write-launch nono: the token is unset after the jailed run" "unset tok" "$w"
  # The token must not reach supervisor-check: it comes after the unset.
  tok_unset_line="$(grep -n '^unset tok$' "$NO/tok.sh" | cut -d: -f1)"
  check_line="$(grep -n 'supervisor-check' "$NO/tok.sh" | cut -d: -f1)"
  if [ -n "$tok_unset_line" ] && [ -n "$check_line" ] && [ "$tok_unset_line" -lt "$check_line" ]; then
    ok "write-launch nono: the token is gone before supervisor-check runs"
  else
    bad "write-launch nono: the token is gone before supervisor-check runs" "unset at ${tok_unset_line:-none}, check at ${check_line:-none}"
  fi

  # Refusals.
  nlrefuse() { # <name> <expected> <args...>
    local name="$1" want="$2" o
    shift 2
    if o="$(nlaunch refuse "$@" 2>&1)"; then bad "$name" "write-launch succeeded"; else have "$name" "$want" "$o"; fi
  }
  "$SCRIPT" render-settings --source plan --out "$NO/seatbelt-settings.json" >/dev/null 2>&1
  nlrefuse "write-launch nono: refuses settings that turn the harness sandbox on" "harness sandbox off" --settings "$NO/seatbelt-settings.json"
  printf '{ "sandbox": { "enabled": true } }\n' >"$NO/spaced-settings.json"
  nlrefuse "write-launch nono: refuses them however the JSON is spaced" "harness sandbox off" --settings "$NO/spaced-settings.json"
  printf ';; not a nono profile\n' >"$NO/not-nono.json"
  nlrefuse "write-launch nono: refuses a profile that is not a rendered nono profile" "not a render-nono-profile profile" --profile "$NO/not-nono.json"
  nlrefuse "write-launch nono: refuses a relative --nono-bin" "absolute --nono-bin" --nono-bin bin/nono
  nlrefuse "write-launch nono: refuses a multi-line token command" "must be one line" --claude-token-cmd $'printf a\nprintf b'
  if o="$("$SCRIPT" write-launch --nono-bin "$NBIN" --profile "$BASE/cf.sb" --settings "$BASE/wl.json" \
    --workdir "$NO/wd" --log "$NO/wd/.auto-pilot/orchestrator.log" --prompt-file "$NO/prompt.txt" \
    --label com.autopilot.test.nono.sb --claude-bin "$BIN" --path "/usr/bin:/bin" \
    --out-script "$NO/sb.sh" --out-plist "$NO/sb.plist" 2>&1)"; then
    bad "write-launch: --nono-bin without --jail nono is refused" "write-launch succeeded"
  else
    have "write-launch: --nono-bin without --jail nono is refused" "only meaningful with --jail nono" "$o"
  fi
else
  echo "skip - write-launch nono: no rendered profile (python3 or the render failed above)"
fi

# --- claude-token -------------------------------------------------------------
# A real git run worktree, so a halt's run-state commit works for real.
ctrun() { # <dir>
  mkdir -p "$1/.auto-pilot"
  (cd "$1" && git init -q)
  printf -- '---\nstatus: active\npause_reason: \nexit_reason: continuing\n---\n' >"$1/.auto-pilot/RUN.md"
  printf '# report\n' >"$1/.auto-pilot/REPORT.md"
  (cd "$1" && git add -A && git commit -q -m init)
}
ctrun "$NO/ct-ok"
out="$("$SCRIPT" claude-token --dir "$NO/ct-ok" --label com.autopilot.test.ct --cmd 'printf tok-abc' 2>"$NO/ct-ok.err")"
rc=$?
if [ "$rc" = 0 ] && [ "$out" = tok-abc ]; then ok "claude-token: prints the token and exits 0"; else bad "claude-token: prints the token and exits 0" "rc=$rc out=$out"; fi
lack "claude-token: the token never reaches stderr (the run log)" "tok-abc" "$(cat "$NO/ct-ok.err")"
if [ -f "$NO/ct-ok/.auto-pilot/ALARM" ]; then bad "claude-token: success raises no alarm"; else ok "claude-token: success raises no alarm"; fi

ct_fail() { # <name> <cmd> <reason substring>
  local name="$1" cmd="$2" why="$3" d rc o
  d="$NO/ct-$(printf '%s' "$name" | tr -c 'a-z0-9' '-')"
  ctrun "$d"
  o="$("$SCRIPT" claude-token --dir "$d" --label com.autopilot.test.ct --cmd "$cmd" 2>&1)"
  rc=$?
  if [ "$rc" = 3 ]; then ok "claude-token: $name halts (exit 3)"; else bad "claude-token: $name halts (exit 3)" "rc=$rc $o"; fi
  have "claude-token: $name — RUN.md reads systemic" "status: systemic" "$(cat "$d/.auto-pilot/RUN.md")"
  if [ -f "$d/.auto-pilot/ALARM" ]; then ok "claude-token: $name — the alarm is raised"; else bad "claude-token: $name — the alarm is raised"; fi
  have "claude-token: $name — REPORT.md names the token command" "$why" "$(head -3 "$d/.auto-pilot/REPORT.md")"
}
ct_fail "a failing command" 'echo nope >&2; exit 7' "the Claude token command exited 7"
ct_fail "an empty token" 'true' "the Claude token command printed nothing"
ct_fail "two lines" 'printf "a\nb\n"' "printed something other than one token"
ct_fail "a token with a space" 'printf "a b"' "printed something other than one token"
have "claude-token: the alarm says what to do" "run the launch's --claude-token-cmd by hand" \
  "$(cat "$NO/ct-a-failing-command/.auto-pilot/REPORT.md" 2>/dev/null)"

# shellcheck source=scripts/lib/spawn-orchestrator-test-epilogue.sh
. "$SO_LIB/spawn-orchestrator-test-epilogue.sh"
