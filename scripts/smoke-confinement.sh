#!/usr/bin/env bash
# smoke-confinement.sh — the PRE-484 confinement smoke, as an executable so it
# never has to be pasted. Proves the generated jail actually REFUSES disallowed
# filesystem writes/reads, unlisted exec, and — the load-bearing test — raw-socket
# network egress. check.sh proves generation+compile; THIS proves confinement.
#
# Run:  bash scripts/smoke-confinement.sh
# macOS only (sandbox-exec / launchctl). Runs a few real `claude -p` invocations
# under the jail (uses your credentials + a little usage). Exits non-zero if any
# confinement check fails.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SO="$ROOT/scripts/spawn-orchestrator.sh"
command -v sandbox-exec >/dev/null 2>&1 || {
  echo "sandbox-exec required (macOS only)"
  exit 2
}

D="$(mktemp -d)"
D="$(cd "$D" && pwd -P)"
trap 'launchctl bootout "gui/$(id -u)/com.autopilot.smoke" 2>/dev/null; rm -rf "$D"' EXIT
mkdir -p "$D/run/wt" "$D/creds"
echo secret >"$D/creds/token"

pass=0
fail=0
indet=0
PASS() {
  pass=$((pass + 1))
  printf '  \033[32mPASS\033[0m  %s\n' "$1"
}
FAIL() {
  fail=$((fail + 1))
  printf '  \033[31mFAIL\033[0m  %s\n' "$1"
}
INDET() {
  indet=$((indet + 1))
  printf '  \033[33m????\033[0m  %s\n' "$1"
}

echo "== 0. Build the jail =="
CLAUDE="$(command -v claude || true)"
[ -n "$CLAUDE" ] || {
  echo "claude not on PATH"
  exit 2
}
# /usr/bin/security is NOT decoration: the harness reads its credentials from the
# macOS keychain by SPAWNING it, so a cold credential cache inside the jail makes
# `claude -p` die outright — "EPERM: operation not permitted, posix_spawn
# 'security'" — and §1b/§2 below then fail or go indeterminate for a reason that
# has nothing to do with what they test. It reproduces only on a cold cache, which
# is exactly what makes it a nasty intermittent. This is a SMOKE-only grant: a real
# launch passes --toolchain, which already covers it via the /usr/bin subpath.
# /usr/bin/curl is granted for the same reason, and its absence was corrupting §2's
# VERDICT: with curl unexecutable, every curl-based egress probe returned rc=126
# ("cannot execute") — so (b) and (c) reported "blocked, PASS" while proving
# nothing, because curl never ran to be blocked. An egress test that passes when
# the network is wide open is worse than no test. Grant it, and let layer 2 be what
# blocks it.
"$SO" render-profile --confine-under "$D/run" \
  --rw "$D/run/wt" --ro "$ROOT" --ro "$HOME/.claude" \
  --tmpdir "$D/run/wt/tmp" \
  --exec "$CLAUDE" --exec "$(command -v bash)" --exec "$(command -v git)" \
  --exec /usr/bin/security --exec /usr/bin/curl \
  --out "$D/profile.sb" || {
  echo "render-profile failed"
  exit 2
}
"$SO" check-profile "$D/profile.sb" || {
  echo "profile does not compile"
  exit 2
}
# A SECOND profile, rendered with --toolchain — what a real launch actually uses.
# The minimal profile above grants only literal binaries, which is why it cannot
# catch the shim/symlink defect class §1c exists for: an exec grant names a path,
# but Seatbelt matches the RESOLVED target, and on a stock host the run's two most
# important binaries both indirect (/usr/bin/git is a CLT shim that re-execs under
# xcode-select -p; /opt/homebrew/bin/gh is a symlink into Cellar). Both were on an
# allowlist and neither could run.
"$SO" render-profile --confine-under "$D/run" \
  --rw "$D/run/wt" --ro "$ROOT" --ro "$HOME/.claude" \
  --tmpdir "$D/run/wt/tmp" --toolchain \
  --exec "$CLAUDE" --exec "$(command -v bash)" --exec "$(command -v git)" \
  --out "$D/profile-toolchain.sb" || {
  echo "render-profile --toolchain failed"
  exit 2
}
"$SO" check-profile "$D/profile-toolchain.sb" || {
  echo "toolchain profile does not compile"
  exit 2
}
"$SO" render-settings --source plan --coder codex --out "$D/settings.json" || exit 2
echo "  profile + settings built"

