#!/usr/bin/env bash
# test-preflight-scout.sh — fixture-based tests for
# scripts/preflight.sh --scout-run-md (the per-task capability-join scout).
#
# Builds a fixture RUN.md and a fixture PATH dir under a temp dir (mktemp -d)
# so nothing here touches the real repo or the real PATH, and asserts three
# cases:
#   - all declared backends present on PATH: SCOUT VERDICT: go, exit 0
#   - one backend missing: BLOCKS LAUNCH names the task + backend, exit 1
#   - a `cao`-routed task with the CAO port closed: BLOCKS LAUNCH names the
#     task, exit 1
#
# Run directly: bash scripts/test-preflight-scout.sh
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$ROOT/scripts/preflight.sh"

BASE="$(mktemp -d "${TMPDIR:-/tmp}/test-preflight-scout.XXXXXX")"
[ -n "$BASE" ] && [ -d "$BASE" ] || {
  echo "test-preflight-scout: could not create a temp dir" >&2
  exit 2
}
trap 'rm -rf "$BASE"' EXIT

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

assert_exit() {
  # assert_exit <description> <expected-rc> <actual-rc>
  if [ "$2" = "$3" ]; then
    ok "$1"
  else
    bad "$1 (expected exit $2, got $3)"
  fi
}

# --- Fixture PATH: fake `codex`, `agy`, `cao`, `cao-run`, `cao-server` ------
# A bare "$FIXBIN:$PATH" would leak whatever coder CLIs happen to be
# installed on the host (e.g. `devin` under ~/.local/bin) into the "missing"
# case, so the fixture PATH is FIXBIN plus only the standard system dirs the
# script's own `awk`/`sed`/`grep` calls need — never the caller's full PATH.
FIXBIN="$BASE/bin"
FIXTURE_PATH="$FIXBIN:/usr/bin:/bin:/usr/sbin:/sbin"
mkdir -p "$FIXBIN"
for b in codex agy cao cao-run cao-server; do
  printf '#!/bin/sh\nexit 0\n' >"$FIXBIN/$b"
  chmod +x "$FIXBIN/$b"
done
printf '#!/bin/sh\nexit 0\n' >"$FIXBIN/fake-nc-open"
chmod +x "$FIXBIN/fake-nc-open"
printf '#!/bin/sh\nexit 1\n' >"$FIXBIN/fake-nc-closed"
chmod +x "$FIXBIN/fake-nc-closed"

# --- Case 1: all present --------------------------------------------------
cat >"$BASE/all-present.md" <<'RUNMD'
---
run_id: test-run
work_source: plan:foo
base_branch: main
run_profile: less-claude
cao_coder_mapping: {codex: cao-codex, agy: cao-agy}
status: active
---

| task | phase | branch | base | base_sha | pr | notes | coder |
| ---- | ----- | ------ | ---- | -------- | -- | ----- | ----- |
| T-1  | pending | - | main | - | - | - | opus |
| T-2  | pending | - | main | - | - | - | codex |
| T-3  | pending | - | main | - | - | - | cao |
RUNMD

out1="$(PATH="$FIXTURE_PATH" PREFLIGHT_NC="$FIXBIN/fake-nc-open" "$SCRIPT" --scout-run-md "$BASE/all-present.md")"
rc1=$?
assert_exit "all present: exits 0" 0 "$rc1"
if grep -q '^SCOUT VERDICT: go$' <<<"$out1"; then
  ok "all present: prints SCOUT VERDICT: go"
else
  bad "all present: missing SCOUT VERDICT: go (got: $out1)"
fi

# --- Case 2: one backend missing ------------------------------------------
cat >"$BASE/one-missing.md" <<'RUNMD'
---
run_id: test-run
work_source: plan:foo
base_branch: main
run_profile: default
cao_coder_mapping: {}
status: active
---

| task | phase | branch | base | base_sha | pr | notes | coder |
| ---- | ----- | ------ | ---- | -------- | -- | ----- | ----- |
| T-1  | pending | - | main | - | - | - | opus |
| T-2  | pending | - | main | - | - | - | devin |
RUNMD

out2="$(PATH="$FIXTURE_PATH" "$SCRIPT" --scout-run-md "$BASE/one-missing.md")"
rc2=$?
assert_exit "one backend missing: exits 1" 1 "$rc2"
if grep -q '^BLOCKS LAUNCH: T-2 -> devin (missing)$' <<<"$out2"; then
  ok "one backend missing: names the task and backend"
else
  bad "one backend missing: wrong output: $out2"
fi

# --- Case 3: CAO port closed ------------------------------------------------
cat >"$BASE/cao-closed.md" <<'RUNMD'
---
run_id: test-run
work_source: plan:foo
base_branch: main
run_profile: less-claude
cao_coder_mapping: {codex: cao-codex, agy: cao-agy}
status: active
---

| task | phase | branch | base | base_sha | pr | notes | coder |
| ---- | ----- | ------ | ---- | -------- | -- | ----- | ----- |
| T-1  | pending | - | main | - | - | - | opus |
| T-2  | pending | - | main | - | - | - | cao |
RUNMD

out3="$(PATH="$FIXTURE_PATH" PREFLIGHT_NC="$FIXBIN/fake-nc-closed" "$SCRIPT" --scout-run-md "$BASE/cao-closed.md")"
rc3=$?
assert_exit "CAO port closed: exits 1" 1 "$rc3"
if grep -q '^BLOCKS LAUNCH: T-2 -> cao (missing)$' <<<"$out3"; then
  ok "CAO port closed: names the cao-routed task"
else
  bad "CAO port closed: wrong output: $out3"
fi

# --- Case 4: --source and --scout-run-md are mutually exclusive -----------
"$SCRIPT" --source plan --scout-run-md "$BASE/all-present.md" >/dev/null 2>"$BASE/mutex.stderr"
rc4=$?
assert_exit "mutually exclusive flags: exits 2" 2 "$rc4"
if grep -q 'mutually exclusive' "$BASE/mutex.stderr"; then
  ok "mutually exclusive flags: stderr names the conflict"
else
  bad "mutually exclusive flags: stderr did not name it: $(cat "$BASE/mutex.stderr")"
fi

echo
echo "test-preflight-scout: $pass_count passed, $fail_count failed"
[ "$fail" -eq 0 ] || exit 1
echo "test-preflight-scout: OK"
