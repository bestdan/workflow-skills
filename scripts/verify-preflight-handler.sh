#!/usr/bin/env bash
# verify-preflight-handler.sh — real-machine check that preflight.sh reports a
# run's OWN task handler when the run root is a linked worktree.
#
# This is the shape the launch actually uses: the task config lives in a
# repo's main checkout (git-excluded for every handler except repo-pr, so a
# linked worktree never receives it), the launch creates a linked worktree and
# passes it as --run-root, and preflight.sh must still report that repo's
# handler with HANDLER_SOURCE pointing at the main checkout's file.
# scripts/test-preflight-handler.sh proves this against fakes; this script
# proves it against a real repo, real git, and the real gh/network probes, so
# it is for a human on a real machine, not the gate.
#
# What it does:
#   1. reads the expected handler from <repo>'s merged task config
#      (dev_docs/tasks/.task-config.yml overlaid with .task-config.local.yml);
#   2. adds a temporary detached linked worktree of <repo> under $TMPDIR;
#   3. runs the chosen preflight.sh with --run-root <that worktree>;
#   4. asserts the worktree carries no config of its own, and that HANDLER,
#      DEST_HOST and HANDLER_SOURCE all come from the main checkout;
#   5. removes the worktree again, whatever happened.
#
# Usage:
#   scripts/verify-preflight-handler.sh --repo <main checkout> [--preflight <path>] [--base <branch>]
#
#   --repo       A main checkout whose dev_docs/tasks/ holds a task config. Required.
#   --preflight  The preflight.sh to exercise. Default: the one beside this
#                script. Pass the installed plugin's copy to verify a release:
#                ~/.claude/plugins/cache/workflow-skills/workflow-skills/<ver>/scripts/preflight.sh
#   --base       The base branch preflight.sh checks freshness against. Default: main.
#
# preflight.sh's own verdict (auth, freshness, confinement, consent) is printed
# but does not decide this script's exit code — only the handler assertions do.
# Exit 0 when every assertion passes, 1 when any fails, 2 on a usage error.
#
# Needs network (gh, git ls-remote), so run it outside any sandbox.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo=""
preflight="$HERE/preflight.sh"
base="main"

usage() {
  sed -n '2,/^set -uo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//' >&2
  exit 2
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo)
      [ $# -ge 2 ] || usage
      repo="$2"
      shift 2
      ;;
    --preflight)
      [ $# -ge 2 ] || usage
      preflight="$2"
      shift 2
      ;;
    --base)
      [ $# -ge 2 ] || usage
      base="$2"
      shift 2
      ;;
    -h | --help) usage ;;
    *)
      echo "verify-preflight-handler: unknown argument: $1" >&2
      usage
      ;;
  esac
done

[ -n "$repo" ] || {
  echo "verify-preflight-handler: --repo is required" >&2
  usage
}
[ -f "$preflight" ] || {
  echo "verify-preflight-handler: preflight.sh not found: $preflight" >&2
  exit 2
}
repo="$(git -C "$repo" rev-parse --show-toplevel 2>/dev/null || true)"
[ -n "$repo" ] || {
  echo "verify-preflight-handler: --repo is not a git checkout" >&2
  exit 2
}
cfg="$repo/dev_docs/tasks/.task-config.yml"
cfg_local="$repo/dev_docs/tasks/.task-config.local.yml"
[ -f "$cfg" ] || [ -f "$cfg_local" ] || {
  echo "verify-preflight-handler: $repo carries no dev_docs/tasks/.task-config*.yml — nothing to verify" >&2
  exit 2
}

# The expected values, derived the same way preflight.sh derives them, from
# the main checkout alone: committed file first, local override wins.
expected_handler="repo-pr"
expected_source=""
for f in "$cfg" "$cfg_local"; do
  [ -f "$f" ] || continue
  h="$(sed -n 's/^handler:[[:space:]]*//p' "$f" | head -1 | tr -d '[:space:]')"
  if [ -n "$h" ]; then
    expected_handler="$h"
    expected_source="$f"
  fi
