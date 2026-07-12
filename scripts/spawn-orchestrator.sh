#!/usr/bin/env bash
# spawn-orchestrator.sh — materialize the auto-pilot detached orchestrator's
# launch wrapper and its two-layer jail from a run's resolved inputs, so an
# agent never hand-authors a sandbox-exec profile at launch (PRE-484).
#
# The jail is two layers (skills/auto-pilot/references/launch-runtime.md
# "Sandbox profile"):
#   1. filesystem + process/exec  → this script's Seatbelt profile (below)
#   2. host-level network egress  → the detached `claude -p`'s own
#      sandbox.network allowlist (added by a later task)
#
# Ships the layer-1 profile renderer + compile check (render-profile /
# check-profile) and the layer-2 egress-allowlist emitter (render-settings).
# Subcommands added by sibling tasks: write-launch, detach, teardown.
#
# Verify broker (task 4 — run the run's verify_command OUTSIDE the jail): the
# jailed orchestrator can't spawn an un-jailed verifier (children inherit the
# seatbelt profile) and `bash scripts/check.sh` execve-denies in-jail (finding
# #4). So write-verify-broker installs a SEPARATE, un-jailed launchd job that
# polls a sentinel dir; the orchestrator hands off with verify-request and reads
# the outcome with verify-await. The broker runs a COMMAND-PINNED verify only,
# in a worktree confined to the run root — see the "Task 4" block below.
#
# Usage:
#   spawn-orchestrator.sh render-profile \
#       --rw <path> [--rw <path> ...] \
#       --ro <path> [--ro <path> ...] \
#       --cred-ro <file> [--cred-ro <file> ...] \
#       --exec <path> [--exec <path> ...] \
#       --exec-dir <dir> [--exec-dir <dir> ...] [--toolchain] \
#       --out <file> [--template <file>]
#   spawn-orchestrator.sh render-settings \
#       --source <linear|plan> \
#       [--coder <codex|devin|agy> ...] [--agy-host <host>] \
#       [--mcp-host <host> ...] [--add-task-host <host> ...] [--npm] \
#       --out <file>
#   spawn-orchestrator.sh check-profile <file>
#   spawn-orchestrator.sh status --label <label> [--dir <run-dir>]
#   spawn-orchestrator.sh teardown --label <label> [--done-sentinel <path>]
#   spawn-orchestrator.sh assert-run-head --dir <run-worktree> --run-id <run_id> \
#       [--questions <QUESTIONS.md path>]
#
#   assert-run-head  The run-loop / --resume HEAD guard (PRE-hardening task 13):
#           asserts the run worktree's HEAD is `auto-pilot/<run_id>`; if not,
#           restores it (`git checkout`) and, with --questions, appends a
#           QUESTIONS.md entry recording the deviation (which then reaches
#           REPORT.md via the existing rolling rewrite). Fail-closed only if
#           the restore itself fails (e.g. uncommitted changes block checkout).
#   spawn-orchestrator.sh classify-exit --exit-code <n> --output <file> \
#       [--since-offset <bytes>]
#   spawn-orchestrator.sh supervisor-check --exit-code <n> --log <file> \
#       [--since-offset <bytes>] \
#       --dir <run-dir> --label <label> --state <file> [--no-progress-limit <n>]
#
#   classify-exit  Read-only, no model call (task 10 / finding #22): classify
#                  an orchestrator exit from its exit code + captured
#                  stdout+stderr. Prints exactly one of `done` (exit 0),
#                  `fatal: <reason>` (a non-retryable auth failure —
#                  authentication_failed / an expired-OAuth message / a 401 in
#                  an auth CONTEXT, never a bare `401` substring, which occurs
#                  incidentally in any transcript), or `retry: <reason>` (a
#                  rate-limit signal, or any other non-zero exit). Always exits
#                  0 — the classification is the payload, not a pass/fail
#                  signal. `--since-offset` bounds the read to the bytes THIS
#                  wake appended to the shared log, so a past wake's auth
#                  failure can't keep halting a run that has since been
#                  re-authenticated.
#   supervisor-check  The per-wake decision the generated launch script calls
#                  AFTER `claude -p` exits. Classifies the exit (above); a
#                  `fatal` classification halts immediately. A `retry`
#                  classification is checked against the no-progress guard:
#                  if the run-state branch HEAD (under --dir) hasn't moved
#                  across --no-progress-limit (default 3) consecutive
#                  non-zero wakes, it halts too — the general backstop that
#                  would have caught #22 even without matching the 401 string.
#                  A wake that lands while RUN.md's `status:` is `paused`
#                  never counts against the guard (a paused wake makes no
#                  progress by design — see run-budget.md "Two pause kinds").
#                  A halt writes run-level `status: systemic` + `pause_reason`
#                  to RUN.md, appends one alarm entry to REPORT.md, commits
#                  both to the run-state branch, and tears the supervisor job
#                  down (`launchctl bootout`) so it never relaunches into the
#                  same condition. Exits with the classified exit code (0 for
#                  `done`) so the launch script's own exit is meaningful.
#   spawn-orchestrator.sh restack --run-dir <dir> [--repo <path>] [--remote <name>] \
#       [--gh <path>] [--dry-run]
#
#   restack  Post-merge restack of stacked PRs (finding #25): reads RUN.md's
#            task table (base/base_sha/pr per task), and for every chained task
#            whose parent PR has merged, does the four-command incantation a
#            human must otherwise remember at the right moment — fetch,
#            `rebase --onto <remote>/<base_branch> <base_sha> <branch>`,
#            `push --force-with-lease`, `gh pr edit --base <base_branch>` —
#            in dependency order, idempotent (a PR already based on
#            base_branch is a no-op), fail-closed on a rebase conflict (aborts,
#            reports, never force-pushes). Also flags orphaned children (a PR
#            whose live base is a deleted or already-merged branch) as a
#            defect, whether or not this pass could fix them, and appends every
#            outcome — including the mandatory re-verify / stale-co-review
#            warning per restacked child — to the run's REPORT.md.
#            Every rebase runs in a THROWAWAY worktree: restack never moves the
#            caller's HEAD (finding #23 / task 13's run-HEAD invariant), and
#            asserts that on exit. `--gh` is mockable — pass a local stub so
#            the offline test suite never calls GitHub while the git mechanics
#            run against a real repo.
#
#   status  Read-only: report the run's live state in one shot — the RUN.md
#           run-level `status:`, the per-task phase table, the last
#           meaningful event from `orchestrator.log`, whether the recorded
#           orchestrator PID is actually live (guarding against a recycled
#           PID), the `--until` deadline, and whether the done-sentinel
#           (written by `teardown --done-sentinel`, see below) is present.
#           Never mutates anything. `--dir` defaults to $PWD; state is read
#           from <dir>/.auto-pilot/{RUN.md,orchestrator.log,orchestrator.done}.
#   teardown  Boot the launchd job out (`launchctl bootout`). With
#             `--done-sentinel <path>`, first atomically writes that file as
#             the durable completion marker, THEN boots the job out — so a
#             watcher polling the sentinel never observes "job gone, no
#             done-marker yet". This is the ONE completion mechanism `status`
#             also reads (see launch-runtime.md "Logs / observability").
#
#   render-settings  Emit the ephemeral `claude -p --settings` JSON: layer-2
#                    network egress (sandbox.network.allowedDomains) narrowed to
#                    the resolved coders + source, deny-by-default. Fail-closed on
#                    an unresolvable required host (e.g. agy without --agy-host).
#
#   render-profile  Render the Seatbelt (.sb) profile from resolved paths into
#                   --out. Every path must be ABSOLUTE and EXIST; a relative or
#                   missing path fails closed and no file is written.
#     --rw    A read-write scope (run/worker worktrees, $TMPDIR). Repeatable.
#     --ro    A read-only scope (rest of repo, credential stores). Repeatable.
#     --cred-ro  A credential FILE kept read-only even when it lives inside an
#                --rw state dir (a tool's own token under ~/.codex/~/.claude). The
#                state dir stays writable; only the token is denied writes, via a
#                specific (deny file-write* (literal …)) emitted after the RW block.
#                Must be an existing file. Repeatable.
#     --exec  A binary permitted to exec (resolved coder + base toolchain).
#             Repeatable.
#     --exec-dir  A directory permitted to exec (subpath, coarser than --exec).
#                 Must exist and must not be "/". Repeatable.
#     --toolchain  Convenience: add each of a standard set of bin dirs (that
#                  exist on this host) to the exec-dir set.
#     --out   Destination path for the rendered profile. Required.
#     --template  Override the profile template (default: scripts/orchestrator.sb.tmpl).
#     Every render ALSO emits fixed, RENDERER-OWNED (never caller-supplied) write
#     scopes for Claude Code's own runtime surface: the $TMPDIR srt-mux-*.sock mux
#     socket (file-write* + network-bind), the /tmp/claude-<uid> scratch/cwd tree,
#     and ~/.claude/session-env. Without them the harness's inner sandbox can't
#     initialize and the Bash tool EPERMs on its own mkdir BEFORE running anything
#     — poisoning every exit code to 1. Renderer-owned because two of the three
#     live outside the run root and would otherwise trip --confine-under
#     (task 12 / finding #20; see orchestrator.sb.tmpl's @@HARNESS_RUNTIME@@).
#   check-profile  Confirm a rendered profile COMPILES via `sandbox-exec -f`,
#                  surfacing the Seatbelt parser error verbatim on failure.
#
# Exit status and structured final line:
#   0  spawn-orchestrator: profile OK <path>       render/check succeeded
#   2  usage, dependency, or fail-closed path error (nothing partial written)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE_DEFAULT="$ROOT/scripts/orchestrator.sb.tmpl"
# The done-sentinel filename `status` looks for under <dir>/.auto-pilot/. Callers
# pass the matching absolute path (<dir>/.auto-pilot/orchestrator.done) to
# `teardown --done-sentinel`; the two agree on this one name so it's a single
# completion mechanism, never a second independent marker (launch-runtime.md
# "Logs / observability").
DONE_SENTINEL_NAME="orchestrator.done"
# Local supervisor bookkeeping for the no-progress guard (task 10) — the
# consecutive-failure counter + last-seen run-state HEAD. Lives beside RUN.md
# but is NEVER committed to the run-state branch itself (it's wake-to-wake
# scratch state, not part of the run's durable record).
SUPERVISOR_STATE_NAME="supervisor-state"

die() { echo "spawn-orchestrator: $*" >&2; exit 2; }