# denied() runs a single action under the seatbelt profile ONLY and passes when it
# is refused (non-zero). allowed() passes when it succeeds (zero). PROFILE selects
# which rendered profile they run under (§1c swaps in the --toolchain one).
PROFILE="$D/profile.sb"
run_jailed() { sandbox-exec -f "$PROFILE" "$@" >/dev/null 2>&1; }
denied() {
  local d="$1"
  shift
  if run_jailed "$@"; then FAIL "$d (was ALLOWED — breach)"; else PASS "$d (denied)"; fi
}
allowed() {
  local d="$1"
  shift
  if run_jailed "$@"; then PASS "$d (allowed)"; else FAIL "$d (was denied)"; fi
}

echo "== 1. Layer 1 — filesystem + exec =="
denied "write outside the worktree" bash -c "echo x > $HOME/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST"
allowed "write inside the worktree" bash -c "echo x > $D/run/wt/ok"
denied "read /etc/sudoers" bash -c "cat /etc/sudoers"
denied "exec unlisted /usr/bin/python3" /usr/bin/python3 -c "print(1)"
# The harness-runtime grant (task 12) opens ~/.claude/session-env for writes. It
# must NOT have opened the rest of ~/.claude — a blanket state-dir write would
# put the credential file in a writable scope (the very thing --cred-ro exists to
# prevent). Assert the neighbouring write is still refused.
denied "write elsewhere under ~/.claude (session-env grant is not blanket)" \
  bash -c "echo x > $HOME/.claude/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST"
rm -f "$HOME/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST" "$HOME/.claude/AUTOPILOT_SMOKE_SHOULD_NOT_EXIST" 2>/dev/null

# --- exec grants must RUN, not merely be listed (task 10) -------------------
# The regression guard for the shim/symlink defect class. Every §1 exec assertion
# above is either a denial or uses `bash` — a real binary, not a shim — so a grant
# that resolves to something ungranted sails straight through. That is exactly how
# `git` shipped broken: line 51 has passed `--exec "$(command -v git)"` since day
# one, the grant was asserted, and git was NEVER RUN inside the jail. It could not
# have run: /usr/bin/git is a 119k Command Line Tools shim that re-execs
# /Library/Developer/CommandLineTools/usr/bin/git, which nothing granted —
# "can't exec … (errno=Operation not permitted)". `gh` was the same shape via
# Homebrew's Cellar symlink farm. Assert EXECUTION, under the --toolchain profile a
# real launch uses. A listed-but-unrunnable binary must fail this suite.
echo "== 1c. Exec grants actually EXECUTE (--toolchain profile) =="
PROFILE="$D/profile-toolchain.sb"
# PIN the paths — do NOT let $PATH pick the binary. Each assertion below exists to
# prove ONE grant, and an ambient lookup can satisfy it via a DIFFERENT one: a host
# with Homebrew git first on PATH would exercise the Cellar grant here, so deleting
# the xcode-select grant would leave this test green — a regression guard that no
# longer guards the regression. /usr/bin/git IS the CLT shim this covers.
allowed "exec git (CLT shim re-execs its real target)" /usr/bin/git --version
if [ -x /opt/homebrew/bin/gh ]; then
  allowed "exec gh (Homebrew bin/ symlink into Cellar)" /opt/homebrew/bin/gh --version
else
  INDET "exec gh (no Homebrew gh on this host — a real run needs it)"
fi
# Not just `--version`: a real commit forks the git-core helpers out of the
# toolchain's libexec, a second exec hop that a bin-only grant would miss.
allowed "git commit end-to-end (forks git-core helpers)" bash -c \
  "cd $D/run/wt && rm -rf gitrepo && /usr/bin/git init -q gitrepo && cd gitrepo \
     && /usr/bin/git -c user.email=a@b -c user.name=c commit -q --allow-empty -m smoke \
     && /usr/bin/git log --oneline -1"
