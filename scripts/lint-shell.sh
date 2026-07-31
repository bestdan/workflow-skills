#!/usr/bin/env bash
# Repository-wide deterministic syntax, formatting, and shell lint checks.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

for tool in shfmt shellcheck; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "lint-shell: $tool is required" >&2
    exit 2
  fi
done

[ -x test/vendor/bats-core/bin/bats ] || {
  echo "lint-shell: bats submodules missing — run: git submodule update --init --recursive" >&2
  exit 2
}

# dev_docs/elite-spike/fixtures/ is excluded below for the same reason
# dprint.json excludes it: it is checked-in spike evidence that must stay
# byte-for-byte as it ran, and results.json records sha256 provenance for files
# in that tree. A formatter rewriting them would silently invalidate the record.
shell_files=()
bats_files=()
while IFS= read -r file; do
  case "$file" in
    *.sh | *.bash) shell_files+=("$file") ;;
    *.bats) bats_files+=("$file") ;;
  esac
done < <(git ls-files --cached --others --exclude-standard '*.sh' '*.bash' '*.bats' ':(exclude)dev_docs/elite-spike/fixtures/**' | while IFS= read -r file; do
  [ -f "$file" ] && printf '%s\n' "$file"
done)

fail=0
run() {
  echo "→ $*"
  "$@" || fail=1
}

if [ "${#shell_files[@]}" -gt 0 ]; then
  run bash -n "${shell_files[@]}"
  run shfmt -i 2 -ci -bn -d "${shell_files[@]}"
  # SC1090/SC1091: runtime-resolved includes are validated by syntax checks and
  # the Bats suite; ShellCheck cannot follow paths assembled from repo roots.
  # Warning-and-error findings are blocking. Lower-severity style suggestions
  # remain available to contributors without forcing behavior-risking rewrites.
  run shellcheck -s bash --severity=warning -e SC1090,SC1091 "${shell_files[@]}"
fi
if [ "${#bats_files[@]}" -gt 0 ]; then
  run shfmt -ln bats -i 2 -ci -bn -d "${bats_files[@]}"
  run test/vendor/bats-core/bin/bats --count "${bats_files[@]}"
fi

[ "$fail" -eq 0 ] || exit 1
echo "lint-shell: OK"