# Canonicalize an absolute, existing path. Prints the canonical path on success;
# on a fail-closed condition it writes the reason to stderr and RETURNS non-zero
# (never `exit` — this runs inside a $(command substitution), where `exit` would
# only kill the subshell and let the caller sail on). Callers must `|| exit 2`.
# Resolves via cd/pwd -P so no external `realpath` is required (not on all macOS).
canonicalize() {
  local p="$1"
  case "$p" in
    /*) ;;
    *) echo "spawn-orchestrator: path must be absolute (fail-closed): $p" >&2; return 1 ;;
  esac
  # A newline in a path would break the line-oriented SBPL emission (`(allow …)`
  # rules, the `;; @spawn-tmpdir:` stamp) — a crafted dir name could smuggle its
  # own rule or a fake stamp line. No legitimate path here contains one; reject
  # fail-closed, same posture as the egress-host newline guard.
  case "$p" in *$'\n'*) echo "spawn-orchestrator: path contains a newline (fail-closed)" >&2; return 1 ;; esac
  [ -e "$p" ] || { echo "spawn-orchestrator: path does not exist (fail-closed): $p" >&2; return 1; }
  if [ -d "$p" ]; then
    (cd "$p" && pwd -P)
  else
    # Resolve the file's symlink chain: Seatbelt matches process-exec (and file
    # rules) against the vnode's canonical target, not the symlink literal. Many
    # macOS binaries are symlinks (e.g. /opt/homebrew/bin/gh → Cellar/…), and
    # `command -v` hands back the symlink — so authorizing only the symlink path
    # would silently deny the exec. Follow links, re-canonicalizing the dir each
    # hop, with a loop guard.
    local d b target guard=0
    while [ -L "$p" ]; do
      guard=$((guard + 1))
      [ "$guard" -le 40 ] || { echo "spawn-orchestrator: symlink chain too deep (fail-closed): $1" >&2; return 1; }
      target="$(readlink "$p")" || { echo "spawn-orchestrator: cannot read link (fail-closed): $p" >&2; return 1; }
      case "$target" in
        /*) p="$target" ;;
        *)  p="$(cd "$(dirname "$p")" && pwd -P)/$target" ;;
      esac
    done
    d="$(cd "$(dirname "$p")" && pwd -P)" || { echo "spawn-orchestrator: cannot resolve directory of: $p" >&2; return 1; }
    b="$(basename "$p")"
    printf '%s/%s' "$d" "$b"
  fi
}

# Escape a path for use inside a Seatbelt "(subpath \"…\")" / "(literal \"…\")".
sbpl_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//\"/\\\"}"
  printf '%s' "$s"
}

# Escape a canonicalized path for embedding inside an SBPL "(regex #\"…\")"
# string: backslash-prefix backslash/quote AND any literal regex metacharacter
# the path might contain (host temp-dir components are normally plain
# alnum/._-, but this must not silently mismatch if that's ever not true).
# Verified empirically against sandbox-exec: a SINGLE backslash in the profile
# source is enough for both the SBPL string reader and the regex engine to see
# a literal char — do NOT double it (that breaks the match).
sbpl_regex_escape() {
  local s="$1" out="" c i
  for (( i=0; i<${#s}; i++ )); do
    c="${s:$i:1}"
    case "$c" in
      '\'|'"'|'.'|'*'|'+'|'?'|'('|')'|'['|']'|'{'|'}'|'|'|'^'|'$') out+="\\$c" ;;
      *) out+="$c" ;;
    esac
  done
  printf '%s' "$out"
}

# Resolve a path whose LEAF may not exist yet. The harness creates its runtime
# dirs LAZILY (on first session), so canonicalize()'s existence check would
# fail-close a launch just because a dir hasn't been made yet — which is the
# wrong trade at 3am. Canonicalize the deepest EXISTING ancestor (so /tmp →
# /private/tmp still resolves through the symlink) and re-append the missing
# tail verbatim. Prints the resolved path; returns non-zero only if even the
# ancestor chain can't be resolved.
resolve_lazy() {
  local p="$1" tail=""
  case "$p" in /*) ;; *) echo "spawn-orchestrator: path must be absolute (fail-closed): $p" >&2; return 1 ;; esac
  while [ ! -e "$p" ] && [ "$p" != "/" ]; do
    tail="/$(basename "$p")$tail"
    p="$(dirname "$p")"
  done
  local c; c="$(canonicalize "$p")" || return 1
  printf '%s%s' "$c" "$tail"
}

# Emit the FIXED (never caller-supplied) write scopes for Claude Code's OWN
# runtime surface. These are host-resolved by the renderer, NOT passed as --rw,
# and that is load-bearing: two of the three live OUTSIDE the run root, so a
# caller-supplied --rw would either trip the --confine-under guard or force the
# launch path to weaken it. Renderer-owned means the containment guard keeps
# covering every caller scope while the harness still gets exactly what it needs.
# See orchestrator.sb.tmpl's @@HARNESS_RUNTIME@@ comment for why denying any of
# these poisons the harness itself (task 12 / finding #20).
# Args: <tmpdir-canonical> <tmp-root-canonical> <session-env-dir>.
emit_harness_runtime() {
  local tmpdir_c="$1" tmp_root="$2" session_env="$3"
  local sock_pattern claude_tmp_pattern
  sock_pattern="^$(sbpl_regex_escape "$tmpdir_c")"'/srt-mux-[0-9]+-[0-9]+\.sock$'
  # The harness's per-project/per-session scratch + cwd tree under /tmp. This is
  # a DIRECTORY TREE it mkdir's into, not a single file — a file-only grant
  # (e.g. just the claude-*-cwd literal) does not permit the enclosing mkdir, so
  # the Bash tool dies before it ever runs the command.
  #
  # Matched by PATTERN, not by a uid-resolved path: the id in claude-<id> is not
  # the uid (see render_profile) and a resolved path silently misses, restoring
  # the exit-code poisoning.
  #
  # TWO distinct shapes live here, and granting only one still poisons exit codes:
  #   /tmp/claude-<id>/<project>/<session>/…  the scratch TREE (mkdir'd)
  #   /tmp/claude-<hex>-cwd                   the cwd-tracking FILE, rewritten
  #                                           after EVERY Bash call, with a fresh
  #                                           hex id each time
  # The detached orchestrator uses the -cwd files; an interactive session was seen
  # using the numeric tree. Cover both, and no more: still never a blanket /tmp write.
  #
  # ACCEPTED RELAXATION: this pattern is deliberately broader than the old
  # `(subpath "/tmp/claude-$(id -u)")` — it grants any `claude-<id>` tree, not just
  # this uid's, because <id> is not the uid and can't be resolved ahead of time.
  # The widening is bounded by standard POSIX permissions, which SBPL grants do NOT
  # override: another operator's `claude-<id>` tree is only reachable if it is
  # independently writable to this uid (it isn't, under the single-operator host
  # this harness targets). So the relaxation is a real but low-severity trade of
  # tightness for the reliability that keeps finding #20 fixed.
  claude_tmp_pattern="^$(sbpl_regex_escape "$tmp_root")"'/claude-[A-Za-z0-9]+(-cwd)?(/|$)'
  printf '(allow file-write*\n'
  printf '  (regex #"%s")\n' "$sock_pattern"
  printf '  (regex #"%s")\n' "$claude_tmp_pattern"
  # The harness mkdir's a per-session dir under ~/.claude/session-env. Scoped to
  # session-env SPECIFICALLY — never a blanket ~/.claude write, which would put
  # the credential file inside a writable scope and undo the cred-RO/state-RW
  # split (§3) that --cred-ro exists to enforce.
  printf '  (subpath "%s")\n' "$(sbpl_escape "$session_env")"
  printf ')\n'
  # network-bind: a unix-domain socket bind/listen is gated on the socket's own
  # path (like Apple's own rpcbind.sb), not a free network class — this grant is
  # required IN ADDITION to the file-write* above, not instead of it. Without it
  # the harness's inner sandbox can't listen on its mux socket and silently
  # disables itself for the session.
  printf '(allow network-bind\n'
  printf '  (regex #"%s")\n' "$sock_pattern"
  printf ')\n'
}

# Emit an s-expression allowing `op` over the given canonical paths, or a
# placeholder comment when the list is empty. Args: <op> <path>...
emit_allow() {
  local op="$1"; shift
  if [ "$#" -eq 0 ]; then
    printf ';; (none)\n'
    return
  fi
  printf '(allow %s\n' "$op"
  local p
  for p in "$@"; do
    printf '  (subpath "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

# Emit a (deny file-write* (literal …)) form over credential files, or a
# placeholder comment when the list is empty. This is emitted AFTER the RW block
# so it overrides the state-dir write allow (Seatbelt = last matching rule wins),
# keeping a token read-only while its enclosing state dir stays writable. Args:
# <cred-path>...
emit_deny_write() {
  if [ "$#" -eq 0 ]; then
    printf ';; (no isolated credential files)\n'
    return
  fi
  printf '(deny file-write*\n'
  local p
  for p in "$@"; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

# Emit the process-exec allow form: literal binaries (--exec) and subpath bin
# dirs (--exec-dir/--toolchain). Args: <literal>... -- <subpath>...
emit_exec() {
  local -a lits=()
  while [ $# -gt 0 ] && [ "$1" != "--" ]; do lits+=("$1"); shift; done
  shift || true   # drop the -- separator
  if [ "${#lits[@]}" -eq 0 ] && [ "$#" -eq 0 ]; then
    printf ';; (no coder/toolchain binaries resolved)\n'
    return
  fi
  printf '(allow process-exec\n'
  local p
  for p in ${lits[@]+"${lits[@]}"}; do
    printf '  (literal "%s")\n' "$(sbpl_escape "$p")"
  done
  for p in "$@"; do
    printf '  (subpath "%s")\n' "$(sbpl_escape "$p")"
  done
  printf ')\n'
}

render_profile() {
  local out="" template="$TEMPLATE_DEFAULT" toolchain=0 tmpdir=""
  local -a rw=() ro=() cred=() ex=() exd=() confine=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --rw) [ $# -ge 2 ] || die "missing value for --rw"; rw+=("$2"); shift 2 ;;
      --ro) [ $# -ge 2 ] || die "missing value for --ro"; ro+=("$2"); shift 2 ;;
      --cred-ro) [ $# -ge 2 ] || die "missing value for --cred-ro"; cred+=("$2"); shift 2 ;;
      --tmpdir) [ $# -ge 2 ] || die "missing value for --tmpdir"; tmpdir="$2"; shift 2 ;;
      --exec) [ $# -ge 2 ] || die "missing value for --exec"; ex+=("$2"); shift 2 ;;
      --exec-dir) [ $# -ge 2 ] || die "missing value for --exec-dir"; exd+=("$2"); shift 2 ;;
      --toolchain) toolchain=1; shift ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; confine+=("$2"); shift 2 ;;
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      --template) [ $# -ge 2 ] || die "missing value for --template"; template="$2"; shift 2 ;;
      *) die "unknown render-profile argument: $1" ;;
    esac
  done

  [ -n "$out" ] || die "render-profile requires --out <file>"
  [ -f "$template" ] || die "profile template not found: $template"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # --toolchain: expand to the standard bin dirs, skipping any that don't exist
  # on this host (a missing standard dir is not fail-closed — it's just omitted).
  if [ "$toolchain" = 1 ]; then
    local d
    for d in /bin /usr/bin /usr/sbin /usr/libexec /opt/homebrew/bin \
      "$HOME/.local/bin" "$HOME/.local/share/claude" "$HOME/.codex" "$HOME/.nvm"; do
      [ -d "$d" ] && exd+=("$d")
    done
  fi

  # Canonicalize ALL inputs first — any bad path aborts before we write, so a
  # partial profile is never emitted.
  local -a rw_c=() ro_c=() cred_c=() ex_c=() exd_c=() confine_c=()
  local p c
  for p in ${confine[@]+"${confine[@]}"}; do c="$(canonicalize "$p")" || exit 2; confine_c+=("$c"); done
  for p in ${rw[@]+"${rw[@]}"}; do c="$(canonicalize "$p")" || exit 2; rw_c+=("$c"); done
  for p in ${ro[@]+"${ro[@]}"}; do c="$(canonicalize "$p")" || exit 2; ro_c+=("$c"); done
  # Credential files: must be an existing FILE (a token, not a dir). The literal
  # deny is emitted after the RW block so it overrides a co-located state-dir
  # write allow; a cred file outside every RW scope is harmless (already unwritable).
  # KNOWN LIMITATION (documented, not fail-closed): Seatbelt matches on the path,
  # not the inode, so a second HARD LINK to the same credential inode inside an RW
  # scope stays writable and would mutate the token through the alias. We do NOT
  # reject nlink>1 here — a benign multiply-linked file must not fail a launch
  # closed at 3am — but tool credential files are not hard-linked in practice.
  for p in ${cred[@]+"${cred[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    [ -f "$c" ] || die "cred-ro path is not a file (fail-closed): $c"
    cred_c+=("$c")
  done
  for p in ${ex[@]+"${ex[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    { [ -f "$c" ] && [ -x "$c" ]; } || die "exec path is not an executable file (fail-closed): $c"
    ex_c+=("$c")
  done
  for p in ${exd[@]+"${exd[@]}"}; do
    c="$(canonicalize "$p")" || exit 2
    [ -d "$c" ] || die "exec-dir is not a directory (fail-closed): $c"
    # Dedupe against what's already resolved (explicit --exec-dir + --toolchain
    # can overlap, e.g. /opt/homebrew/bin listed twice).
    case " ${exd_c[@]+"${exd_c[@]}"} " in *" $c "*) continue ;; esac
    exd_c+=("$c")
  done

  # Floor (ALWAYS, even without --confine-under): a write scope of "/" emits a
  # whole-filesystem write rule that defeats the jail. Refuse it unconditionally —
  # the containment guard below is opt-in and can't protect itself if a caller
  # forgets --confine-under, so this floor closes the worst case regardless.
  local w r under
  for w in ${rw_c[@]+"${rw_c[@]}"}; do
    [ "$w" = "/" ] && die "refusing --rw / (whole-filesystem write, fail-closed)"
  done
  # Same floor for exec breadth: an exec-dir of "/" would allow-list exec of
  # every binary on the filesystem, defeating the exec wall entirely.
  for w in ${exd_c[@]+"${exd_c[@]}"}; do
    [ "$w" = "/" ] && die "refusing --exec-dir / (whole-filesystem exec, fail-closed)"
  done
  # Containment: when --confine-under roots are given, every WRITE scope must
  # canonicalize inside one of them (so a worktree symlinked to /$HOME can't slip
  # a broad write through). Both sides are already pwd -P'd; the prefix test is
  # LITERAL (`${w#"$r"/}`, not a glob) so a root containing * / [ can't over-match.
  # RO/exec are intentionally exempt (creds and system binaries live outside the
  # run root). Fail-closed on any escape.
  if [ "${#confine_c[@]}" -gt 0 ]; then
    for w in ${rw_c[@]+"${rw_c[@]}"}; do
      under=0
      for r in "${confine_c[@]}"; do
        { [ "$w" = "$r" ] || [ "${w#"$r"/}" != "$w" ]; } && under=1
      done
      [ "$under" = 1 ] || die "write scope escapes --confine-under (fail-closed): $w"
    done
  fi

  # Build the token blocks.
  local ro_block rw_block cred_block ex_block harness_block
  ro_block="$(emit_allow "file-read*" ${ro_c[@]+"${ro_c[@]}"})"
  # RW scopes need both read and write.
  rw_block="$(emit_allow "file-read*" ${rw_c[@]+"${rw_c[@]}"})
$(emit_allow "file-write*" ${rw_c[@]+"${rw_c[@]}"})"
  cred_block="$(emit_deny_write ${cred_c[@]+"${cred_c[@]}"})"
  ex_block="$(emit_exec ${ex_c[@]+"${ex_c[@]}"} -- ${exd_c[@]+"${exd_c[@]}"})"
  # Harness's OWN runtime surface (task 12 / finding #20) — fixed, host-resolved
  # paths, never caller-supplied (and so never subject to the caller-scope
  # --confine-under check above; two of these legitimately live outside the run
  # root). $TMPDIR must exist; session-env is created lazily on first session, so
  # resolve_lazy tolerates its absence rather than fail-closing a launch over a
  # dir the harness is about to make itself.
  #
  # The harness's tmp tree is /tmp/claude-<N>, and N is NOT the uid — it varies
  # per harness instance. A detached launchd orchestrator was observed using
  # /tmp/claude-522 on a uid-501 host while the interactive session on the same
  # host used /tmp/claude-501. A uid-derived path therefore matches only by
  # coincidence: when it misses, the harness's mkdir is denied, the Bash tool
  # dies before running anything, and EVERY exit code is poisoned to 1 — finding
  # #20, the exact failure this grant exists to prevent, silently restored. So
  # match the tree by PATTERN over /tmp, never by a uid-resolved path.
  #
  # The detached launchd job's TMPDIR is authoritative here: the srt-mux socket
  # grant is anchored to it, and write-launch reads it back from the @spawn-tmpdir
  # stamp emitted below (so the two can never diverge — a divergence silently
  # re-breaks the inner sandbox). Prefer the explicit --tmpdir (the run-owned dir
  # the job will export); fall back to the env only when a caller renders a profile
  # for the ambient session. When --tmpdir is given AND the render is confined,
  # require it to sit inside a confinement root so the job's later `mkdir -p` is
  # bounded.
  local tmpdir_c tmp_root session_env
  if [ -n "$tmpdir" ]; then
    case "$tmpdir" in /*) ;; *) die "--tmpdir must be absolute (fail-closed): $tmpdir" ;; esac
    tmpdir_c="$(resolve_lazy "$tmpdir")" || exit 2
    if [ "${#confine_c[@]}" -gt 0 ]; then
      local _t_ok=0 r
      for r in "${confine_c[@]}"; do
        [ "$tmpdir_c" = "$r" ] || [ "${tmpdir_c#"$r"/}" != "$tmpdir_c" ] && { _t_ok=1; break; }
      done
      [ "$_t_ok" = 1 ] || die "--tmpdir escapes --confine-under (fail-closed): $tmpdir_c"
    fi
  else
    tmpdir_c="$(canonicalize "${TMPDIR:-/tmp}")" || exit 2
  fi
  tmp_root="$(canonicalize "/tmp")" || exit 2
  session_env="$(resolve_lazy "$HOME/.claude/session-env")" || exit 2
  harness_block="$(emit_harness_runtime "$tmpdir_c" "$tmp_root" "$session_env")"

  # Substitute tokens line-by-line into a temp file, then move into place so a
  # failure mid-render leaves no partial --out.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator.sb.XXXXXX")" || die "mktemp failed"
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      *@@RO_PATHS@@*) printf '%s\n' "$ro_block" ;;
      *@@RW_PATHS@@*) printf '%s\n' "$rw_block" ;;
      *@@CRED_DENY@@*) printf '%s\n' "$cred_block" ;;
      *@@HARNESS_RUNTIME@@*) printf '%s\n' "$harness_block" ;;
      *@@EXEC_PATHS@@*) printf '%s\n' "$ex_block" ;;
      *) printf '%s\n' "$line" ;;
    esac
  done <"$template" >"$tmp"

  # Stamp the canonical TMPDIR the srt-mux grant is anchored to, as an SBPL
  # comment (`;;` is ignored by the compiler). write-launch reads THIS back to set
  # the job's TMPDIR, so the socket the job creates always lands where the profile
  # grants — the render and the launch can't drift apart. Exactly one stamp.
  printf ';; @spawn-tmpdir: %s\n' "$tmpdir_c" >>"$tmp"

  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write profile: $out"; }
  echo "spawn-orchestrator: profile OK $out"
}

# Build the narrowed egress host set (sorted, unique), fail-closed. This is
# layer 2 of the jail: Seatbelt can't filter by hostname, so egress is enforced
# by the detached `claude -p`'s own sandbox.network allowlist. The set is
# narrowed to the run's resolved coders + source so a linear+codex run never
# opens devin's or agy's endpoints. Args are parsed by render_settings below.
render_network_allowlist() {
  local source="" agy_host="" npm=0
  local -a coders=() mcp=() add_task=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --coder) [ $# -ge 2 ] || die "missing value for --coder"; coders+=("$2"); shift 2 ;;
      --source) [ $# -ge 2 ] || die "missing value for --source"; source="$2"; shift 2 ;;
      --agy-host) [ $# -ge 2 ] || die "missing value for --agy-host"; agy_host="$2"; shift 2 ;;
      --mcp-host) [ $# -ge 2 ] || die "missing value for --mcp-host"; mcp+=("$2"); shift 2 ;;
      --add-task-host) [ $# -ge 2 ] || die "missing value for --add-task-host"; add_task+=("$2"); shift 2 ;;
      --npm) npm=1; shift ;;
      *) die "unknown allowlist argument: $1" ;;
    esac
  done
  case "$source" in
    linear|plan) ;;
    "") die "network allowlist requires --source <linear|plan>" ;;
    *) die "unknown --source (fail-closed): $source" ;;
  esac

  # Always needed: the orchestrator model + GitHub (PRs, git over HTTPS).
  local -a hosts=(api.anthropic.com api.github.com github.com codeload.github.com '*.githubusercontent.com')
  [ "$source" = linear ] && hosts+=(api.linear.app)
  [ "$npm" = 1 ] && hosts+=(registry.npmjs.org)

  local c
  for c in ${coders[@]+"${coders[@]}"}; do
    case "$c" in
      codex) hosts+=(api.openai.com) ;;
      devin) hosts+=(api.devin.ai server.codeium.com) ;;
      agy)
        [ -n "$agy_host" ] || die "coder 'agy' requires --agy-host <resolved Antigravity host> (fail-closed): install/re-route or resolve the host at pre-flight"
        case "$agy_host" in
          \**) die "agy host must be a concrete host, never a wildcard (fail-closed): $agy_host" ;;
        esac
        hosts+=("$agy_host") ;;
      *) die "unknown --coder (fail-closed): $c" ;;
    esac
  done
  hosts+=(${mcp[@]+"${mcp[@]}"})
  # The add-task destination host is added regardless of --source: a plan-source
  # run whose /add-task handler routes to Linear/Jira still needs egress there, or
  # the run's own settings would deny its own tracked-follow-up filing.
  hosts+=(${add_task[@]+"${add_task[@]}"})

  # Validate EVERY host, fail-closed. Host values from --mcp-host / --agy-host /
  # --add-task-host are resolved per-run from the (possibly adversary-influenced)
  # work source, so an unvalidated value could smuggle a bare `*` (nullifying the
  # allowlist) or inject JSON in hosts_to_json_array. Require a real hostname —
  # labels of [A-Za-z0-9-] separated by dots, with at most a single leading `*.` subdomain wildcard (which
  # `*.githubusercontent.com` legitimately uses; a bare `*` has no labels and is
  # rejected). The agy-specific guard above additionally bans ANY wildcard for agy.
  local h
  for h in "${hosts[@]}"; do
    # Reject an embedded newline FIRST: `grep -Eq` matches if ANY line matches,
    # so a multiline value could pass the anchored regex on one line while
    # hosts_to_json_array splits it into a second, unvalidated host — smuggling a
    # wildcard past the allowlist. A real hostname never contains a newline.
    case "$h" in *$'\n'*) die "invalid egress host (embedded newline, fail-closed): $h" ;; esac
    printf '%s' "$h" | grep -Eq '^(\*\.)?[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$' \
      || die "invalid egress host (fail-closed): $h"
  done

  printf '%s\n' "${hosts[@]}" | sort -u
}

# Emit a JSON array literal from the host lines on stdin.
hosts_to_json_array() {
  local first=1 out="["
  local h
  while IFS= read -r h; do
    [ -n "$h" ] || continue
    [ "$first" = 1 ] && first=0 || out+=","
    out+="\"$h\""
  done
  out+="]"
  printf '%s' "$out"
}

render_settings() {
  local out="" localbind=false
  local -a passthru=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --out) [ $# -ge 2 ] || die "missing value for --out"; out="$2"; shift 2 ;;
      # Default OFF: a listen socket lets the inside process reach host-local
      # services (the Solo control port, local proxies, …) — a pivot the jail
      # shouldn't grant. Enable only if a run genuinely needs to bind loopback.
      --allow-local-binding) localbind=true; shift ;;
      *) passthru+=("$1"); shift ;;
    esac
  done
  [ -n "$out" ] || die "render-settings requires --out <file>"
  case "$out" in /*) ;; *) die "--out must be absolute (fail-closed): $out" ;; esac

  # render_network_allowlist runs in a $() — its die() exits only the subshell,
  # so capture the status and abort here (fail-closed: no settings on any error).
  local hosts array
  hosts="$(render_network_allowlist ${passthru[@]+"${passthru[@]}"})" || exit 2
  array="$(printf '%s\n' "$hosts" | hosts_to_json_array)"

  # Ephemeral run settings for `claude -p --settings '<json>'`: deny-by-default
  # egress narrowed to $array, plus loopback for the enforcement proxy + local
  # tooling. Written to --out (audit trail); the caller passes it via --settings,
  # never mutating ~/.claude.
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-settings.XXXXXX")" || die "mktemp failed"
  printf '{"sandbox":{"enabled":true,"network":{"allowedDomains":%s,"allowLocalBinding":%s}}}\n' "$array" "$localbind" >"$tmp" \
    || { rm -f "$tmp"; die "failed to write settings"; }
  mv "$tmp" "$out" || { rm -f "$tmp"; die "failed to write settings: $out"; }
  echo "spawn-orchestrator: settings OK $out"
}

# ---------------------------------------------------------------------------
# Task 3 — spawn mechanics: launch script + relaunchable launchd supervisor,
# an auth smoke-test THROUGH the wrapper (before detaching), and the run handle.
# ---------------------------------------------------------------------------

PLIST_TEMPLATE_DEFAULT="$ROOT/scripts/orchestrator.plist.tmpl"

# Escape a value for inclusion inside a plist <string>…</string>. `&` MUST be
# replaced first. Without this, a value containing plist/XML metacharacters (legal
# in macOS paths, and a label is caller-set) could inject launchd keys — e.g. a
# second <key>ProgramArguments</key>, which launchd execs DIRECTLY, not under
# sandbox-exec, bypassing the whole jail. This is the plist analogue of sbpl_escape.
xml_escape() {
  local s="$1"
  s="${s//&/&amp;}"
  s="${s//</&lt;}"
  s="${s//>/&gt;}"
  s="${s//\"/&quot;}"
  printf '%s' "$s"
}

# Inline token replacement (bash, not sed) so arbitrary paths with regex-special
# chars can't corrupt the render; every substituted string value is XML-escaped.
render_plist() {
  local label launch_script workdir log
  label="$(xml_escape "$1")"; launch_script="$(xml_escape "$2")"
  workdir="$(xml_escape "$3")"; log="$(xml_escape "$4")"
  local interval="$5" throttle="$6" template="$7"   # integers, validated numeric upstream
  local line
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line//@@LABEL@@/$label}"
    line="${line//@@LAUNCH_SCRIPT@@/$launch_script}"
    line="${line//@@WORKDIR@@/$workdir}"
    line="${line//@@LOG@@/$log}"
    line="${line//@@INTERVAL@@/$interval}"
    line="${line//@@THROTTLE@@/$throttle}"
    printf '%s\n' "$line"
  done <"$template"
}

# Emit the self-contained launch script + the launchd plist from resolved inputs.
# The launch script is what the plist runs: it composes the jail
# (sandbox-exec -f <profile>) around `claude -p --permission-mode bypassPermissions`
# with the layer-2 --settings, reads the run prompt from a file, and redirects to
# the log. %q-quoting keeps every interpolated path/string shell-safe.
write_launch() {
  local profile="" settings="" workdir="" log="" prompt="" until="" \
        label="" interval="300" throttle="30" out_script="" out_plist="" \
        plist_template="$PLIST_TEMPLATE_DEFAULT" claude_bin="" path="" tmpdir="" \
        self="$ROOT/scripts/spawn-orchestrator.sh" no_progress_limit="3"
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profile="$2"; shift 2 ;;
      --settings) settings="$2"; shift 2 ;;
      --workdir) workdir="$2"; shift 2 ;;
      --log) log="$2"; shift 2 ;;
      --prompt-file) prompt="$2"; shift 2 ;;
      --until) until="$2"; shift 2 ;;
      --label) label="$2"; shift 2 ;;
      --interval) interval="$2"; shift 2 ;;
      --throttle) throttle="$2"; shift 2 ;;
      --claude-bin) claude_bin="$2"; shift 2 ;;
      --path) path="$2"; shift 2 ;;
      --tmpdir) tmpdir="$2"; shift 2 ;;
      --out-script) out_script="$2"; shift 2 ;;
      --out-plist) out_plist="$2"; shift 2 ;;
      --plist-template) plist_template="$2"; shift 2 ;;
      --self) self="$2"; shift 2 ;;
      --no-progress-limit) no_progress_limit="$2"; shift 2 ;;
      *) die "unknown write-launch argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-launch requires --out-script and --out-plist"
  [ -n "$path" ] || die "write-launch requires --path <PATH> (fail-closed): a launchd job has a minimal PATH; pass the fingerprint-resolved toolchain dirs"
  [ -n "$label" ] || die "write-launch requires --label"
  # Pin the label to a launchd reverse-DNS charset — belt-and-suspenders against
  # plist injection on top of xml_escape, and it's what launchd expects anyway.
  case "$label" in *[!A-Za-z0-9._-]*) die "--label must be [A-Za-z0-9._-] (fail-closed): $label" ;; esac
  local f
  for f in "$profile" "$settings" "$prompt"; do
    [ -n "$f" ] || die "write-launch requires --profile, --settings, and --prompt-file"
    [ -f "$f" ] || die "not found (fail-closed): $f"
  done
  # The launchd job's TMPDIR comes from the profile's @spawn-tmpdir stamp — the
  # SAME canonical dir the srt-mux socket grant is anchored to (render_profile
  # emits it). Deriving it from the profile makes a render/launch drift impossible:
  # the job exports exactly the dir whose mux socket the profile permits. A stale
  # profile with no stamp is fail-closed. A passed --tmpdir is an OPTIONAL
  # cross-check: it must resolve to the stamped dir, else the caller rendered and
  # launched with different dirs and the inner sandbox would silently degrade.
  local stamped
  # tail -1, NOT head -1: render_profile appends the real stamp LAST (after all
  # template substitution), and nothing user-controlled can land after it — so
  # the last match is authoritative. head -1 would let an earlier `;; @spawn-tmpdir:`
  # line (e.g. one smuggled through a scope path) override the real one.
  stamped="$(sed -n 's/^;; @spawn-tmpdir: //p' "$profile" | tail -1)"
  [ -n "$stamped" ] || die "profile has no @spawn-tmpdir stamp (fail-closed): re-render it with the current render-profile: $profile"
  if [ -n "$tmpdir" ]; then
    case "$tmpdir" in /*) ;; *) die "--tmpdir must be absolute (fail-closed): $tmpdir" ;; esac
    local tmpdir_check
    tmpdir_check="$(resolve_lazy "$tmpdir")" || exit 2
    [ "$tmpdir_check" = "$stamped" ] || die "--tmpdir ($tmpdir_check) does not match the profile's rendered TMPDIR ($stamped) (fail-closed) — render the profile and launch with the SAME --tmpdir"
  fi
  tmpdir="$stamped"
  [ -n "$workdir" ] && [ -d "$workdir" ] || die "write-launch requires an existing --workdir"
  [ -n "$log" ] || die "write-launch requires --log"
  case "$interval$throttle" in *[!0-9]*) die "--interval/--throttle must be integers" ;; esac
  case "$no_progress_limit" in *[!0-9]*|"") die "--no-progress-limit must be a positive integer" ;; esac
  [ "$no_progress_limit" -ge 1 ] || die "--no-progress-limit must be a positive integer"
  [ -f "$self" ] || die "spawn-orchestrator.sh not found (fail-closed): $self"

  local settings_json; settings_json="$(cat "$settings")"
  # Resolve claude to an ABSOLUTE path: a detached launchd job runs with a minimal
  # PATH (/usr/bin:/bin:/usr/sbin:/sbin) and would not find a Homebrew `claude`.
  # The caller should pass --claude-bin (the same path it gave render-profile's
  # --exec, so the launch invokes exactly the binary the profile permits); default
  # to `command -v claude` for convenience.
  [ -n "$claude_bin" ] || claude_bin="$(command -v claude 2>/dev/null)" || claude_bin=""
  [ -n "$claude_bin" ] || die "claude not found (fail-closed): pass --claude-bin or put claude on PATH"
  case "$claude_bin" in /*) ;; *) die "--claude-bin must be absolute (fail-closed): $claude_bin" ;; esac

  local state_file="$workdir/.auto-pilot/$SUPERVISOR_STATE_NAME"
  local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-launch.XXXXXX")" || die "mktemp failed"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Auto-pilot orchestrator launch script (generated — do not edit).\n'
    printf 'set -uo pipefail\n'
    printf 'export AUTO_PILOT_UNTIL=%q\n' "$until"
    printf 'export PATH=%q\n' "$path"
    # A launchd job inherits no usable TMPDIR, so without this the jailed process
    # has nowhere writable to put temp files: zsh dies with "can't create temp file
    # for here document" on any heredoc, and the harness's mux socket can't be
    # created. This is the profile's own @spawn-tmpdir (read above) — the exact dir
    # the srt-mux socket grant is anchored to — so the job's mux socket always
    # lands where the profile permits it; render and launch cannot drift apart.
    printf 'export TMPDIR=%q\n' "$tmpdir"
    printf 'mkdir -p %q\n' "$tmpdir"
    printf 'cd %q\n' "$workdir"
    # Record the log's size BEFORE this wake writes to it. The log is appended
    # to across every wake, so classify-exit must look only at the bytes THIS
    # process wrote — otherwise an old wake's 401 stays in the file forever and
    # would keep halting the run long after a human re-authenticated.
    printf 'off=$(wc -c <%q 2>/dev/null | tr -d " ") || off=0\n' "$log"
    printf ': "${off:=0}"\n'
    # NOT `exec`'d (task 10): the wrapper must observe claude's exit to
    # classify it, so the launchd-tracked PID is this wrapper, not claude
    # itself. `set +e`/`set -e` bracket the one command allowed to fail.
    printf 'set +e\n'
    printf 'sandbox-exec -f %q \\\n' "$profile"
    printf '  %q -p "$(cat %q)" \\\n' "$claude_bin" "$prompt"
    printf '  --permission-mode bypassPermissions \\\n'
    printf '  --settings %q \\\n' "$settings_json"
    printf '  --verbose \\\n'
    printf '  --output-format stream-json \\\n'
    printf '  >>%q 2>&1\n' "$log"
    printf 'code=$?\n'
    printf 'set -e\n'
    # Supervisor-side classification (no model call, task 10 / finding #22):
    # a non-retryable auth failure halts the run instead of relaunching into
    # the same 401 forever; an unclassified repeated failure with no
    # run-state progress also halts (the general backstop). A retryable exit
    # (or a legitimate paused_until wait) just exits non-zero, and the
    # plist's StartInterval relaunches as before.
    printf '%q supervisor-check --exit-code "$code" --log %q --since-offset "$off" --dir %q --label %q --state %q --no-progress-limit %q\n' \
      "$self" "$log" "$workdir" "$label" "$state_file" "$no_progress_limit"
    printf 'exit $?\n'
  } >"$tmp" || { rm -f "$tmp"; die "failed to write launch script"; }
  mv "$tmp" "$out_script" || { rm -f "$tmp"; die "failed to write launch script: $out_script"; }
  chmod +x "$out_script"

  [ -f "$plist_template" ] || die "plist template not found: $plist_template"
  tmp="$(mktemp "${TMPDIR:-/tmp}/orchestrator-plist.XXXXXX")" || die "mktemp failed"
  render_plist "$label" "$out_script" "$workdir" "$log" "$interval" "$throttle" "$plist_template" >"$tmp" \
    || { rm -f "$tmp"; die "failed to render plist"; }
  mv "$tmp" "$out_plist" || { rm -f "$tmp"; die "failed to write plist: $out_plist"; }
  echo "spawn-orchestrator: launch written $out_script $out_plist"
}

# Record the orchestrator's identity for stale/recycled-PID detection: the PID,
# its process start-time (guards against a recycled PID being mistaken for a live
# run), and the run's --until deadline.
record_handle() {
  local pid="" until="" out=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --pid) pid="$2"; shift 2 ;;
      --until) until="$2"; shift 2 ;;
      --out) out="$2"; shift 2 ;;
      *) die "unknown record-handle argument: $1" ;;
    esac
  done
  [ -n "$pid" ] && [ -n "$out" ] || die "record-handle requires --pid and --out"
  case "$pid" in *[!0-9]*|"") die "--pid must be numeric: $pid" ;; esac
  local started; started="$(ps -p "$pid" -o lstart= 2>/dev/null)" || true
  [ -n "$started" ] || die "no live process at pid $pid (cannot record a dead handle)"
  {
    printf 'orchestrator_pid: %s\n' "$pid"
    printf 'orchestrator_started_at: %s\n' "$started"
    printf 'until: %s\n' "$until"
  } >"$out" || die "failed to write handle: $out"
  echo "spawn-orchestrator: handle recorded $out"
}

# Auth smoke-test THROUGH the exact wrapper + settings, before detaching, so a
# dead credential fails loudly now rather than silently at 3am. Executes claude.
smoke_test() {
  local profile="" settings=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --profile) profile="$2"; shift 2 ;;
      --settings) settings="$2"; shift 2 ;;
      *) die "unknown smoke-test argument: $1" ;;
    esac
  done
  [ -f "$profile" ] && [ -f "$settings" ] || die "smoke-test requires --profile and --settings files"
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available (macOS only)"
  local json; json="$(cat "$settings")"
  local claude_bin; claude_bin="$(command -v claude 2>/dev/null)" || die "claude not found on PATH"
  # Match the REAL launch's posture (bypassPermissions + the wrapper + settings) so
  # the smoke validates the same invocation the detached job will run. Assert on
  # observable output, not just $?, so a degraded claude that exits 0 without a live
  # credential can't false-pass the auth gate.
  local out
  out="$(sandbox-exec -f "$profile" "$claude_bin" -p 'ok' --max-turns 1 \
    --permission-mode bypassPermissions --settings "$json" \
    --verbose --output-format stream-json 2>/dev/null)" \
    || die "auth smoke-test failed THROUGH the wrapper — a credential or the jail is wrong; not detaching"
  [ -n "$out" ] || die "auth smoke-test produced no output THROUGH the wrapper — credential/jail suspect; not detaching"
  # Assert the output is actually parseable stream-json (a line beginning with
  # `{` and containing "type"), not just non-empty: a degraded claude could print
  # a plain-text error to stdout and false-pass the emptiness check above.
  printf '%s\n' "$out" | grep -Eq '^\{.*"type"' \
    || die "auth smoke-test produced non-stream-json output THROUGH the wrapper — flag/jail suspect; not detaching"
  echo "spawn-orchestrator: smoke-test OK"
}

detach() {
  local plist=""
  while [ $# -gt 0 ]; do
    case "$1" in --plist) plist="$2"; shift 2 ;; *) die "unknown detach argument: $1" ;; esac
  done
  [ -f "$plist" ] || die "detach requires an existing --plist"
  command -v launchctl >/dev/null 2>&1 || die "launchctl not available (macOS only)"
  launchctl bootstrap "gui/$(id -u)" "$plist" || die "launchctl bootstrap failed for $plist"
  echo "spawn-orchestrator: detached (launchd) $plist"
}

teardown() {
  local label="" done_sentinel=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --done-sentinel) [ $# -ge 2 ] || die "missing value for --done-sentinel"; done_sentinel="$2"; shift 2 ;;
      *) die "unknown teardown argument: $1" ;;
    esac
  done
  [ -n "$label" ] || die "teardown requires --label"

  # Write the done-sentinel FIRST (atomically: tmp + mv), THEN boot the job
  # out — so a watcher polling the sentinel never sees "gone but no
  # done-marker" (the ordering is load-bearing, same reasoning as launch()'s
  # smoke-test-before-detach). This is the single completion mechanism;
  # `status` reads the same file.
  if [ -n "$done_sentinel" ]; then
    case "$done_sentinel" in /*) ;; *) die "--done-sentinel must be absolute (fail-closed): $done_sentinel" ;; esac
    local sdir; sdir="$(dirname "$done_sentinel")"
    mkdir -p "$sdir" || die "failed to create sentinel directory: $sdir"
    # Create the temp file IN the sentinel's own directory so the `mv` below is a
    # same-filesystem atomic rename — a temp under $TMPDIR could be on another
    # filesystem, making `mv` a non-atomic copy that can fail with EXDEV or leave
    # a watcher observing a partial sentinel.
    local tmp; tmp="$(mktemp "$sdir/.orchestrator-done.XXXXXX")" || die "mktemp failed"
    printf '%s done %s\n' "$label" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" >"$tmp" \
      || { rm -f "$tmp"; die "failed to write done sentinel"; }
    mv "$tmp" "$done_sentinel" || { rm -f "$tmp"; die "failed to write done sentinel: $done_sentinel"; }
  fi

  # launchctl may genuinely be absent (non-macOS, or the in-jail test harness) —
  # that must not skip the sentinel write above; the sentinel is authoritative.
  if command -v launchctl >/dev/null 2>&1; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  fi
  echo "spawn-orchestrator: torn down $label"
}

# ---------------------------------------------------------------------------
# Task 10 — supervisor exit classification (finding #22): an expired OAuth
# credential made the supervisor relaunch into the same 401 ~52 times over
# 4.3 hours, doing zero work and raising zero signal. The supervisor already
# knew "retry later" (the rate-limit backstop, run-budget.md); it needs a
# third bucket — "stop, a human must act" — plus a general backstop for any
# OTHER unclassified repeated failure. All of this is SHELL-level, no model
# call: a rate-limited or auth-dead agent cannot run its own bookkeeping.
# ---------------------------------------------------------------------------

# Unambiguous auth-failure phrases. These are distinctive enough that a literal
# `grep -F` is safe: no transcript says "OAuth token has expired" incidentally.
_AUTH_FAIL_SIGNALS=('authentication_failed' 'Invalid authentication credentials' 'OAuth token has expired')
# A bare `401` is NOT one of them. The classified bytes are a full stream-json
# transcript — model output, tool results, diffs — where `401` shows up in line
# numbers (`foo.py:401`), byte counts, and SHAs. Matching it as a bare substring
# would halt a healthy run with a WRONG diagnosis ("re-authenticate"), which is
# the expensive kind of false positive. So 401 only counts in an auth-ish
# CONTEXT — an HTTP status field or a status line. The motivating run-#2 failure
# line (`401 Invalid authentication credentials`) still matches, via both the
# literal above and the status-line alternative here.
_AUTH_401_RE='("status"[[:space:]]*:[[:space:]]*401([^0-9]|$)|[Ss]tatus[[:space:]]*:[[:space:]]*401([^0-9]|$)|[Ee]rror[[:space:]]*:[[:space:]]*401([^0-9]|$)|(^|[^0-9])401[[:space:]]+(Invalid authentication credentials|Unauthorized))'
_RATE_LIMIT_SIGNALS=('429' 'rate_limit_error' 'overloaded')

# Classify an exit from its code + the bytes the process wrote THIS WAKE, with
# no model call. Prints exactly one of `done` / `fatal: <reason>` /
# `retry: <reason>` and always exits 0 (the classification is the payload).
# Auth is checked BEFORE rate-limit: it is the non-retryable case, and a
# transcript that happens to mention both must still halt, not retry.
#
# --since-offset is load-bearing, not an optimization. The launch script appends
# to one orchestrator.log across every wake, so classifying the WHOLE file makes
# an auth failure STICKY: once any wake emits a 401 the string never leaves the
# log, so every later non-zero exit — including ones after a human has
# re-authenticated and resumed — would re-classify as `fatal` and halt again,
# blaming a credential that is now fine. Reading only this wake's bytes is what
# makes the classification about the CURRENT process. A missing/invalid offset
# falls back to the whole file: over-halting is the safe direction, silently
# relaunching forever is not (that is the bug this task exists to fix).
classify_exit() {
  local code="" outfile="" offset=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --exit-code) [ $# -ge 2 ] || die "missing value for --exit-code"; code="$2"; shift 2 ;;
      --output) [ $# -ge 2 ] || die "missing value for --output"; outfile="$2"; shift 2 ;;
      --since-offset) [ $# -ge 2 ] || die "missing value for --since-offset"; offset="$2"; shift 2 ;;
      *) die "unknown classify-exit argument: $1" ;;
    esac
  done
  [ -n "$code" ] || die "classify-exit requires --exit-code"
  case "$code" in *[!0-9]*) die "--exit-code must be a non-negative integer: $code" ;; esac
  [ -n "$outfile" ] || die "classify-exit requires --output <file>"
  [ -f "$outfile" ] || die "classify-exit --output not found: $outfile"

  if [ "$code" = 0 ]; then
    echo "done"
    return 0
  fi

  # Slice to this wake's bytes. A non-numeric offset is NOT fail-closed — it
  # degrades to the whole file (see above), because refusing to classify at all
  # would leave the supervisor relaunching blind.
  local content
  case "$offset" in
    ''|*[!0-9]*) content="$(cat "$outfile")" ;;
    *) content="$(tail -c "+$((offset + 1))" "$outfile" 2>/dev/null)" || content="$(cat "$outfile")" ;;
  esac

  # Fatal auth signals are matched ONLY on the orchestrator's own error surface,
  # never on transcript CONTENT. In `--output-format stream-json`, stdout is one
  # JSON object per line (every event line starts with `{`), and the process's
  # stderr — merged into the log — is plain prose. So the error surface is: any
  # NON-`{` line (the CLI's own stderr: `API Error: …`, `OAuth token has expired
  # · …`), a stream-json `"type":"error"` event, or a structural JSON
  # `"status":<code>` field. An auth phrase sitting INSIDE a `{`-prefixed event
  # (a tool_result, an assistant message, or the run's own REPORT.md re-read
  # after --resume) is CONTENT — it never qualifies, so a task about auth, or the
  # halt's own reason, can't revive finding #22's loop. (JSON escapes nested
  # quotes, so a `"status"` written into event content arrives as `\"status\"`
  # and matches neither the status nor the content path.)
  local err_lines
  err_lines="$(grep -E '^[^{]|"type"[[:space:]]*:[[:space:]]*"error"|"status"[[:space:]]*:[[:space:]]*[0-9]' <<<"$content" 2>/dev/null)"
  local p
  for p in "${_AUTH_FAIL_SIGNALS[@]}"; do
    if grep -qF -- "$p" <<<"$err_lines"; then
      echo "fatal: non-retryable auth failure ($p)"
      return 0
    fi
  done
  if grep -Eq -- "$_AUTH_401_RE" <<<"$err_lines"; then
    echo "fatal: non-retryable auth failure (401 in an auth context)"
    return 0
  fi
  for p in "${_RATE_LIMIT_SIGNALS[@]}"; do
    if grep -qF -- "$p" <<<"$content"; then
      echo "retry: rate-limit signal ($p)"
      return 0
    fi
  done
  echo "retry: unclassified non-zero exit ($code)"
}

# Pull a bare "key: value" line out of the small supervisor-state file (below).
_supervisor_state_field() {
  local f="$1" key="$2"
  [ -f "$f" ] || return 0
  grep -E "^${key}:" "$f" | head -1 | sed -e "s/^${key}: *//"
}