# RE-EXAMINED, not flipped (task 10 acceptance): this denial's intent is "an
# un-granted interpreter cannot run", and it still holds verbatim under the
# toolchain profile — CLT's python3 resolves to <dev>/Library/Frameworks/…, which
# is OUTSIDE the <dev>/usr subpath the git fix grants. The narrow grant is what
# keeps this assertion meaningful; a whole-<dev> grant would have flipped it to an
# allow. Asserted under BOTH profiles deliberately, so a future widening of the
# toolchain grant trips here instead of passing quietly.
denied "exec unlisted /usr/bin/python3 (still unlisted under --toolchain)" \
  /usr/bin/python3 -c "print(1)"
# The coarse exec wall's real load-bearing property: exec dirs are all
# non-writable, and writable scopes are never exec-granted, so a binary the agent
# STAGES cannot be run. This is what makes the launchctl/open exec denies complete.
# The fixture MUST fail closed: denied() passes on ANY non-zero exit, so a silently
# failed cp would leave no file and the assertion would pass on ENOENT instead of on
# a Seatbelt denial — the same pass-for-the-wrong-reason this whole section exists
# to kill. Assert the staged binary is real and executable before trusting the deny.
cp /bin/echo "$D/run/wt/staged-binary" || {
  echo "could not stage the exec fixture"
  exit 2
}
[ -x "$D/run/wt/staged-binary" ] || {
  echo "staged exec fixture is not executable"
  exit 2
}
denied "exec a binary staged in the RW worktree" "$D/run/wt/staged-binary" hi
PROFILE="$D/profile.sb"

# --- exit-code integrity (task 12 / finding #20) --------------------------
# A jail that can't report a correct exit code is a BROKEN jail. When the
# harness's OWN runtime surface is denied, the Bash tool dies with EPERM BEFORE
# it ever runs the command (it can't mkdir its per-session dirs), and every Bash
# tool call reports exit 1 regardless of the command's real result — so an agent
# verifying by `$?` is reading pure noise. This is the real regression guard for
# task 12: it FAILS against the pre-fix profile and PASSES once the harness's own
# runtime paths are permitted (the renderer emits them; see @@HARNESS_RUNTIME@@).
# It must run THROUGH `claude -p` itself, not bare sandbox-exec — only the real
# harness exercises the session-env/scratch mkdirs and the mux socket.
echo "== 1b. Exit-code integrity through the harness (claude -p) =="
JSON="$(cat "$D/settings.json")"
EC_LOG="$D/exit-code.log"
# TMPDIR is part of the launch CONTRACT, not ambience: write-launch exports the
# profile's @spawn-tmpdir stamp — the exact dir the srt-mux socket grant is
# anchored to — and fails closed on a render/launch mismatch precisely because a
# drift makes the harness's inner sandbox silently degrade. Invoking claude here
# through bare sandbox-exec does NOT inherit that, so without this export the
# harness binds its mux socket in the ambient /var/folders TMPDIR, which the
# profile does not grant; the inner sandbox then disables ITSELF, layer 2 stops
# enforcing, and §2's egress deciders below grade a jail that has quietly become
# one-layer. Reproduce the launch contract, or §2 is theatre.
JAIL_TMPDIR="$D/run/wt/tmp"
mkdir -p "$JAIL_TMPDIR"
jailed_claude() { TMPDIR="$JAIL_TMPDIR" sandbox-exec -f "$D/profile.sb" "$CLAUDE" "$@"; }
exit_code_check() { # <desc> <expect-rc> <shell-command> <rcfile-suffix>
  local desc="$1" expect="$2" cmd="$3" name="rc_ec_$4"
  local rcf="$D/run/wt/$name"
  rm -f "$rcf"
  jailed_claude -p \
    "Run exactly this one bash command and then stop, nothing else: { $cmd ; } ; printf '%s' \$? > $rcf" \
    --permission-mode bypassPermissions --settings "$JSON" --max-turns 4 \
    --verbose --output-format stream-json >>"$EC_LOG" 2>&1
  # No rc file is NOT indeterminate here — it is the exact pre-fix symptom (the
  # Bash tool EPERM'd before running anything), so it must FAIL, not INDET.
  if [ ! -s "$rcf" ]; then
    FAIL "$desc (no rc recorded — the Bash tool never ran; harness runtime paths denied?)"
    return
  fi
  local rc
  rc="$(cat "$rcf")"
  [ "$rc" = "$expect" ] && PASS "$desc (rc=$rc)" || FAIL "$desc (rc=$rc, expected $expect — the jail is lying about exit codes)"
}
exit_code_check "true reports exit 0" 0 true true
exit_code_check "false reports exit 1" 1 false false
# The inner sandbox must actually initialize — a denied mux-socket bind/listen
# makes the harness silently disable its own sandboxing, degrading the documented
# two-layer posture to one layer.
if grep -q 'Sandbox is enabled but failed to initialize' "$EC_LOG" 2>/dev/null; then
  FAIL "inner sandbox initializes (found 'failed to initialize' — mux socket denied)"