done
case "$expected_handler" in
  linear) expected_host="api.linear.app" ;;
  jira)
    site="$(sed -n 's/^[[:space:]]*site:[[:space:]]*//p' "$cfg" "$cfg_local" 2>/dev/null | tail -1 | tr -d '[:space:]')"
    expected_host="${site:-github.com}"
    ;;
  *) expected_host="github.com" ;;
esac

fail=0
ok() { echo "  ✔ $1"; }
bad() {
  fail=1
  echo "  ✘ $1" >&2
}
assert_contains() { # <description> <needle> <haystack>
  case "$3" in
    *"$2"*) ok "$1" ;;
    *) bad "$1 (missing: $2)" ;;
  esac
}

echo "verify-preflight-handler"
echo "  repo:      $repo"
echo "  preflight: $preflight"
echo "  expecting: HANDLER=$expected_handler DEST_HOST=$expected_host"
echo "             HANDLER_SOURCE=${expected_source:-<none: repo-pr default>}"
tracked="$(git -C "$repo" ls-files dev_docs/tasks/.task-config.yml)"
if [ -n "$tracked" ]; then
  echo "  note:      .task-config.yml is TRACKED in this repo, so the linked worktree"
  echo "             will carry its own copy — this exercises the run-root path, not"
  echo "             the main-checkout fallback. Pick a repo with an excluded config"
  echo "             (any external handler set up by /task-config) to test the fallback."
fi

# A detached worktree: no branch is created, so nothing is left behind but the
# directory, and `worktree remove` takes it out cleanly on exit.
wt="$(mktemp -d "${TMPDIR:-/tmp}/verify-preflight-handler.XXXXXX")"
rmdir "$wt"
cleanup() {
  git -C "$repo" worktree remove --force "$wt" >/dev/null 2>&1 || rm -rf "$wt"
  git -C "$repo" worktree prune >/dev/null 2>&1 || true
}
trap cleanup EXIT
git -C "$repo" worktree add --detach -q "$wt" >/dev/null 2>&1 || {
  echo "verify-preflight-handler: could not add a linked worktree of $repo" >&2
  exit 2
}
wt="$(cd "$wt" && pwd -P)"
echo "  run root:  $wt (linked worktree)"
echo

if [ -z "$tracked" ]; then
  if [ -e "$wt/dev_docs/tasks/.task-config.yml" ]; then
    bad "linked worktree carries no config of its own"
  else
    ok "linked worktree carries no config of its own (production shape)"
  fi
fi

out="$(bash "$preflight" --source plan --base "$base" --run-root "$wt" 2>&1)"
rc=$?
echo "$out" | grep -E '^PREFLIGHT (RUN_ROOT|HANDLER|HANDLER_SOURCE|DEST_HOST|VERDICT):' | sed 's/^/  /'
echo "  (preflight.sh exited $rc — its verdict is informational here)"
echo

assert_contains "HANDLER is the repo's own" "PREFLIGHT HANDLER: $expected_handler" "$out"
assert_contains "DEST_HOST follows it" "PREFLIGHT DEST_HOST: $expected_host" "$out"
if [ -n "$expected_source" ]; then
  if [ -n "$tracked" ]; then
    assert_contains "HANDLER_SOURCE names the run root's own copy" \
      "PREFLIGHT HANDLER_SOURCE: $wt/dev_docs/tasks/$(basename "$expected_source")" "$out"
  else
    assert_contains "HANDLER_SOURCE names the MAIN checkout's file" \
      "PREFLIGHT HANDLER_SOURCE: $expected_source" "$out"
  fi
else
  assert_contains "HANDLER_SOURCE reports the default" "PREFLIGHT HANDLER_SOURCE: default" "$out"
fi
case "$out" in
  *"PREFLIGHT HANDLER_SOURCE: $(dirname "$preflight")"*) bad "HANDLER_SOURCE must never point into the plugin dir" ;;
  *) ok "HANDLER_SOURCE does not point into the plugin dir" ;;
esac
if ! grep -q '^PREFLIGHT HANDLER_SOURCE:' <<<"$out"; then
  bad "this preflight.sh emits no HANDLER_SOURCE line — it predates the run-root fix"
fi

echo
if [ "$fail" = 0 ]; then
  echo "verify-preflight-handler: PASS"
else
  echo "verify-preflight-handler: FAIL"
fi
exit "$fail"