# Atomically persist the no-progress counter + last-seen run-state HEAD.
_write_supervisor_state() {
  local f="$1" count="$2" head="$3" d; d="$(dirname "$f")"
  mkdir -p "$d" || die "cannot create supervisor-state directory: $d"
  local tmp; tmp="$(mktemp "$d/.supstate.XXXXXX")" || die "mktemp failed"
  { printf 'count: %s\n' "$count"; printf 'head: %s\n' "$head"; } >"$tmp" \
    || { rm -f "$tmp"; die "failed to write supervisor state"; }
  mv "$tmp" "$f" || { rm -f "$tmp"; die "failed to write supervisor state: $f"; }
}

# The run-state branch's current tip, or empty if --dir isn't (yet) a git
# checkout — best-effort, never fail-closed (a missing HEAD just means every
# wake looks like "no progress," which is the safe direction for the guard).
_run_head() {
  ( cd "$1" 2>/dev/null && git rev-parse HEAD 2>/dev/null ) || true
}

# True (exit 0) iff RUN.md's own run-level `status:` is `paused` — a wake
# that lands mid-pause makes no progress BY DESIGN (task 11: the orchestrator
# gates on `paused_until` before invoking claude and exits early), so it must
# never count against the no-progress guard.
_run_is_paused() {
  local run_md="$1/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || return 1
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
  local st; st="$(printf '%s\n' "$front" | grep -E '^status:' | head -1 \
    | sed -e 's/^status: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  [ "$st" = "paused" ]
}