else
  PASS "inner sandbox initializes (no 'failed to initialize' line)"
fi

echo "== 2. Layer 2 — network egress (through claude --settings) =="
# egress_check writes the curl/socket exit code to a file in the RW worktree from
# INSIDE the jailed claude, so we read a deterministic rc instead of parsing prose.
egress_check() { # <desc> <expect: reach|block> <rcfile-name> <shell-command>
  local desc="$1" expect="$2" name="$3" cmd="$4"
  local rcf="$D/run/wt/$name"
  rm -f "$rcf"
  jailed_claude -p \
    "Run exactly this one bash command and then stop, nothing else: { $cmd ; } ; printf '%s' \$? > $rcf" \
    --permission-mode bypassPermissions --settings "$JSON" --max-turns 4 >/dev/null 2>&1
  if [ ! -s "$rcf" ]; then
    INDET "$desc (claude didn't record an rc — check manually)"
    return
  fi
  local rc
  rc="$(cat "$rcf")"
  case "$expect" in
    reach) [ "$rc" = 0 ] && PASS "$desc (reached, rc=0)" || FAIL "$desc (rc=$rc — allowlisted host was blocked)" ;;
    block) [ "$rc" != 0 ] && PASS "$desc (blocked, rc=$rc)" || FAIL "$desc (rc=0 — EGRESS ESCAPED)" ;;
  esac
}
egress_check "(a) allowlisted api.github.com" reach rc_a 'curl -sS --max-time 8 -o /dev/null https://api.github.com'
egress_check "(b) non-allowlisted example.com" block rc_b 'curl -sS --max-time 8 -o /dev/null https://example.com'
egress_check "(c) raw IP 1.1.1.1 [decider]" block rc_c 'curl -sS --max-time 8 -o /dev/null http://1.1.1.1'
egress_check "(d) raw socket /dev/tcp [decider]" block rc_d 'exec 3<>/dev/tcp/1.1.1.1/80'

echo "== 3. Detach + supervisor lifecycle =="
printf 'noop\n' >"$D/run/wt/prompt.txt"
if "$SO" write-launch --profile "$D/profile.sb" --settings "$D/settings.json" \
  --workdir "$D/run/wt" --log "$D/orch.log" --prompt-file "$D/run/wt/prompt.txt" \
  --until T --label com.autopilot.smoke --claude-bin "$CLAUDE" --path "$PATH" --tmpdir "$D/run/wt/tmp" \
  --out-script "$D/launch.sh" --out-plist "$D/job.plist" >/dev/null 2>&1 \
  && plutil -lint "$D/job.plist" >/dev/null 2>&1; then
  PASS "write-launch produced a valid plist"
else
  FAIL "write-launch / plist"
fi
if launchctl bootstrap "gui/$(id -u)" "$D/job.plist" 2>/dev/null \
  && launchctl print "gui/$(id -u)/com.autopilot.smoke" 2>/dev/null | grep -q 'pid = '; then
  PASS "job bootstrapped with a pid"
else
  INDET "launchd bootstrap (may need Full Disk Access / a real login session)"
fi
"$SO" teardown --label com.autopilot.smoke >/dev/null 2>&1
launchctl print "gui/$(id -u)/com.autopilot.smoke" >/dev/null 2>&1 \
  && FAIL "teardown left the job loaded" || PASS "teardown removed the job"

echo
echo "== Summary =="
printf '  %d passed, %d failed, %d indeterminate\n' "$pass" "$fail" "$indet"
[ "$fail" = 0 ] && echo "  ✅ confinement holds — the (c)/(d) egress deciders passed if listed above." \
  || echo "  ❌ a wall did NOT hold — see FAIL lines (a (c)/(d) FAIL means raw egress escaped → layer-1 fix needed)."
[ "$fail" = 0 ]
