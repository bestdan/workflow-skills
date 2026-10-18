#!/usr/bin/env bash
# ci-docs-only.sh — is the diff <base>..<head> confined to dev_docs/?
#
# Usage: scripts/ci-docs-only.sh <base> <head>
#
# Prints exactly one line, `docs_only=true` or `docs_only=false`, in the form
# GitHub Actions reads from $GITHUB_OUTPUT. ci.yml uses it to skip the macOS
# job on a PR that cannot reach anything that job tests.
#
# It fails toward running: `true` needs positive evidence, and everything else
# is `false` — a git error, an unknown ref (a shallow clone that cannot see the
# base), or an EMPTY diff. That last one matters most: "every changed file is
# under dev_docs/" is vacuously true of zero files, and a classifier that let
# vacuous truth through would skip CI precisely when it could not see the diff.
#
# A dev_docs/ path still counts as code when it is a shell or bats file:
# scripts/lint-shell.sh lints every tracked *.sh/*.bash/*.bats wherever it
# lives, so a dev_docs PR adding one must not lose that coverage.
#
# --no-renames because rename detection reports only the NEW path, so moving
# scripts/foo.sh to dev_docs/foo.md would otherwise read as dev_docs-only.
set -uo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <base> <head>" >&2
  exit 2
fi

if ! files="$(git diff --no-renames --name-only "$1" "$2" --)"; then
  echo "ci-docs-only: git diff failed; treating the change as code" >&2
  echo "docs_only=false"
  exit 0
fi

if [ -z "$files" ]; then
  echo "ci-docs-only: empty diff; treating the change as code" >&2
  echo "docs_only=false"
  exit 0
fi

while IFS= read -r file; do
  case "$file" in
    *.sh | *.bash | *.bats) ;;
    dev_docs/*) continue ;;
  esac
  echo "ci-docs-only: $file is outside dev_docs/ or is a shell file" >&2
  echo "docs_only=false"
  exit 0
done <<EOF
$files
EOF

echo "docs_only=true"