# Set a single front-matter key's value in RUN.md (between the first two
# `---` lines). Fail-closed if the key isn't already declared — every field
# this is used for (`status`, `pause_reason`) is always present per
# run-state.md's `RUN.md` template, so a missing key means the file is not
# the shape this expects, and silently no-op-ing would hide that.
_set_front_field() {
  local f="$1" key="$2" value="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.runmd.XXXXXX")" || die "mktemp failed"
  awk -v key="$key" -v val="$value" '
    /^---$/ { dashes++; print; next }
    dashes==1 && $0 ~ "^" key ":" { print key ": " val; next }
    { print }
  ' "$f" >"$tmp" || { rm -f "$tmp"; die "failed to render $key update for $f"; }
  grep -qE "^${key}: " "$tmp" || { rm -f "$tmp"; die "front-matter key not found (fail-closed): $key in $f"; }
  mv "$tmp" "$f" || { rm -f "$tmp"; die "failed to write $f"; }
}

# The halt itself: write run-level `status: systemic` + `pause_reason` to
# RUN.md, append one alarm entry to REPORT.md, commit both to the run-state
# branch (best-effort — a broken git checkout must not block tearing the
# supervisor down, which is the one thing that MUST happen), then boot the
# launchd job out so it never relaunches into the same condition.
_supervisor_halt() {
  local dir="" label="" reason=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) dir="$2"; shift 2 ;;
      --label) label="$2"; shift 2 ;;
      --reason) reason="$2"; shift 2 ;;
      *) die "unknown supervisor-halt argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$label" ] && [ -n "$reason" ] \
    || die "supervisor-halt requires --dir, --label, and --reason"

  local run_md="$dir/.auto-pilot/RUN.md" report_md="$dir/.auto-pilot/REPORT.md" ts
  ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

  if [ -f "$run_md" ]; then
    _set_front_field "$run_md" status systemic
    _set_front_field "$run_md" pause_reason "$reason"
  else
    echo "spawn-orchestrator: supervisor halt: no RUN.md at $run_md, skipping run-state write" >&2
  fi

  {
    printf '\n## ALARM — supervisor halt (%s)\n\n' "$ts"
    printf -- '- **Reason:** %s\n' "$reason"
    printf -- '- **Action required:** a human must resolve this before the run can continue; the supervisor has torn itself down and will NOT relaunch on its own. Re-authenticate (or fix the underlying condition), then `--resume`.\n'
  } >>"$report_md" 2>/dev/null || echo "spawn-orchestrator: supervisor halt: failed to append $report_md" >&2

  if [ -f "$run_md" ] || [ -f "$report_md" ]; then
    ( cd "$dir" \
      && git add -- .auto-pilot/RUN.md .auto-pilot/REPORT.md 2>/dev/null \
      && git -c user.name="auto-pilot-supervisor" -c user.email="auto-pilot@localhost" \
             commit -q -m "auto-pilot: supervisor halt — $reason" \
    ) 2>/dev/null || echo "spawn-orchestrator: supervisor halt: run-state commit failed (not a git checkout, or nothing to commit)" >&2
  fi

  teardown --label "$label" >/dev/null 2>&1
  # Verify the bootout actually took: a failed teardown leaves the job loaded and
  # StartInterval relaunches straight back into this condition (finding #22's
  # loop, masked by the halt message). Retry once, then make a still-loaded job
  # LOUD rather than let the systemic status quietly contradict a live job.
  if command -v launchctl >/dev/null 2>&1 \
     && launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
    teardown --label "$label" >/dev/null 2>&1
    if launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1; then
      echo "spawn-orchestrator: supervisor halt ($label): WARNING — bootout failed, job still loaded and will keep relaunching; remove it by hand: launchctl bootout gui/$(id -u)/$label" >&2
    fi
  fi
  echo "spawn-orchestrator: supervisor halt ($label): $reason"
}

