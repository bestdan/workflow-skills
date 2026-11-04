#!/usr/bin/env bash
# smoke-nono.sh — the Linux confinement smoke (#1349): proves the rendered nono
# profile refuses what it must and still runs the orchestrator, through the same
# invocation the generated wrapper uses (write-launch --jail nono): the pinned
# nono, `run --silent --profile`, NONO_NO_UPDATE_CHECK/NONO_NO_MIGRATE, the
# profile's own TMPDIR, the run dir as cwd, and the token handed to the jailed
# process alone as CLAUDE_CODE_OAUTH_TOKEN.
#
# Usage:
#   smoke-nono.sh --nono-bin <path> [--claude-token-cmd '<cmd>'] [--claude-bin <path>]
#
# Prints one `SMOKE <CHECK>: pass|FAIL|skip (<detail>)` line per assertion and
# exits 0 only when none FAILed (2 on bad usage or a broken scratch setup):
#   VALIDATE      `nono profile validate` accepts the rendered profile
#   WRITE_INSIDE  a write inside the run dir succeeds
#   WRITE_HOME    a write to $HOME is denied
#   WRITE_TMP     a write to /tmp outside the run's TMPDIR is denied
#   CRED_READ     ~/.claude/.credentials.json is not readable  (token command only)
#   CRED_WRITE    ~/.claude/.credentials.json is not writable  (token command only)
#   BUS_STAGED    a copy of systemctl staged in the run dir cannot reach the user bus
#   BUS_DIRECT    /usr/bin/systemctl cannot reach it either
#   GIT_COMMIT    a commit in the run worktree (a linked worktree) succeeds
#   GIT_HOOKS     a write to the main checkout's .git/hooks is denied
#   GIT_CONFIG    a write to the main checkout's .git/config is denied
#   CLAUDE        `claude -p 'ok' --max-turns 1` answers through the jail
#   CRED_UNTOUCHED  that run left .credentials.json byte-for-byte unchanged
#
# Without a token command the credential checks are skipped and the profile is
# the documented fallback (~/.claude writable), which this smoke says out loud.
# The CLAUDE check makes one real one-turn model call.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SO="$ROOT/scripts/spawn-orchestrator.sh"
nono="" token_cmd="" claude_bin=""
while [ $# -gt 0 ]; do
  case "$1" in
    --nono-bin | --claude-token-cmd | --claude-bin)
      [ $# -ge 2 ] || {
        echo "smoke-nono: missing value for $1" >&2
        exit 2
      }
      case "$1" in
        --nono-bin) nono="$2" ;;
        --claude-token-cmd) token_cmd="$2" ;;
        --claude-bin) claude_bin="$2" ;;
      esac
      shift 2
      ;;
    *)
      echo "smoke-nono: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done
[ -x "$nono" ] || {
  echo "smoke-nono: --nono-bin must name an executable nono" >&2
  exit 2
}
[ -n "$claude_bin" ] || claude_bin="$(command -v claude 2>/dev/null || true)"

fails=0
say() { # <check> <pass|FAIL|skip> <detail>
  echo "SMOKE $1: $2 ($3)"
  [ "$2" = FAIL ] && fails=$((fails + 1))
  return 0
}

S="$(mktemp -d "${TMPDIR:-/tmp}/smoke-nono.XXXXXX")" || exit 2
[ -n "$S" ] && [ -d "$S" ] || exit 2
S="$(cd "$S" && pwd -P)" || exit 2
home_probe="$HOME/.auto-pilot-smoke-$$"
tmp_probe="/tmp/auto-pilot-smoke-$$"
trap 'rm -rf "$S"; rm -f "$home_probe" "$tmp_probe"' EXIT
mkdir -p "$S/tmp"

# The run dir is a linked worktree of a scratch repo, as a real run's is, so
# the git grants (--git-worktree) are tested on the shape they exist for. The
# scratch repo never reads the owner's global config or hooks.
have_git=0
if command -v git >/dev/null 2>&1 \
  && GIT_CONFIG_GLOBAL=/dev/null git init -q -b main "$S/repo" \
  && GIT_CONFIG_GLOBAL=/dev/null git -C "$S/repo" -c user.name=smoke -c user.email=smoke@invalid commit -q --allow-empty -m init \
  && GIT_CONFIG_GLOBAL=/dev/null git -C "$S/repo" worktree add -q "$S/run" -b smoke-run 2>/dev/null; then
  have_git=1
