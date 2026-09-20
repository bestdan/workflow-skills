#!/usr/bin/env bash
# test-preflight-handler.sh — fixture-based tests for scripts/preflight.sh's
# task-handler resolution (section 3: `PREFLIGHT HANDLER` / `DEST_HOST`).
#
# The property under test: the handler is read from the RUN ROOT's task config,
# never from the plugin directory the script lives in. This repo ships its own
# tracked dev_docs/tasks/.task-config.yml in every release, so a plugin-dir
# lookup reported this repository's handler to every installed user (#780).
#
# The fixture therefore keeps the two roots apart on purpose: preflight.sh is
# copied into a PLUGIN root that ships a `gh-issue` config, and each case passes
# a separate RUN root — one with a `linear` config, one with a `jira` config
# plus a local override, one with no config at all. Fake `gh`, `claude`,
# `probe-coders.sh`, `preflight-freshness.sh` and `spawn-orchestrator.sh` keep
# every case off the network and the real repo, as test-preflight-consent.sh
# does; the consent probe is pointed at a launchctl that does not exist so it
# degrades to its logged skip everywhere.
#
# Run directly: bash scripts/test-preflight-handler.sh
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BASE="$(mktemp -d "${TMPDIR:-/tmp}/test-preflight-handler.XXXXXX")"
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-preflight-handler: could not create a temp dir" >&2
  exit 2
}
trap 'rm -rf "$BASE"' EXIT
# Physical path: `git rev-parse --show-toplevel` reports /private/var rather
# than /var under $TMPDIR, and HANDLER_SOURCE echoes the resolved run root.
BASE="$(cd "$BASE" && pwd -P)"

fail=0
pass_count=0
fail_count=0

ok() {
  pass_count=$((pass_count + 1))
  echo "  ✔ $1"
}

bad() {
  fail_count=$((fail_count + 1))
  fail=1
  echo "  ✘ $1" >&2
}

assert_contains() {
  # assert_contains <description> <needle> <haystack>
  case "$3" in
    *"$2"*) ok "$1" ;;
    *) bad "$1 (missing: $2)" ;;
  esac
}

assert_not_contains() {
  # assert_not_contains <description> <needle> <haystack>
  case "$3" in
    *"$2"*) bad "$1 (found: $2)" ;;
    *) ok "$1" ;;
  esac
}

# --- Plugin root: where preflight.sh lives, shipping its OWN config ---------
PLUGIN="$BASE/plugin"
mkdir -p "$PLUGIN/scripts" "$PLUGIN/dev_docs/tasks"
cp "$REPO/scripts/preflight.sh" "$PLUGIN/scripts/preflight.sh"
SCRIPT="$PLUGIN/scripts/preflight.sh"
printf 'handler: gh-issue\ngh-issue:\n  repo: example/plugin-repo\n' \
  >"$PLUGIN/dev_docs/tasks/.task-config.yml"

cat >"$PLUGIN/scripts/preflight-freshness.sh" <<'FRESH'
#!/bin/bash
echo "FRESHNESS: fresh refs=main"
exit 0
FRESH

cat >"$PLUGIN/scripts/probe-coders.sh" <<'CODERS'
#!/bin/bash
echo "coders:"
echo "  codex:"
echo "    installed: false"
echo "  agy:"
echo "    installed: false"
echo "  devin:"
echo "    installed: false"
exit 0
CODERS

cat >"$PLUGIN/scripts/spawn-orchestrator.sh" <<'SPAWN'
#!/bin/bash
sub="$1"
shift
out=""
while [ $# -gt 0 ]; do
  case "$1" in
    --out)
      out="$2"
      shift 2
      ;;
    *) shift ;;
  esac
done
[ -n "$out" ] || exit 2
case "$sub" in
  render-profile)
    printf '(version 1)\n(allow default)\n(deny file-write* (subpath "%s"))\n' "$HOME" >"$out"
    ;;
  render-settings)
    printf '{"allowedDomains":[],"enabled":true}\n' >"$out"
    ;;
  *) exit 2 ;;
