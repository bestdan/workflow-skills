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

# --- run-clone + render-nono-profile --git-worktree ---------------------------
# The run's own git repo: a bare clone inside the run root, the run worktree and
# workers/ beside it. --git-worktree grants that clone's whole git dir and
# refuses a linked worktree of a main checkout.
if command -v python3 >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then
  GM="$NO/gitmain"
  git init -q --bare "$NO/origin.git"
  git init -q -b main "$GM"
  git -C "$GM" -c user.name=t -c user.email=t@t commit -q --allow-empty -m init
  git -C "$GM" remote add origin "$NO/origin.git"
  git -C "$GM" push -q origin main 2>/dev/null
  git -C "$GM" branch -q side
  main_sum() { (cd "$GM/.git" && find . -type f -exec cksum {} + | sort | cksum); }
  before="$(main_sum)"
  NR="$NO/runroot"
  out="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NR" --run-id r-1 2>&1)"
  NR="$(cd "$NR" 2>/dev/null && pwd -P)"
  have "run-clone: prints the clone" "RUN_CLONE $NR/gitmain.git" "$out"
  have "run-clone: prints the run worktree" "RUN_WORKTREE $NR/r-1" "$out"
  [ "$(git -C "$NR/r-1" rev-parse --abbrev-ref HEAD 2>/dev/null)" = auto-pilot/r-1 ] \
    && ok "run-clone: the run worktree is on auto-pilot/<run_id>" \
    || bad "run-clone: the run worktree is on auto-pilot/<run_id>" "$(git -C "$NR/r-1" rev-parse --abbrev-ref HEAD 2>&1)"
  [ "$(git --git-dir="$NR/gitmain.git" config --bool core.bare 2>/dev/null)" = true ] \
    && ok "run-clone: the clone is bare" || bad "run-clone: the clone is bare"
  [ "$(git -C "$NR/r-1" remote get-url origin 2>/dev/null)" = "$NO/origin.git" ] \
    && ok "run-clone: origin is the main checkout's origin, not the main checkout" \
    || bad "run-clone: origin is the main checkout's origin, not the main checkout" "$(git -C "$NR/r-1" remote get-url origin 2>&1)"
  git -C "$NR/r-1" rev-parse -q --verify refs/remotes/origin/main >/dev/null \
    && ok "run-clone: origin's branches are fetched" || bad "run-clone: origin's branches are fetched"
  git --git-dir="$NR/gitmain.git" rev-parse -q --verify refs/heads/side >/dev/null \
    && bad "run-clone: only the base branch is cloned" "the main checkout's side branch came along" \
    || ok "run-clone: only the base branch is cloned"
  [ -d "$NR/workers" ] && ok "run-clone: creates workers/" || bad "run-clone: creates workers/"
  obj="$(cd "$NR/gitmain.git/objects" && find . -type f -path './??/*' | head -1)"
  if [ -n "$obj" ] && [ "$(stat -c %h "$NR/gitmain.git/objects/$obj" 2>/dev/null || stat -f %l "$NR/gitmain.git/objects/$obj")" = 1 ]; then
    ok "run-clone: objects are copied, not hardlinked to the main checkout's"
  else
    bad "run-clone: objects are copied, not hardlinked to the main checkout's" "${obj:-no loose object}"
  fi
  [ "$(main_sum)" = "$before" ] && ok "run-clone: the main checkout's .git is unchanged" \
    || bad "run-clone: the main checkout's .git is unchanged"
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NR" --run-id r-2 2>&1)"; then
    bad "run-clone: refuses a non-empty run root" "succeeded"
  else
    have "run-clone: refuses a non-empty run root" "is not empty" "$o"
  fi
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$GM/inner" --run-id r-3 2>&1)"; then
    bad "run-clone: refuses a run root inside the repo" "succeeded"
  else
    have "run-clone: refuses a run root inside the repo" "inside a checkout of the repo" "$o"
  fi
  # A linked worktree of the repo is a checkout too: a run root there would go
  # with the worktree when it is torn down.
  git -C "$GM" worktree add -q "$NO/linked" -b linked 2>/dev/null
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NO/linked/rr" --run-id r-4 2>&1)"; then
    bad "run-clone: refuses a run root inside a linked worktree of the repo" "succeeded"
  else
    have "run-clone: refuses a run root inside a linked worktree of the repo" "inside a checkout of the repo" "$o"
  fi
  # Reached through a symlink: refused before anything is created inside.
  ln -s "$GM" "$NO/to-main"
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NO/to-main/via-link/rr" --run-id r-5 2>&1)"; then
    bad "run-clone: refuses a run root reached through a symlink into the repo" "succeeded"
  else
    have "run-clone: refuses a run root reached through a symlink into the repo" "inside a checkout of the repo" "$o"
  fi
  [ -e "$GM/via-link" ] && bad "run-clone: a symlinked in-repo run root is refused before it is created" \
    || ok "run-clone: a symlinked in-repo run root is refused before it is created"
  [ "$(git --git-dir="$NR/gitmain.git" config --get auto-pilot.mainCheckout 2>/dev/null)" = "$(cd "$GM" && pwd -P)" ] \
    && ok "run-clone: records the main checkout for pre-flight" \
    || bad "run-clone: records the main checkout for pre-flight" "$(git --git-dir="$NR/gitmain.git" config --get auto-pilot.mainCheckout 2>&1)"
  [ -e "$GM/inner" ] && bad "run-clone: a refused in-repo run root is not created" || ok "run-clone: a refused in-repo run root is not created"
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NO/rr-bad" --run-id 'a/b' 2>&1)"; then
    bad "run-clone: refuses a run id that is not bare" "succeeded"
  else
    have "run-clone: refuses a run id that is not bare" "must be a bare id" "$o"
  fi
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NO/rr-base" --run-id r --base nosuch 2>&1)"; then
    bad "run-clone: refuses a --base that is not a local branch" "succeeded"
  else
    have "run-clone: refuses a --base that is not a local branch" "is not a local branch" "$o"
  fi
  [ -z "$(ls -A "$NO/rr-base" 2>/dev/null)" ] && ok "run-clone: a refused --base leaves the run root empty" \
    || bad "run-clone: a refused --base leaves the run root empty" "$(ls -A "$NO/rr-base")"
  if o="$("$SCRIPT" run-clone --repo "$GM" --run-root "$NO/rr-remote" --run-id r --remote nosuch 2>&1)"; then
    bad "run-clone: refuses a --remote the repo does not have" "succeeded"
  else
    have "run-clone: refuses a --remote the repo does not have" "has no remote 'nosuch'" "$o"
  fi

  out="$(nrender "$NO/p-git.json" --claude-token --git-worktree "$NR/r-1" 2>&1)"
  have "nono render --git-worktree: succeeds on a run clone" "nono profile OK" "$out"
  gflat="$(nflat "$NO/p-git.json" 2>&1)"
  have "nono render --git-worktree: the run clone's git dir is writable" "$(printf 'allow\t%s/gitmain.git' "$NR")" "$gflat"
  lack "nono render --git-worktree: nothing of the main checkout's .git is granted" "$GM/.git" "$gflat"
  git -C "$GM" worktree add -q "$NO/gitwt" -b run 2>/dev/null
  if o="$(nrender "$NO/p-git1.json" --git-worktree "$NO/gitwt" 2>&1)"; then
    bad "nono render --git-worktree: refuses a linked worktree of a main checkout" "render succeeded"
  else
    have "nono render --git-worktree: refuses a linked worktree of a main checkout" "not of a run clone" "$o"
  fi
  if o="$(nrender "$NO/p-git2.json" --git-worktree "$GM" 2>&1)"; then
    bad "nono render --git-worktree: refuses a main checkout" "render succeeded"
  else
    have "nono render --git-worktree: refuses a main checkout" "is a main checkout" "$o"
  fi
  if o="$(nrender "$NO/p-git3.json" --git-worktree "$NO/tmp" 2>&1)"; then
    bad "nono render --git-worktree: refuses a non-checkout" "render succeeded"
  else
    have "nono render --git-worktree: refuses a non-checkout" "not a git checkout" "$o"
  fi
  # A bare repo elsewhere is not this run's clone: the clone must sit in the
  # worktree's own run root, under the run's write grant.
  git -C "$NR/gitmain.git" worktree add -q "$NO/elsewhere" -b stray 2>/dev/null
  if o="$(nrender "$NO/p-git4.json" --git-worktree "$NO/elsewhere" 2>&1)"; then
    bad "nono render --git-worktree: refuses a clone outside the worktree's run root" "render succeeded"
  else
    have "nono render --git-worktree: refuses a clone outside the worktree's run root" "not of a run clone" "$o"
  fi

  # --- jail-run: resume's doctor goes through the run's own jail -------------
  # A stand-in nono records how it was called, then runs what follows `--`.
  JR="$NO/jailrun"
  mkdir -p "$JR"
  printf '#!/bin/sh\n{ echo "cwd=$PWD"; echo "tmp=$TMPDIR"; echo "argv=$*"; } >%s\nwhile [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n' \
    "$JR/called" >"$JR/nono"
  chmod +x "$JR/nono"
  out="$("$SCRIPT" jail-run --jail nono --profile "$NO/p-git.json" --nono-bin "$JR/nono" \
    --workdir "$NR/r-1" -- check-run-clone --worktree "$NR/r-1" 2>&1)"
  rc=$?
  if [ "$rc" = 0 ]; then ok "jail-run: runs the subcommand and passes its exit status"; else bad "jail-run: runs the subcommand and passes its exit status" "rc=$rc $out"; fi
  have "jail-run: the subcommand's output comes back" "$NR/gitmain.git" "$out"
  jcalled="$(cat "$JR/called" 2>/dev/null)"
  have "jail-run: nono gets the run's profile" "argv=run --silent --profile $NO/p-git.json --" "$jcalled"
  have "jail-run: the subcommand runs in the run worktree" "cwd=$NR/r-1" "$jcalled"
  have "jail-run: TMPDIR is the profile's" "tmp=$NTMP" "$jcalled"
  "$SCRIPT" jail-run --jail nono --profile "$NO/p-git.json" --nono-bin "$JR/nono" \
    --workdir "$NR/r-1" -- check-run-clone --worktree "$NO/tmp" >/dev/null 2>&1
  rc=$?
  if [ "$rc" != 0 ]; then ok "jail-run: a failing subcommand fails jail-run"; else bad "jail-run: a failing subcommand fails jail-run"; fi
  for jr_bad in "--jail nono --profile $NO/p-git.json --workdir $NR/r-1" \
    "--jail nono --nono-bin $JR/nono --workdir $NR/r-1" \
    "--jail bogus --profile $NO/p-git.json --workdir $NR/r-1"; do
    # shellcheck disable=SC2086 # the flag set is split on purpose
    if "$SCRIPT" jail-run $jr_bad -- check-run-clone --worktree "$NR/r-1" >/dev/null 2>&1; then
      bad "jail-run: refuses an incomplete jail ($jr_bad)"
    else
      ok "jail-run: refuses an incomplete jail ($jr_bad)"
    fi
  done
else
  echo "skip - run-clone / render-nono-profile --git-worktree: python3 or git not available"
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