else
  mkdir -p "$S/run"
fi

# The same renderers the launch uses, with this run's shape: the run dir
# read-write, gh's config read-only, the host toolchain readable.
render=(--rw "$S/run" --tmpdir "$S/tmp" --toolchain --out "$S/profile.json")
[ "$have_git" = 1 ] && render+=(--git-worktree "$S/run")
[ -d "$HOME/.config/gh" ] && render+=(--cred-ro "$HOME/.config/gh")
[ -n "$token_cmd" ] && render+=(--claude-token)
if ! bash "$SO" render-nono-profile "${render[@]}" >"$S/render.log" 2>&1; then
  say RENDER FAIL "render-nono-profile failed: $(tail -1 "$S/render.log")"
  exit 1
fi
bash "$SO" render-settings --jail nono --source gh-issue --out "$S/settings.json" >/dev/null 2>&1 || {
  say RENDER FAIL "render-settings --jail nono failed"
  exit 1
}
jail_tmp="$(sed -n 's/^      "TMPDIR": "\(.*\)"$/\1/p' "$S/profile.json" | tail -1)"

# The wrapper's jail line, minus the claude arguments.
jail() {
  (cd "$S/run" && NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1 TMPDIR="$jail_tmp" \
    "$nono" run --silent --profile "$S/profile.json" -- "$@")
}

if out="$(NONO_NO_UPDATE_CHECK=1 "$nono" profile validate "$S/profile.json" 2>&1)" && grep -q 'Result: valid' <<<"$out"; then
  say VALIDATE pass "nono profile validate"
else
  say VALIDATE FAIL "$(tail -1 <<<"$out")"
fi

if jail /bin/sh -c 'echo x > inside' >/dev/null 2>&1 && [ -f "$S/run/inside" ]; then
  say WRITE_INSIDE pass "the run dir is writable"
else
  say WRITE_INSIDE FAIL "a write inside the run dir failed — the jail cannot do the run's work"
fi

jail /bin/sh -c ': > "$1"' _ "$home_probe" >/dev/null 2>&1
if [ -e "$home_probe" ]; then
  say WRITE_HOME FAIL "a write to \$HOME succeeded"
else
  say WRITE_HOME pass "a write to \$HOME is denied"
fi

jail /bin/sh -c ': > "$1"' _ "$tmp_probe" >/dev/null 2>&1
if [ -e "$tmp_probe" ]; then
  say WRITE_TMP FAIL "a write to /tmp outside the run's TMPDIR succeeded"
else
  say WRITE_TMP pass "/tmp outside the run's TMPDIR is denied"
fi

if [ "$have_git" = 0 ]; then
  say GIT_COMMIT skip "no git, or the scratch repo could not be built"
  say GIT_HOOKS skip "no scratch repo"
  say GIT_CONFIG skip "no scratch repo"
else
  # --no-verify and a null global config: this proves the grants, not the
  # owner's hooks.
  if jail /bin/sh -c 'echo x > f && git add f && GIT_CONFIG_GLOBAL=/dev/null git -c user.name=smoke -c user.email=smoke@invalid commit -q --no-verify -m smoke' >/dev/null 2>&1 \
    && [ "$(GIT_CONFIG_GLOBAL=/dev/null git -C "$S/run" log -1 --format=%s 2>/dev/null)" = smoke ]; then
    say GIT_COMMIT pass "a commit in the run worktree succeeds"
  else
    say GIT_COMMIT FAIL "a commit in the run worktree failed — the run cannot record its state"
  fi
  jail /bin/sh -c ': > "$1"' _ "$S/repo/.git/hooks/post-checkout" >/dev/null 2>&1
  if [ -e "$S/repo/.git/hooks/post-checkout" ]; then
    say GIT_HOOKS FAIL "a write to the main checkout's .git/hooks succeeded — a hook there runs outside the jail"
  else
    say GIT_HOOKS pass "the main checkout's .git/hooks is not writable"
  fi
  before_cfg="$(cksum <"$S/repo/.git/config")"
  jail /bin/sh -c 'printf "[core]\\n" >> "$1"' _ "$S/repo/.git/config" >/dev/null 2>&1
  if [ "$(cksum <"$S/repo/.git/config")" != "$before_cfg" ]; then
    say GIT_CONFIG FAIL "a write to the main checkout's .git/config succeeded"
  else
    say GIT_CONFIG pass "the main checkout's .git/config is not writable"
  fi