# The per-wake entry point the generated launch script calls after `claude -p`
# exits. See the file-header comment above for the full decision.
supervisor_check() {
  local code="" log="" dir="" label="" state="" limit=3 offset=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --exit-code) [ $# -ge 2 ] || die "missing value for --exit-code"; code="$2"; shift 2 ;;
      --log) [ $# -ge 2 ] || die "missing value for --log"; log="$2"; shift 2 ;;
      --since-offset) [ $# -ge 2 ] || die "missing value for --since-offset"; offset="$2"; shift 2 ;;
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --state) [ $# -ge 2 ] || die "missing value for --state"; state="$2"; shift 2 ;;
      --no-progress-limit) [ $# -ge 2 ] || die "missing value for --no-progress-limit"; limit="$2"; shift 2 ;;
      *) die "unknown supervisor-check argument: $1" ;;
    esac
  done
  [ -n "$code" ] && [ -n "$log" ] && [ -n "$dir" ] && [ -n "$label" ] && [ -n "$state" ] \
    || die "supervisor-check requires --exit-code, --log, --dir, --label, and --state"
  case "$code" in *[!0-9]*) die "--exit-code must be a non-negative integer: $code" ;; esac
  [ -f "$log" ] || die "supervisor-check --log not found: $log"
  [ -d "$dir" ] || die "supervisor-check --dir not found: $dir"
  case "$limit" in *[!0-9]*|"") die "--no-progress-limit must be a positive integer: $limit" ;; esac

  local class; class="$(classify_exit --exit-code "$code" --output "$log" --since-offset "$offset")"

  case "$class" in
    done)
      _write_supervisor_state "$state" 0 "$(_run_head "$dir")"
      echo "spawn-orchestrator: supervisor-check done"
      return 0
      ;;
    fatal:*)
      _supervisor_halt --dir "$dir" --label "$label" --reason "${class#fatal: }"
      return "$code"
      ;;
  esac

  # retry: a legitimate paused wake never counts against the guard.
  if _run_is_paused "$dir"; then
    _write_supervisor_state "$state" 0 "$(_run_head "$dir")"
    echo "spawn-orchestrator: supervisor-check retry (paused wake, no-progress guard skipped): ${class#retry: }"
    return "$code"
  fi

  local prev_head prev_count head count
  prev_head="$(_supervisor_state_field "$state" head)"
  prev_count="$(_supervisor_state_field "$state" count)"
  # A corrupt (non-numeric) count must not brick the guard via an arithmetic
  # error under `set -u` — treat it as a fresh start.
  case "$prev_count" in ''|*[!0-9]*) prev_count=0 ;; esac
  # An empty HEAD (non-git dir, git absent from the launchd PATH, an unborn
  # branch) must count AS no-progress, not silently reset the guard to 1 every
  # wake — a broken environment is exactly when the relaunch loop this guard
  # backstops is most likely. Sentinel it so empty == empty advances the counter.
  head="$(_run_head "$dir")"; head="${head:-unknown}"

  if [ -n "$prev_head" ] && [ "$prev_head" = "$head" ]; then
    count=$((prev_count + 1))
  else
    count=1
  fi
  _write_supervisor_state "$state" "$count" "$head"

  if [ "$count" -ge "$limit" ]; then
    _supervisor_halt --dir "$dir" --label "$label" \
      --reason "no forward progress after $count consecutive supervisor wakes (${class#retry: })"
  else
    echo "spawn-orchestrator: supervisor-check retry ($count/$limit consecutive, no progress): ${class#retry: }"
  fi
  return "$code"
}

# ---------------------------------------------------------------------------
# Task 18 — post-merge restack of stacked PRs (finding #25). Squash-merging a
# parent orphans a child two ways: LOUD (GitHub deletes the parent's branch,
# closing a child still based on it) and QUIET (the child still targets the
# parent's *branch*, so merging it lands on that branch, never on
# base_branch, and the PR looks perfectly healthy while doing nothing — worse
# than the loud failure because nothing alarms). `restack` mechanizes the
# four-command incantation a human would otherwise have to remember at the
# right moment, per child, in dependency order.
#
# IMPORTANT actor distinction (skills/auto-pilot/references/run-state.md
# "base_sha"): a HUMAN merging/reviewing a parent is the EXPECTED trigger for
# restack, never an error — the frozen `base_sha` diverging from the parent's
# post-review tip is exactly what review is supposed to do. The existing
# base_sha freeze/park guard exists to catch the ORCHESTRATOR moving a base
# mid-run; it does not apply here.
#
# `gh` is mockable: --gh <path> (default: resolved `gh` on PATH) lets the test
# suite point at a local stub script, so the git mechanics (rebase/push)
# exercise a real temp repo while every GitHub call is fully offline.
# ---------------------------------------------------------------------------

# Parse RUN.md's front matter + task table into the globals _RS_BASE_BRANCH and
# the index-aligned arrays _RS_TASK/_RS_BRANCH/_RS_BASE/_RS_BASE_SHA/_RS_PR.
# Reuses status()'s front-matter/table extraction (RUN.md's format is defined
# once in run-state.md; both readers walk the same `| ... |` rows).
_restack_read_run_md() {
  local run_md="$1"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"
  _RS_BASE_BRANCH="$(printf '%s\n' "$front" | grep -E '^base_branch:' | head -1 \
    | sed -e 's/^base_branch: *//' -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//')"
  [ -n "$_RS_BASE_BRANCH" ] || _RS_BASE_BRANCH="main"

  # _RS_NEWTIP[i] is filled in only by restack(): the rewritten tip a branch got
  # when THIS pass force-pushed it. A child of a branch we just rewrote must be
  # cascaded onto that new tip (see restack's "cascade" mode), or force-pushing
  # a parent silently orphans its own child inside the same run.
  _RS_TASK=(); _RS_BRANCH=(); _RS_BASE=(); _RS_BASE_SHA=(); _RS_PR=(); _RS_NEWTIP=()
  local line
  while IFS= read -r line; do
    # skip the header separator row (only pipes/colons/dashes/spaces)
    case "$line" in *[!'|'' ':-]*) ;; *) continue ;; esac
    local cols t b base bsha pr
    IFS='|' read -ra cols <<<"$line"
    [ "${#cols[@]}" -ge 7 ] || continue
    t="$(printf '%s' "${cols[1]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ "$t" != "task" ] || continue   # header row
    b="$(printf '%s' "${cols[3]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    base="$(printf '%s' "${cols[4]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    bsha="$(printf '%s' "${cols[5]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    pr="$(printf '%s' "${cols[6]}" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    _RS_TASK+=("$t"); _RS_BRANCH+=("$b"); _RS_BASE+=("$base"); _RS_BASE_SHA+=("$bsha"); _RS_PR+=("$pr")
    _RS_NEWTIP+=("")
  done < <(awk '/^\|/{print}' "$run_md")
}

# True (0) when a RUN.md cell is one of its "empty" spellings (the table uses
# both a bare hyphen and an em-dash depending on which doc wrote it).
_restack_empty() { case "$1" in ''|-|'—') return 0 ;; *) return 1 ;; esac; }

# Rebase <branch>'s commits (those after <base_sha>) onto <onto_ref> in a
# DEDICATED SCRATCH WORKTREE, never in the caller's tree, and print the
# resulting tip SHA on success.
#
# Why a scratch worktree is load-bearing (not just tidy): `git rebase --onto X
# Y <branch>` CHECKS OUT <branch> and leaves HEAD there. Run against the run
# worktree, restack would park the orchestrator's HEAD on a task branch — which
# is finding #23 exactly, the invariant task 13's `assert-run-head` guard
# exists to enforce — and a mid-rebase crash would strand the run worktree on a
# task branch, so `--resume` would read RUN.md from the wrong tree. The scratch
# worktree is created DETACHED at the branch's REMOTE tip (authoritative, and a
# detached checkout can't collide with the branch being checked out elsewhere),
# and is removed on every exit path: success, conflict, and push rejection.
#
# Args: <repo> <remote> <branch> <base_sha> <onto_ref> <lease_sha>
# Exit: 0 rebased+pushed (tip on stdout) | 3 rebase conflict | 4 push rejected
_restack_rebase_push() {
  local repo="$1" remote="$2" branch="$3" base_sha="$4" onto_ref="$5" lease_sha="$6"
  local sw; sw="$(mktemp -d "${TMPDIR:-/tmp}/restack-wt.XXXXXX")" || return 4
  rm -rf "$sw"   # `git worktree add` requires the path NOT to exist yet

  git -C "$repo" worktree add --detach "$sw" "$remote/$branch" >/dev/null 2>&1 || {
    rm -rf "$sw"; return 4
  }
  # Cleanup is unconditional from here on — every `return` below goes through it.
  local rc=0 tip=""
  if git -C "$sw" rebase --onto "$onto_ref" "$base_sha" >/dev/null 2>&1; then
    tip="$(git -C "$sw" rev-parse HEAD)"
    # Explicit lease (branch:sha we actually fetched), not the bare
    # --force-with-lease default: the bare form trusts the remote-tracking ref,
    # which a concurrent fetch could have already advanced — silently turning
    # the lease into a plain --force.
    git -C "$sw" push --force-with-lease="$branch:$lease_sha" "$remote" "HEAD:$branch" >/dev/null 2>&1 \
      || rc=4
  else
    git -C "$sw" rebase --abort >/dev/null 2>&1
    rc=3
  fi
  git -C "$repo" worktree remove --force "$sw" >/dev/null 2>&1
  rm -rf "$sw"
  [ "$rc" = 0 ] || return "$rc"
  printf '%s' "$tip"
}