esac
exit 0
SPAWN
chmod +x "$PLUGIN/scripts"/*.sh

# --- Fixture PATH ----------------------------------------------------------
FIXBIN="$BASE/bin"
FIXTURE_PATH="$FIXBIN:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$FIXBIN"
printf '#!/bin/sh\nexit 0\n' >"$FIXBIN/claude"
cat >"$FIXBIN/gh" <<'GH'
#!/bin/bash
case "$*" in
  "auth status") exit 0 ;;
  *viewerPermission*) echo "ADMIN" ;;
esac
exit 0
GH
chmod +x "$FIXBIN/claude" "$FIXBIN/gh"

# --- Run roots: real checkouts, each with its own (or no) task config --------
# GIT_CONFIG_GLOBAL is pinned per AGENTS.md so the fixture does not inherit the
# developer's global config.
make_run_root() { # make_run_root <name>
  local d="$BASE/$1"
  mkdir -p "$d/dev_docs/tasks"
  GIT_CONFIG_GLOBAL=/dev/null git -C "$d" init -q
  echo "$d"
}

RUN_LINEAR="$(make_run_root run-linear)"
printf 'handler: linear\nlinear:\n  team: EX\n' >"$RUN_LINEAR/dev_docs/tasks/.task-config.yml"

RUN_JIRA="$(make_run_root run-jira)"
printf 'handler: repo-pr\n' >"$RUN_JIRA/dev_docs/tasks/.task-config.yml"
printf 'handler: jira\njira:\n  site: example.atlassian.net\n' \
  >"$RUN_JIRA/dev_docs/tasks/.task-config.local.yml"

RUN_NONE="$(make_run_root run-none)"

run_preflight() { # run_preflight <run-root>
  PATH="$FIXTURE_PATH" \
    PREFLIGHT_LAUNCHCTL="$BASE/no-such-launchctl" \
    PREFLIGHT_TCC_LOCATIONS="$BASE/elsewhere" \
    bash "$SCRIPT" --source plan --base main --run-root "$1" 2>&1
}

# --- Case 1: a linear run root wins over the plugin's shipped gh-issue -------
out1="$(run_preflight "$RUN_LINEAR")"
assert_contains "linear run root: HANDLER is linear" "PREFLIGHT HANDLER: linear" "$out1"
assert_contains "linear run root: DEST_HOST is api.linear.app" \
  "PREFLIGHT DEST_HOST: api.linear.app" "$out1"
assert_contains "linear run root: HANDLER_SOURCE names the run root's config" \
  "PREFLIGHT HANDLER_SOURCE: $RUN_LINEAR/dev_docs/tasks/.task-config.yml" "$out1"
assert_not_contains "linear run root: the plugin's gh-issue never surfaces" \
  "PREFLIGHT HANDLER: gh-issue" "$out1"

# --- Case 2: the run root's local override wins, and jira reads its site -----
out2="$(run_preflight "$RUN_JIRA")"
assert_contains "jira run root: local override wins" "PREFLIGHT HANDLER: jira" "$out2"
assert_contains "jira run root: DEST_HOST is the configured site" \
  "PREFLIGHT DEST_HOST: example.atlassian.net" "$out2"
assert_contains "jira run root: HANDLER_SOURCE names the local override" \
  "PREFLIGHT HANDLER_SOURCE: $RUN_JIRA/dev_docs/tasks/.task-config.local.yml" "$out2"

# --- Case 3: no config in the run root does NOT inherit the plugin's ---------
out3="$(run_preflight "$RUN_NONE")"
assert_contains "no-config run root: falls to the repo-pr default" \
  "PREFLIGHT HANDLER: repo-pr" "$out3"
assert_not_contains "no-config run root: does not inherit the plugin's gh-issue" \
  "PREFLIGHT HANDLER: gh-issue" "$out3"
assert_contains "no-config run root: says the default was taken, and from where" \
  "PREFLIGHT HANDLER_SOURCE: default (no task config under $RUN_NONE/dev_docs/tasks)" "$out3"
assert_not_contains "no-config run root: HANDLER_SOURCE never points into the plugin dir" \
  "PREFLIGHT HANDLER_SOURCE: $PLUGIN" "$out3"

echo
echo "test-preflight-handler: $pass_count passed, $fail_count failed"
exit "$fail"