fi

cred="$HOME/.claude/.credentials.json"
if [ -z "$token_cmd" ]; then
  say CRED_READ skip "no token command: ~/.claude is writable in the jail, the documented fallback"
  say CRED_WRITE skip "no token command"
elif [ ! -e "$cred" ]; then
  say CRED_READ skip "no $cred on this host"
  say CRED_WRITE skip "no $cred on this host"
else
  if jail /bin/sh -c 'head -c 1 "$1" >/dev/null' _ "$cred" >/dev/null 2>&1; then
    say CRED_READ FAIL "the credential file is readable in the jail"
  else
    say CRED_READ pass "the credential file is not readable"
  fi
  if jail /bin/sh -c ': >> "$1"' _ "$cred" >/dev/null 2>&1; then
    say CRED_WRITE FAIL "the credential file is writable in the jail"
  else
    say CRED_WRITE pass "the credential file is not writable"
  fi
fi

# A reachable user manager answers is-system-running with its state; a refused
# socket prints "Failed to connect to bus". Either non-answer passes, an answer fails.
bus_check() { # <check> <command...>
  local name="$1" o
  shift
  o="$(jail "$@" 2>&1)"
  if grep -Eq '^(running|degraded|starting|initializing|maintenance|stopping|offline)$' <<<"$o"; then
    say "$name" FAIL "it reached the user service manager: $o"
  else
    say "$name" pass "$(head -1 <<<"$o")"
  fi
}
sysctl="$(command -v systemctl 2>/dev/null || true)"
if [ -z "$sysctl" ]; then
  say BUS_STAGED skip "no systemctl on this host"
  say BUS_DIRECT skip "no systemctl on this host"
else
  bus_check BUS_STAGED /bin/sh -c 'cp "$1" ./sc && ./sc --user is-system-running; rm -f ./sc' _ "$sysctl"
  # From a child shell, as the agent would: nono's own startup-command block
  # stops a top-level systemctl, which would pass this without testing the socket.
  bus_check BUS_DIRECT /bin/sh -c '"$1" --user is-system-running' _ "$sysctl"
fi

if [ -z "$claude_bin" ]; then
  say CLAUDE FAIL "claude not found on PATH"
else
  token="" before="" after=""
  [ -e "$cred" ] && before="$(cksum <"$cred") $(wc -c <"$cred")"
  if [ -n "$token_cmd" ]; then
    token="$(timeout 60 bash -c "$token_cmd" </dev/null 2>/dev/null)" || token=""
  fi
  if [ -n "$token_cmd" ] && [ -z "$token" ]; then
    say CLAUDE FAIL "the token command printed nothing, failed, or ran past 60s"
  else
    settings="$(cat "$S/settings.json")"
    # Prefix assignment, as the wrapper does: the token reaches the jailed
    # process and nothing else.
    if [ -n "$token" ]; then
      o="$(cd "$S/run" && CLAUDE_CODE_OAUTH_TOKEN="$token" NONO_NO_UPDATE_CHECK=1 NONO_NO_MIGRATE=1 TMPDIR="$jail_tmp" \
        "$nono" run --silent --profile "$S/profile.json" -- "$claude_bin" -p 'Reply with exactly: ok' --max-turns 1 \
        --permission-mode bypassPermissions --settings "$settings" --output-format json 2>"$S/claude.err")"
    else
      o="$(jail "$claude_bin" -p 'Reply with exactly: ok' --max-turns 1 \
        --permission-mode bypassPermissions --settings "$settings" --output-format json 2>"$S/claude.err")"
    fi
    token=""
    if grep -q '"is_error":false' <<<"$o" && grep -q '"result":"ok' <<<"$o"; then
      say CLAUDE pass "claude -p answered through the jail$([ -n "$token_cmd" ] && echo ', on the token command alone')"
    else
      say CLAUDE FAIL "claude -p did not answer through the jail: $(head -c 300 "$S/claude.err" | tr '\n' ' ')"
    fi
    if [ -n "$token_cmd" ] && [ -e "$cred" ]; then
      after="$(cksum <"$cred") $(wc -c <"$cred")"
      if [ "$before" = "$after" ]; then
        say CRED_UNTOUCHED pass "the credential file is unchanged"
      else
        say CRED_UNTOUCHED FAIL "the credential file changed during the jailed run"
      fi
    fi
  fi
fi

[ "$fails" = 0 ]