restack() {
  local run_dir="" repo="" remote="origin" gh_bin="" dry=0
  while [ $# -gt 0 ]; do
    case "$1" in
      --run-dir) [ $# -ge 2 ] || die "missing value for --run-dir"; run_dir="$2"; shift 2 ;;
      --repo) [ $# -ge 2 ] || die "missing value for --repo"; repo="$2"; shift 2 ;;
      --remote) [ $# -ge 2 ] || die "missing value for --remote"; remote="$2"; shift 2 ;;
      --gh) [ $# -ge 2 ] || die "missing value for --gh"; gh_bin="$2"; shift 2 ;;
      --dry-run) dry=1; shift ;;
      *) die "unknown restack argument: $1" ;;
    esac
  done
  [ -n "$run_dir" ] || die "restack requires --run-dir <dir containing .auto-pilot/RUN.md>"
  case "$run_dir" in /*) ;; *) die "--run-dir must be absolute (fail-closed): $run_dir" ;; esac
  [ -n "$repo" ] || repo="$run_dir"
  case "$repo" in /*) ;; *) die "--repo must be absolute (fail-closed): $repo" ;; esac
  git -C "$repo" rev-parse --git-dir >/dev/null 2>&1 || die "--repo is not a git worktree (fail-closed): $repo"
  [ -n "$gh_bin" ] || gh_bin="$(command -v gh 2>/dev/null)" || true
  [ -n "$gh_bin" ] || die "gh not found (fail-closed): pass --gh <path> (mockable in tests) or put gh on PATH"

  _restack_read_run_md "$run_dir/.auto-pilot/RUN.md"
  local base_branch="$_RS_BASE_BRANCH"

  # HEAD-invariant guard (finding #23 / task 13's `assert-run-head`): restack
  # must NEVER move the caller's HEAD. The rebases below run in throwaway
  # worktrees, so this records HEAD to ASSERT it at every exit path rather than
  # to restore it — a restack that somehow moved the run worktree's HEAD is a
  # bug, not something to paper over.
  _RS_HEAD_BEFORE="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
  _RS_REF_BEFORE="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  [ -n "$_RS_HEAD_BEFORE" ] || die "cannot read HEAD (fail-closed): $repo"
  # Fail closed on a dirty or mid-operation tree: `git worktree add` off a repo
  # that is itself mid-rebase/mid-merge, or carrying uncommitted work, is how a
  # "helpful" automated recovery destroys a human's in-progress state.
  [ -z "$(git -C "$repo" status --porcelain 2>/dev/null)" ] \
    || die "--repo worktree is dirty (fail-closed): commit or stash before restacking: $repo"
  local gitdir; gitdir="$(git -C "$repo" rev-parse --git-dir 2>/dev/null)"
  case "$gitdir" in /*) ;; *) gitdir="$repo/$gitdir" ;; esac
  { [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] || [ -e "$gitdir/MERGE_HEAD" ]; } \
    && die "--repo worktree is mid-rebase/mid-merge (fail-closed): finish or abort it first: $repo"

  # Fetch ALL refs once, up front: every rebase below must see the SAME remote
  # view. A stale local <remote>/<base_branch> is exactly how a "clean" rebase
  # could silently miss the parent's post-hand-off review commits
  # (run-state.md's "restacked child is stale" note), and the per-branch
  # remote-tracking refs are what the push leases are computed against.
  git -C "$repo" fetch "$remote" >/dev/null 2>&1 \
    || die "fetch failed (fail-closed): $remote"

  local n="${#_RS_TASK[@]}" pass=0 progressed=1 restacked=0 failed=0 flagged=0
  local -a report=()
  # Fixed-point over the table, because a chain resolves one link per pass: a
  # grandchild only becomes actionable once its own parent has been rewritten
  # (cascade) or retargeted (onto-base) by an earlier pass.
  while [ "$progressed" = 1 ] && [ "$pass" -le "$n" ]; do
    progressed=0; pass=$((pass + 1))
    local i
    for ((i = 0; i < n; i++)); do
      local task="${_RS_TASK[$i]}" branch="${_RS_BRANCH[$i]}" base="${_RS_BASE[$i]}" \
            base_sha="${_RS_BASE_SHA[$i]}" pr="${_RS_PR[$i]}"
      [ "$base" != "$base_branch" ] || continue   # independent task — nothing to restack
      _restack_empty "$pr" && continue            # no PR yet — nothing to retarget
      local pr_num="${pr#\#}"

      # Idempotent: trust the PR's OWN live base over our (possibly stale)
      # RUN.md column — a child already retargeted to base_branch is a no-op,
      # never a re-rebase/re-push/re-edit.
      local live_base
      live_base="$("$gh_bin" pr view "$pr_num" --json baseRefName --jq .baseRefName 2>/dev/null)"
      if [ "$live_base" = "$base_branch" ]; then
        echo "spawn-orchestrator: restack $task already based on $base_branch (no-op)"
        continue
      fi

      # Never force-push a child whose OWN PR is no longer open: `gh pr edit
      # --base` only works on an open PR, so a MERGED child is already done and a
      # CLOSED one (LOUD-orphaned when its parent branch was deleted) needs a
      # human to reopen/recreate it — rewriting either's branch here would be a
      # destructive no-win. Check state BEFORE any rebase/push.
      local child_state
      child_state="$("$gh_bin" pr view "$pr_num" --json state --jq .state 2>/dev/null)"
      if [ "$child_state" = "MERGED" ]; then
        echo "spawn-orchestrator: restack $task PR #$pr_num already MERGED (no-op)"
        continue
      fi
      if [ "$child_state" = "CLOSED" ]; then
        echo "spawn-orchestrator: restack $task DEFECT — PR #$pr_num is CLOSED (LOUD-orphaned by a deleted parent branch); a human must reopen or recreate it — NOT force-pushing"
        report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): the PR is CLOSED — LOUD-orphaned when its parent branch was deleted on merge. A human must reopen or recreate it; restack will not force-push a closed PR's branch (finding #25, LOUD case).")
        flagged=$((flagged + 1))
        continue
      fi

      local parent_pr="" j pidx=-1
      for ((j = 0; j < n; j++)); do
        if [ "${_RS_BRANCH[$j]}" = "$base" ]; then pidx=$j; parent_pr="${_RS_PR[$j]#\#}"; break; fi
      done
      [ "$pidx" -ge 0 ] || continue   # parent not tracked in this run

      # Two distinct reasons a child needs rewriting, and they are NOT the same op:
      #
      #   onto-base  the parent's PR MERGED (squashed onto base_branch) → drop the
      #              parent's now-squashed commits and retarget the PR to base_branch.
      #   cascade    the parent was itself force-pushed by THIS run (onto-base or an
      #              earlier cascade) → the child still carries the parent's OLD,
      #              rewritten commits. Rebase it onto the parent's NEW tip. Its PR
      #              base stays the parent branch (still a live branch, now correctly
      #              targeting base_branch) — retargeting it to base_branch here would
      #              re-propose the parent's whole changeset.
      #
      # Without cascade, force-pushing a parent silently orphans its own child inside
      # the very run that was supposed to fix orphaned children.
      local newtip="${_RS_NEWTIP[$pidx]}" mode="" onto_ref="" retarget=""
      if [ -n "$newtip" ] && ! _restack_empty "$base_sha" && [ "$base_sha" != "$newtip" ]; then
        mode="cascade"; onto_ref="$newtip"
      else
        _restack_empty "$parent_pr" && continue   # parent has no PR — can't know if it merged
        local parent_state
        parent_state="$("$gh_bin" pr view "$parent_pr" --json state --jq .state 2>/dev/null)"
        if [ "$parent_state" = "MERGED" ]; then
          if _restack_empty "$base_sha"; then
            echo "spawn-orchestrator: restack $task FAILED — parent PR #$parent_pr merged but no recorded base_sha (fail-closed, needs a human)"
            failed=$((failed + 1))
            continue
          fi
          mode="onto-base"; onto_ref="$remote/$base_branch"; retarget="$base_branch"
        else
          # RESUMABLE cascade: the parent's PR is still OPEN, but a PRIOR restack
          # run may have already rewritten it (retargeted to base_branch and
          # force-pushed). This run's in-memory _RS_NEWTIP is empty for it, so
          # detect the rewrite from the REMOTE: if the parent is already based on
          # base_branch and its remote tip has moved off this child's recorded
          # base_sha, cascade the child onto that tip. Without this, a partial
          # earlier run silently strands the grandchild while reporting success.
          local parent_live_base parent_remote_tip
          parent_live_base="$("$gh_bin" pr view "$parent_pr" --json baseRefName --jq .baseRefName 2>/dev/null)"
          parent_remote_tip="$(git -C "$repo" rev-parse "$remote/$base" 2>/dev/null)"
          if [ "$parent_live_base" = "$base_branch" ] && ! _restack_empty "$base_sha" \
             && [ -n "$parent_remote_tip" ] && [ "$parent_remote_tip" != "$base_sha" ]; then
            # Idempotent: a cascade-mode child keeps its PR base on the parent
            # branch, so the top-of-loop live-base no-op check can't catch an
            # already-cascaded child. If it already sits on the parent's current
            # tip, there is nothing to do — skip, rather than re-run a no-op
            # rebase that churns REPORT.md and re-flags a child nothing touched.
            if git -C "$repo" merge-base --is-ancestor "$parent_remote_tip" "$remote/$branch" 2>/dev/null; then
              echo "spawn-orchestrator: restack $task already cascaded onto $base's tip (no-op)"
              continue
            fi
            mode="cascade"; onto_ref="$parent_remote_tip"
          else
            # A human reviewing/merging the parent is the EXPECTED trigger; an
            # unmerged, un-rewritten parent just means this child isn't ready yet.
            continue
          fi
        fi
      fi

      # The exact incantation, copy-pasteable — printed BEFORE execution, so it
      # reaches the log (and REPORT.md below) even if restack never runs: without
      # the automation the human is still one paste from correct.
      echo "spawn-orchestrator: restack $task ($mode) commands:"
      echo "  git fetch $remote"
      echo "  git rebase --onto $onto_ref $base_sha $branch"
      echo "  git push --force-with-lease $remote $branch"
      [ -n "$retarget" ] && echo "  gh pr edit $pr_num --base $retarget"
      [ "$dry" = 1 ] && continue

      local lease_sha; lease_sha="$(git -C "$repo" rev-parse "$remote/$branch" 2>/dev/null)"
      if [ -z "$lease_sha" ]; then
        echo "spawn-orchestrator: restack $task FAILED — no $remote/$branch to lease against (fail-closed; branch never pushed?)"
        failed=$((failed + 1))
        continue
      fi

      local tip rc
      tip="$(_restack_rebase_push "$repo" "$remote" "$branch" "$base_sha" "$onto_ref" "$lease_sha")"; rc=$?
      case "$rc" in
        3)
          echo "spawn-orchestrator: restack $task FAILED — rebase conflict (fail-closed, NOT force-pushing; a human must resolve)"
          failed=$((failed + 1)); continue ;;
        4)
          echo "spawn-orchestrator: restack $task FAILED — force-with-lease push rejected (fail-closed, remote moved since fetch; re-run restack)"
          failed=$((failed + 1)); continue ;;
      esac

      # The rebase + force-push already happened, so a child's downstream cascade
      # must see the new tip regardless of what the retarget does below.
      _RS_BASE_SHA[$i]="$onto_ref"   # this child's parent-tip is now the ref it sits on
      _RS_NEWTIP[$i]="$tip"          # so ITS children cascade onto the tip we just pushed
      restacked=$((restacked + 1)); progressed=1

      local retarget_ok=1
      if [ -n "$retarget" ]; then
        if "$gh_bin" pr edit "$pr_num" --base "$retarget" >/dev/null 2>&1; then
          _RS_BASE[$i]="$base_branch"
        else
          # Pushed but NOT retargeted: the PR still points at the merged parent
          # branch and reaches nothing — the exact QUIET orphan restack exists to
          # catch. Do NOT mark _RS_BASE as base_branch (that would hide it from
          # the orphan detector) and record it as a DEFECT, not a success.
          retarget_ok=0
          echo "spawn-orchestrator: restack $task DEFECT — rebased and force-pushed $branch, but 'gh pr edit --base $retarget' failed; PR #$pr_num still targets the merged parent branch (retarget by hand: gh pr edit $pr_num --base $retarget)"
          report+=("- **DEFECT — $task** (PR #$pr_num, \`$branch\`): rebased and force-pushed onto \`$onto_ref\`, but retargeting its PR base to \`$retarget\` FAILED — it still points at the merged parent branch and reaches nothing until a human runs \`gh pr edit $pr_num --base $retarget\` (finding #25, QUIET case).")
          flagged=$((flagged + 1))
        fi
      fi
      [ "$retarget_ok" = 1 ] || continue

      echo "spawn-orchestrator: restack $task done ($mode) — force-pushed $branch, PR #$pr_num${retarget:+ retargeted to $retarget}"
      # The re-verification requirement travels with the child into REPORT.md —
      # a clean rebase proves nothing about whether the child still honors the
      # parent's post-hand-off review commits (run-state.md "Restack").
      report+=("- **$task** (PR #$pr_num, \`$branch\`) restacked ($mode) onto \`$onto_ref\`. **Re-verify required:** its previous green ran against the OLD base, and its co-review is **STALE** — it approved code relative to the pre-review parent. Re-run verify against the new base, diff-audit the child against the parent's post-hand-off review commits, and re-run co-review if the parent's review touched files this child also touches.")
    done
  done

  # Orphan detection (ties to task 16's alarm channel): a chained PR reaching
  # here still pointed somewhere other than base_branch after this pass — flag
  # it as a defect rather than waiting for a human to notice by diffing the
  # open-PR list against RUN.md by hand (the only reason finding #25 was
  # caught at all).
  local i
  for ((i = 0; i < n; i++)); do
    local task="${_RS_TASK[$i]}" base="${_RS_BASE[$i]}" pr="${_RS_PR[$i]}"
    [ "$base" != "$base_branch" ] || continue
    _restack_empty "$pr" && continue
    local pr_num="${pr#\#}"
    local live_base live_state
    live_base="$("$gh_bin" pr view "$pr_num" --json baseRefName --jq .baseRefName 2>/dev/null)"
    live_state="$("$gh_bin" pr view "$pr_num" --json state --jq .state 2>/dev/null)"
    if [ -z "$live_base" ]; then
      echo "spawn-orchestrator: DEFECT $task PR #$pr_num — base ref deleted or unreadable (orphaned by a merged/closed parent, finding #25 LOUD case)"
      report+=("- **DEFECT — $task** (PR #$pr_num): base ref deleted or unreadable — orphaned by a merged/closed parent (finding #25, LOUD case). Needs a human.")
      flagged=$((flagged + 1))
      continue
    fi
    [ "$live_base" != "$base_branch" ] || continue
    local base_pr="" k
    for ((k = 0; k < n; k++)); do
      if [ "${_RS_BRANCH[$k]}" = "$live_base" ]; then base_pr="${_RS_PR[$k]#\#}"; break; fi
    done
    local base_state=""
    [ -n "$base_pr" ] && ! _restack_empty "$base_pr" \
      && base_state="$("$gh_bin" pr view "$base_pr" --json state --jq .state 2>/dev/null)"
    if [ "$live_state" = "CLOSED" ] || [ "$base_state" = "MERGED" ]; then
      echo "spawn-orchestrator: DEFECT $task PR #$pr_num — base=$live_base is a merged/closed branch; this PR never reaches $base_branch (finding #25 QUIET case)"
      report+=("- **DEFECT — $task** (PR #$pr_num): base \`$live_base\` is a merged/closed branch, so this PR never reaches \`$base_branch\` — it looks healthy while doing nothing (finding #25, QUIET case). Needs a human.")
      flagged=$((flagged + 1))
    fi
  done

  # Surface the restack outcome where a HUMAN actually reads it. stdout only
  # reaches orchestrator.log; REPORT.md is the file the human wakes up to, and
  # the stale-co-review warning is worthless if it lands somewhere they never
  # look. Append-only (never rewrites the report), and skipped entirely when
  # there is nothing to say — so an idempotent no-op restack does not churn it.
  if [ "${#report[@]}" -gt 0 ]; then
    local report_md="$run_dir/.auto-pilot/REPORT.md"
    {
      printf '\n## Restack — %s\n\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
      printf '%s\n' "${report[@]}"
    } >>"$report_md" 2>/dev/null \
      || echo "spawn-orchestrator: restack WARNING — could not append to $report_md (findings are on stdout only)"
  fi

  # Assert the HEAD invariant on EVERY exit path (finding #23 / task 13): the
  # run worktree's HEAD must be exactly where restack found it.
  local head_after ref_after
  head_after="$(git -C "$repo" rev-parse HEAD 2>/dev/null)"
  ref_after="$(git -C "$repo" rev-parse --abbrev-ref HEAD 2>/dev/null)"
  { [ "$head_after" = "$_RS_HEAD_BEFORE" ] && [ "$ref_after" = "$_RS_REF_BEFORE" ]; } \
    || die "restack moved the caller's HEAD ($_RS_REF_BEFORE@$_RS_HEAD_BEFORE -> $ref_after@$head_after) — this is a bug, not a recoverable state"

  echo "spawn-orchestrator: restack summary — restacked=$restacked failed=$failed defects=$flagged"
  # Non-zero on EITHER a hard failure or a flagged defect (a LOUD/QUIET orphan, a
  # failed retarget): a supervisor checking `$?` must see that a human is needed,
  # not read a defects run as a clean success.
  { [ "$failed" -eq 0 ] && [ "$flagged" -eq 0 ]; } || return 2
}

# Orchestrate the spawn in the ONE safe order: build the launch artifacts, then
# smoke-test auth THROUGH the wrapper, and only if that passes, detach + record.
# The ordering is load-bearing — detaching before auth is verified is exactly the
# "fails silently at 3am" mode the pre-flight exists to prevent. `--dry-run` prints
# the plan without executing (so the order is testable offline).
launch() {
  local dry=0
  local -a wl=() sm=() dt=() rh=()
  local plist="" out_script="" out_plist="" handle="" until="" label=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dry-run) dry=1; shift ;;
      --profile) wl+=(--profile "$2"); sm+=(--profile "$2"); shift 2 ;;
      --settings) wl+=(--settings "$2"); sm+=(--settings "$2"); shift 2 ;;
      --workdir|--log|--prompt-file|--interval|--throttle|--plist-template|--claude-bin|--path|--tmpdir) wl+=("$1" "$2"); shift 2 ;;
      --until) wl+=(--until "$2"); until="$2"; shift 2 ;;
      --label) wl+=(--label "$2"); label="$2"; shift 2 ;;
      --out-script) wl+=(--out-script "$2"); out_script="$2"; shift 2 ;;
      --out-plist) wl+=(--out-plist "$2"); out_plist="$2"; plist="$2"; shift 2 ;;
      --handle) handle="$2"; shift 2 ;;
      *) die "unknown launch argument: $1" ;;
    esac
  done
  [ -n "$out_plist" ] && [ -n "$label" ] && [ -n "$handle" ] || die "launch requires --out-plist, --label, and --handle"

  if [ "$dry" = 1 ]; then
    printf 'launch plan (order is load-bearing — auth is verified BEFORE we spawn):\n'
    printf '  1. write-launch  -> %s %s\n' "$out_script" "$out_plist"
    printf '  2. smoke-test    (claude -p through the sandbox wrapper + settings)\n'
    printf '  3. detach        (launchctl bootstrap %s)\n' "$plist"
    printf '  4. record-handle -> %s\n' "$handle"
    return 0
  fi

  write_launch "${wl[@]}"
  smoke_test "${sm[@]}"            # BEFORE detach — a dead credential stops here.
  teardown --label "$label" >/dev/null 2>&1   # clear any stale registration; bootstrap fails on a dup label.
  detach --plist "$plist"
  # `launchctl bootstrap` is asynchronous — the pid may not be reported for a beat,
  # so poll briefly rather than reading once (a single read fails closed spuriously).
  local pid="" i
  for i in 1 2 3 4 5 6 7 8 9 10; do
    pid="$(launchctl print "gui/$(id -u)/$label" 2>/dev/null | awk -F'= ' '/[^a-z]pid = /{print $2; exit}')"
    case "$pid" in ''|*[!0-9]*) pid="" ;; *) break ;; esac
    sleep 0.2
  done
  if [ -z "$pid" ]; then
    teardown --label "$label" >/dev/null 2>&1   # don't leave an orphaned job loaded
    die "detached but could not read the orchestrator PID from launchctl (job booted out)"
  fi
  record_handle --pid "$pid" --until "$until" --out "$handle"
  echo "spawn-orchestrator: launched pid=$pid label=$label"
}

# ---------------------------------------------------------------------------
# Task 4 — verify broker: run the run's verify_command OUTSIDE the jail.
#
# A sandbox-exec-confined process's children INHERIT the profile, so the jailed
# orchestrator cannot spawn an un-jailed verifier — and the run's verify
# (`bash scripts/check.sh`, which runs `bash scripts/test-*.sh`) execve-denies
# in-jail (finding #4: `bad interpreter: Operation not permitted`, exit 126,
# regardless of the diff). Verify therefore runs in a SEPARATE, UN-JAILED launchd
# job (write-verify-broker) that polls a sentinel dir and runs a COMMAND-PINNED
# verify in the requested worktree:
#
#   jailed orchestrator             un-jailed broker (launchd, NO sandbox-exec)
#   -------------------             ------------------------------------------
#   verify-request  ── writes ──▶   <dir>/<id>.request {worktree, cmd_hash}
#                                   verify-broker: refuse unless the request's
#                                   cmd_hash == the broker's OWN pinned hash AND
#                                   the worktree is under the run root, then run
#                                   its OWN pinned command there and write
#   verify-await   ◀── reads ───    <dir>/<id>.result  {code, output}
#
# Trust boundary (launch-runtime.md "Sandbox profile"): verify runs un-jailed, so
# it executes the diff-under-test with full privilege + network — the SAME trust
# the human extends re-running check.sh before merge, moved earlier. It is bounded
# by (a) command PINNING — the broker runs a FIXED string baked in at install,
# NEVER a command from the request; the request carries only a hash both sides must
# agree on — and (b) worktree-only execution — the broker refuses a worktree
# outside the run root. Neither the request nor an agent ever supplies the command.
# ---------------------------------------------------------------------------

# Pin hash of a verify command string (sha256 hex). Request and broker compute it
# identically, so a stale request or a re-pinned broker is caught before any run.
verify_hash() {
  command -v shasum >/dev/null 2>&1 || die "shasum not available (needed for verify pin)"
  printf '%s' "$1" | shasum -a 256 | awk '{print $1}'
}

# Atomically write a result file: `code:` on line 1, then the raw output after a
# fixed marker so verify-await can split it back out losslessly.
_write_verify_result() {
  local f="$1" code="$2" out="$3" d; d="$(dirname "$f")"
  local tmp; tmp="$(mktemp "$d/.res.XXXXXX")" || return 1
  { printf 'code: %s\n' "$code"; printf -- '--- output ---\n'; printf '%s\n' "$out"; } >"$tmp" \
    || { rm -f "$tmp"; return 1; }
  mv "$tmp" "$f" || { rm -f "$tmp"; return 1; }
}

# (jailed side) Drop a verify request the un-jailed broker will pick up. The
# request names the worktree + a hash of the pinned command — never the command.
verify_request() {
  local dir="" worktree="" cmd_hash="" id=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --worktree) [ $# -ge 2 ] || die "missing value for --worktree"; worktree="$2"; shift 2 ;;
      --cmd-hash) [ $# -ge 2 ] || die "missing value for --cmd-hash"; cmd_hash="$2"; shift 2 ;;
      --id) [ $# -ge 2 ] || die "missing value for --id"; id="$2"; shift 2 ;;
      *) die "unknown verify-request argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$worktree" ] && [ -n "$cmd_hash" ] \
    || die "verify-request requires --sentinel-dir, --worktree, and --cmd-hash"
  case "$cmd_hash" in *[!0-9a-f]*|"") die "--cmd-hash must be lowercase hex (fail-closed): $cmd_hash" ;; esac
  local wt; wt="$(canonicalize "$worktree")" || exit 2
  [ -d "$wt" ] || die "verify-request --worktree is not a directory (fail-closed): $wt"
  mkdir -p "$dir" || die "cannot create sentinel dir: $dir"
  local d; d="$(cd "$dir" && pwd -P)" || die "cannot resolve sentinel dir: $dir"
  if [ -n "$id" ]; then
    case "$id" in *[!A-Za-z0-9._-]*) die "--id must be [A-Za-z0-9._-] (fail-closed): $id" ;; esac
  else
    local t; t="$(mktemp "$d/req.XXXXXX")" || die "mktemp failed"; id="$(basename "$t")"; rm -f "$t"
  fi
  local reqfile="$d/$id.request" tmp
  tmp="$(mktemp "$d/.req.XXXXXX")" || die "mktemp failed"
  { printf 'worktree: %s\n' "$wt"; printf 'cmd_hash: %s\n' "$cmd_hash"; } >"$tmp" \
    || { rm -f "$tmp"; die "cannot write request"; }
  mv "$tmp" "$reqfile" || { rm -f "$tmp"; die "cannot place request: $reqfile"; }
  echo "spawn-orchestrator: verify-request $id"
}

# (UN-JAILED side, run by the broker launchd job) One scan: for each pending
# request, refuse unless its cmd_hash matches the broker's OWN pinned hash and the
# worktree is under the run root, then run the PINNED command there and write the
# result. Scans once and exits — the launchd StartInterval drives the cadence.
verify_broker() {
  local dir="" cmd="" cmd_hash="" root=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; cmd="$2"; shift 2 ;;
      --cmd-hash) [ $# -ge 2 ] || die "missing value for --cmd-hash"; cmd_hash="$2"; shift 2 ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; root="$2"; shift 2 ;;
      *) die "unknown verify-broker argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$cmd" ] && [ -n "$root" ] \
    || die "verify-broker requires --sentinel-dir, --verify-cmd, and --confine-under"
  # The broker runs its OWN pinned command; --cmd-hash (if given) must match it —
  # this catches an install/args mismatch, never authorizes a request's command.
  local pin; pin="$(verify_hash "$cmd")"
  if [ -n "$cmd_hash" ] && [ "$cmd_hash" != "$pin" ]; then
    die "verify-broker --cmd-hash does not match --verify-cmd (fail-closed)"
  fi
  local rootc; rootc="$(canonicalize "$root")" || exit 2
  local d; d="$(cd "$dir" 2>/dev/null && pwd -P)" || die "sentinel dir not found: $dir"

  local req handled=0
  for req in "$d"/*.request; do
    [ -e "$req" ] || continue
    handled=$((handled + 1))
    local id; id="$(basename "$req" .request)"
    local resultf="$d/$id.result"
    local wt rh
    wt="$(sed -n 's/^worktree: //p' "$req" | head -1)"
    rh="$(sed -n 's/^cmd_hash: //p' "$req" | head -1)"
    rm -f "$req"    # claim it — a handled request never re-runs
    if [ "$rh" != "$pin" ]; then
      _write_verify_result "$resultf" 2 "verify-broker: request cmd_hash mismatch (pinned=$pin request=$rh) — refused"
      continue
    fi
    local wtc; wtc="$(cd "$wt" 2>/dev/null && pwd -P)"
    if [ -z "$wtc" ] || { [ "$wtc" != "$rootc" ] && [ "${wtc#"$rootc"/}" = "$wtc" ]; }; then
      _write_verify_result "$resultf" 2 "verify-broker: worktree escapes --confine-under (worktree=$wt root=$rootc) — refused"
      continue
    fi
    # Run the PINNED command in the worktree. `bash -c "$cmd"` on a trusted, pinned
    # string is the same execution the human runs re-invoking check.sh pre-merge.
    local out code
    out="$(cd "$wtc" && bash -c "$cmd" 2>&1)"; code=$?
    _write_verify_result "$resultf" "$code" "$out"
  done
  echo "spawn-orchestrator: verify-broker scanned $d (handled=$handled, pin=$pin)"
}

# (jailed side) Block until the broker writes the result for <id>, then print its
# code + output and exit with the verify command's code (0 = verify passed).
verify_await() {
  local dir="" id="" timeout=600 interval=2
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; dir="$2"; shift 2 ;;
      --id) [ $# -ge 2 ] || die "missing value for --id"; id="$2"; shift 2 ;;
      --timeout) [ $# -ge 2 ] || die "missing value for --timeout"; timeout="$2"; shift 2 ;;
      --interval) [ $# -ge 2 ] || die "missing value for --interval"; interval="$2"; shift 2 ;;
      *) die "unknown verify-await argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$id" ] || die "verify-await requires --sentinel-dir and --id"
  case "$timeout$interval" in *[!0-9]*) die "--timeout/--interval must be integers" ;; esac
  [ "$interval" -gt 0 ] || die "--interval must be > 0 (else the wait loop never advances)"
  local d; d="$(cd "$dir" 2>/dev/null && pwd -P)" || die "sentinel dir not found: $dir"
  local resultf="$d/$id.result" waited=0
  while [ ! -e "$resultf" ]; do
    [ "$waited" -lt "$timeout" ] || die "verify-await timed out after ${timeout}s waiting for $id (broker down?)"
    sleep "$interval"; waited=$((waited + interval))
  done
  local code; code="$(sed -n 's/^code: //p' "$resultf" | head -1)"
  echo "spawn-orchestrator: verify-await $id code=$code"
  sed -n '/^--- output ---$/,$p' "$resultf" | sed '1d'
  case "$code" in ''|*[!0-9]*) return 2 ;; *) return "$code" ;; esac
}

# Render an UN-JAILED broker launch script + its launchd plist. The verify command
# is PINNED here (baked into the script from the run's resolved verify_command), so
# the broker never runs anything a request or an agent supplies. The broker job runs
# `/bin/bash <script>` directly — NO sandbox-exec — which is exactly how it escapes
# the orchestrator's jail to reach a working execve.
write_verify_broker() {
  local sentinel="" verify_cmd="" root="" label="" workdir="" log="" path="" \
        self="$ROOT/scripts/spawn-orchestrator.sh" interval="10" throttle="10" \
        out_script="" out_plist="" plist_template="$PLIST_TEMPLATE_DEFAULT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --sentinel-dir) [ $# -ge 2 ] || die "missing value for --sentinel-dir"; sentinel="$2"; shift 2 ;;
      --verify-cmd) [ $# -ge 2 ] || die "missing value for --verify-cmd"; verify_cmd="$2"; shift 2 ;;
      --confine-under) [ $# -ge 2 ] || die "missing value for --confine-under"; root="$2"; shift 2 ;;
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --workdir) [ $# -ge 2 ] || die "missing value for --workdir"; workdir="$2"; shift 2 ;;
      --log) [ $# -ge 2 ] || die "missing value for --log"; log="$2"; shift 2 ;;
      --path) [ $# -ge 2 ] || die "missing value for --path"; path="$2"; shift 2 ;;
      --self) [ $# -ge 2 ] || die "missing value for --self"; self="$2"; shift 2 ;;
      --interval) [ $# -ge 2 ] || die "missing value for --interval"; interval="$2"; shift 2 ;;
      --throttle) [ $# -ge 2 ] || die "missing value for --throttle"; throttle="$2"; shift 2 ;;
      --out-script) [ $# -ge 2 ] || die "missing value for --out-script"; out_script="$2"; shift 2 ;;
      --out-plist) [ $# -ge 2 ] || die "missing value for --out-plist"; out_plist="$2"; shift 2 ;;
      --plist-template) [ $# -ge 2 ] || die "missing value for --plist-template"; plist_template="$2"; shift 2 ;;
      *) die "unknown write-verify-broker argument: $1" ;;
    esac
  done
  [ -n "$out_script" ] && [ -n "$out_plist" ] || die "write-verify-broker requires --out-script and --out-plist"
  [ -n "$sentinel" ] && [ -n "$verify_cmd" ] && [ -n "$root" ] \
    || die "write-verify-broker requires --sentinel-dir, --verify-cmd, and --confine-under"
  [ -n "$label" ] || die "write-verify-broker requires --label"
  case "$label" in *[!A-Za-z0-9._-]*) die "--label must be [A-Za-z0-9._-] (fail-closed): $label" ;; esac
  [ -n "$path" ] || die "write-verify-broker requires --path <PATH> (a launchd job has a minimal PATH)"
  [ -n "$workdir" ] && [ -d "$workdir" ] || die "write-verify-broker requires an existing --workdir"
  [ -n "$log" ] || die "write-verify-broker requires --log"
  [ -f "$self" ] || die "spawn-orchestrator.sh not found (fail-closed): $self"
  [ -f "$plist_template" ] || die "plist template not found: $plist_template"
  case "$interval$throttle" in *[!0-9]*) die "--interval/--throttle must be integers" ;; esac

  local pin; pin="$(verify_hash "$verify_cmd")"
  local tmp; tmp="$(mktemp "${TMPDIR:-/tmp}/verify-broker-launch.XXXXXX")" || die "mktemp failed"
  {
    printf '#!/usr/bin/env bash\n'
    printf '# Auto-pilot verify BROKER (generated — do not edit). Runs UN-JAILED so\n'
    printf '# the run verify (bash scripts/check.sh) reaches a working execve. The\n'
    printf '# verify command below is PINNED (pin=%s); the broker never runs a\n' "$pin"
    printf '# command supplied by a request or an agent.\n'
    printf 'set -uo pipefail\n'
    printf 'export PATH=%q\n' "$path"
    printf 'cd %q\n' "$workdir"
    printf 'exec /bin/bash %q verify-broker \\\n' "$self"
    printf '  --sentinel-dir %q \\\n' "$sentinel"
    printf '  --verify-cmd %q \\\n' "$verify_cmd"
    printf '  --cmd-hash %q \\\n' "$pin"
    printf '  --confine-under %q \\\n' "$root"
    printf '  >>%q 2>&1\n' "$log"
  } >"$tmp" || { rm -f "$tmp"; die "failed to write broker launch script"; }
  mv "$tmp" "$out_script" || { rm -f "$tmp"; die "failed to write broker launch script: $out_script"; }
  chmod +x "$out_script"

  tmp="$(mktemp "${TMPDIR:-/tmp}/verify-broker-plist.XXXXXX")" || die "mktemp failed"
  render_plist "$label" "$out_script" "$workdir" "$log" "$interval" "$throttle" "$plist_template" >"$tmp" \
    || { rm -f "$tmp"; die "failed to render broker plist"; }
  mv "$tmp" "$out_plist" || { rm -f "$tmp"; die "failed to write broker plist: $out_plist"; }
  echo "spawn-orchestrator: verify-broker written $out_script $out_plist (pin $pin)"
}

# Normalize whitespace (collapse runs, trim ends) so a recorded lstart string
# and a freshly-read one compare equal regardless of incidental spacing.
_norm_ws() { printf '%s' "$1" | awk '{$1=$1; print}'; }

# Pull a single top-level "key: value" field out of a captured front-matter
# block (global $front, set by status() before calling this). Anchors on
# `^key:` so e.g. "until:" never matches "paused_until:". Strips a trailing
# ` # comment`, trailing whitespace, then a wrapping pair of single or double
# quotes.
_front_field() {
  local key="$1"
  printf '%s\n' "$front" | grep -E "^${key}:" | head -1 \
    | sed -e "s/^${key}: *//" -e 's/[[:space:]]*#.*$//' -e 's/[[:space:]]*$//' \
          -e "s/^'\(.*\)'\$/\1/" -e 's/^"\(.*\)"$/\1/'
}

# Read-only: report the run's live state in one shot. Never writes anything.
status() {
  local label="" dir="$PWD"
  while [ $# -gt 0 ]; do
    case "$1" in
      --label) [ $# -ge 2 ] || die "missing value for --label"; label="$2"; shift 2 ;;
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      *) die "unknown status argument: $1" ;;
    esac
  done
  [ -n "$label" ] || die "status requires --label"
  case "$dir" in /*) ;; *) die "--dir must be absolute (fail-closed): $dir" ;; esac

  local run_md="$dir/.auto-pilot/RUN.md"
  [ -f "$run_md" ] || die "no run state found (fail-closed): $run_md"
  local log="$dir/.auto-pilot/orchestrator.log"
  local sentinel="$dir/.auto-pilot/$DONE_SENTINEL_NAME"

  # Front matter is the block between the first two `---` lines. No YAML tool
  # required — plain awk/sed line matching.
  local front; front="$(awk '/^---$/{c++; next} c==1{print}' "$run_md")"

  local run_status until_val orch_pid orch_started
  run_status="$(_front_field status)"; [ -n "$run_status" ] || run_status="unknown"
  until_val="$(_front_field until)"
  orch_pid="$(_front_field orchestrator_pid)"
  orch_started="$(_front_field orchestrator_started_at)"

  # Per-task phase table: every `| ... |` line after the front matter.
  local table; table="$(awk '/^\|/{print}' "$run_md")"
  local task_rows task_count
  task_rows="$(printf '%s\n' "$table" | grep -Ev '^\| *:?-+:? *(\| *:?-+:? *)*\|?$' || true)"
  if [ -n "$task_rows" ]; then
    task_count=$(( $(printf '%s\n' "$task_rows" | wc -l | tr -d ' ') - 1 )) # minus header row
    [ "$task_count" -ge 0 ] || task_count=0
  else
    task_count=0
  fi

  # Last meaningful event from the stream-json orchestrator.log: the last line
  # that looks like an assistant or tool event, else just the last line.
  # Degrades gracefully if the log is absent/empty.
  local last_event=""
  if [ -s "$log" ]; then
    last_event="$(grep -E '"type":"(assistant|tool_use|tool_result)"' "$log" 2>/dev/null | tail -1)"
    [ -n "$last_event" ] || last_event="$(tail -1 "$log")"
    last_event="$(printf '%s' "$last_event" | cut -c1-240)"
  fi

  # PID liveness: live only if the PID is alive AND its process start-time
  # matches the recorded one (guards a recycled PID) — dead / mismatch / none.
  local pid_state="none"
  if [ -n "$orch_pid" ]; then
    if kill -0 "$orch_pid" 2>/dev/null; then
      local actual_started; actual_started="$(ps -o lstart= -p "$orch_pid" 2>/dev/null)"
      if [ -n "$actual_started" ] && [ "$(_norm_ws "$actual_started")" = "$(_norm_ws "$orch_started")" ]; then
        pid_state="live"
      else
        pid_state="mismatch"
      fi
    else
      pid_state="dead"
    fi
  fi

  # Done-sentinel (written by `teardown --done-sentinel`) is the single source
  # of "run is done" — it can mark the run done even before RUN.md's own
  # `status:` field is committed as `done`.
  local sentinel_done="no" run_status_display="$run_status"
  [ -f "$sentinel" ] && { sentinel_done="yes"; run_status_display="done"; }

  echo "run: $label"
  echo "dir: $dir"
  echo "status: $run_status_display (front-matter: $run_status; done-sentinel: $sentinel_done)"
  echo
  echo "tasks:"
  if [ -n "$table" ]; then
    printf '%s\n' "$table"
  else
    echo "  (no task table found in RUN.md)"
  fi
  echo
  echo "log: ${last_event:-(orchestrator.log absent, empty, or no assistant/tool events found)}"
  echo "pid: $pid_state (pid=${orch_pid:-none} started=\"${orch_started:-none}\")"
  echo "until: ${until_val:-(none)}"
  echo "STATUS: $run_status_display pid=$pid_state tasks=$task_count until=${until_val:-none}"
}

# ---------------------------------------------------------------------------
# Task 13 — HEAD guard: the run worktree's HEAD must stay on the run-state
# branch `auto-pilot/<run_id>` for the entire run (skills/auto-pilot/references/
# run-state.md "Run worktree HEAD invariant"). Task code belongs in a SEPARATE
# worker worktree (owned/torn-down by /deliver-task); an orchestrator that
# instead `git checkout`s a task branch INSIDE the run worktree wedges --resume,
# which reads .auto-pilot/RUN.md off this exact worktree. This guard runs at
# every run-loop iteration and at the top of --resume.
# ---------------------------------------------------------------------------

# Assert HEAD in the run worktree is `auto-pilot/<run-id>`; if not, restore it
# and (with --questions) record the deviation as a QUESTIONS.md entry — which
# then reaches REPORT.md via the existing rolling rewrite (no new REPORT.md
# section invented; run-state.md owns that format). A CLEAN deviation is
# restored and the run proceeds, per the task's "restore before proceeding"
# directive; a deviation with a dirty run worktree is fail-closed (restoring
# would carry or lose the uncommitted edits), as is a restore git itself refuses.
assert_run_head() {
  local dir="" run_id="" questions=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --dir) [ $# -ge 2 ] || die "missing value for --dir"; dir="$2"; shift 2 ;;
      --run-id) [ $# -ge 2 ] || die "missing value for --run-id"; run_id="$2"; shift 2 ;;
      --questions) [ $# -ge 2 ] || die "missing value for --questions"; questions="$2"; shift 2 ;;
      *) die "unknown assert-run-head argument: $1" ;;
    esac
  done
  [ -n "$dir" ] && [ -n "$run_id" ] || die "assert-run-head requires --dir and --run-id"
  [ -e "$dir/.git" ] || die "assert-run-head: not a git worktree (fail-closed): $dir"
  local expected="auto-pilot/$run_id"
  local actual
  actual="$(git -C "$dir" rev-parse --abbrev-ref HEAD 2>/dev/null)" \
    || die "assert-run-head: cannot read HEAD in $dir"
  if [ "$actual" = "$expected" ]; then
    echo "spawn-orchestrator: HEAD OK ($expected)"
    return 0
  fi
  # A deviation with a DIRTY run worktree is the wedge case: a non-conflicting
  # `git checkout` would silently carry those uncommitted (task-branch) edits onto
  # the run-state branch, and a conflicting one blocks the restore. Either way,
  # fail closed rather than restore — the guard only repairs a CLEAN deviation.
  if [ -n "$(git -C "$dir" status --porcelain 2>/dev/null)" ]; then
    die "assert-run-head: HEAD is on '$actual', not '$expected', and the run worktree has uncommitted changes (fail-closed) — restoring would carry or lose them; resolve by hand"
  fi
  # A detached HEAD reads back as the literal "HEAD"; record its SHA so the
  # parked ref (and any commit made there) stays findable after the restore.
  case "$actual" in HEAD) actual="detached@$(git -C "$dir" rev-parse --short HEAD 2>/dev/null)" ;; esac
  local giterr
  giterr="$(git -C "$dir" checkout "$expected" 2>&1 >/dev/null)" \
    || die "assert-run-head: HEAD is on '$actual', not '$expected', and could not be restored (fail-closed): ${giterr:-git checkout failed}"
  if [ -n "$questions" ]; then
    # Resolve a relative --questions against the run worktree (--dir), not the
    # caller's cwd — the documented invocation passes `.auto-pilot/QUESTIONS.md`.
    case "$questions" in /*) ;; *) questions="$dir/$questions" ;; esac
    # Number from the MAX existing index, not the count — QUESTIONS.md is not
    # guaranteed contiguously numbered from Q1 (other writers append too).
    local n; n="$(grep -oE '^## Q[0-9]+' "$questions" 2>/dev/null | grep -oE '[0-9]+' | sort -n | tail -1)"
    [ -n "$n" ] || n=0
    local qn=$((n + 1))
    {
      [ -s "$questions" ] && printf '\n'
      printf '## Q%s — RUN — run worktree HEAD was parked on `%s`\n\n' "$qn" "$actual"
      printf -- '- **Options:** restore HEAD to `%s` and continue | halt the run for a human\n' "$expected"
      printf -- '- **Call:** restored HEAD to `%s` and continued\n' "$expected"
      printf -- '- **Why:** the run worktree'\''s HEAD staying on the run-state branch is load-bearing for --resume (run-state.md "Run worktree HEAD invariant"); the run worktree was clean, so the deviation is fully repairable and halting an otherwise-recoverable run would be worse\n'
      printf -- '- **Reversible:** yes — the run worktree was clean, so HEAD is only restored and nothing is lost\n'
    } >>"$questions" \
      || die "assert-run-head: restored HEAD to '$expected' but could not write the QUESTIONS.md entry (fail-closed): $questions"
  fi
  echo "spawn-orchestrator: HEAD DEVIATION restored (was $actual, now $expected)"
}

check_profile() {
  [ $# -eq 1 ] || die "check-profile takes exactly one <file>"
  local f="$1"
  [ -f "$f" ] || die "profile not found: $f"
  command -v sandbox-exec >/dev/null 2>&1 || die "sandbox-exec not available (macOS only)"
  # Compile the profile by loading it. We test COMPILATION, not permission: a
  # locked-down profile legitimately denies exec of an unlisted probe binary, so
  # reaching execvp() means the profile parsed and loaded fine. Only a genuine
  # parse/compile error (which never reaches execvp) is a failure.
  local errout status
  errout="$(sandbox-exec -f "$f" /usr/bin/true 2>&1)"; status=$?
  if [ "$status" -eq 0 ] || printf '%s' "$errout" | grep -q 'execvp('; then
    echo "spawn-orchestrator: profile OK $f"
    return 0
  fi
  [ -n "$errout" ] && echo "$errout" >&2
  die "profile failed to compile: $f"
}

[ $# -ge 1 ] || die "usage: spawn-orchestrator.sh <render-profile|check-profile> …"
sub="$1"; shift
case "$sub" in
  render-profile) render_profile "$@" ;;
  render-settings) render_settings "$@" ;;
  write-launch) write_launch "$@" ;;
  record-handle) record_handle "$@" ;;
  smoke-test) smoke_test "$@" ;;
  detach) detach "$@" ;;
  teardown) teardown "$@" ;;
  launch) launch "$@" ;;
  write-verify-broker) write_verify_broker "$@" ;;
  verify-request) verify_request "$@" ;;
  verify-broker) verify_broker "$@" ;;
  verify-await) verify_await "$@" ;;
  check-profile) check_profile "$@" ;;
  status) status "$@" ;;
  assert-run-head) assert_run_head "$@" ;;
  classify-exit) classify_exit "$@" ;;
  supervisor-check) supervisor_check "$@" ;;
  restack) restack "$@" ;;
  -h|--help) sed -n '2,/^[^#]/{/^#/p;}' "$0"; exit 0 ;;
  *) die "unknown subcommand: $sub" ;;
esac
