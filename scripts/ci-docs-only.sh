#!/usr/bin/env bash
# ci-docs-only.sh — is the diff <base>..<head> confined to dev_docs/, or at
# least out of reach of the shell suites?
#
# Usage: scripts/ci-docs-only.sh <base> <head>
#
# Prints exactly two lines in the form GitHub Actions reads from
# $GITHUB_OUTPUT:
#
#   docs_only=true|false    every changed file is under dev_docs/. check.sh
#                           --base reads this to skip the slow suites.
#   shell_free=true|false   every changed file is somewhere scripts/test-shell.sh
#                           cannot reach: dev_docs/, or the task-dashboard's
#                           Python. ci.yml uses it to skip the macOS job, whose
#                           runtime is all test-shell.sh. docs_only implies it.
#
# The shell_free list is an allowlist, not "no shell file changed": the bats
# and orchestrator suites drive scripts that shell out to Python helpers, so a
# .py change elsewhere in scripts/ can regress them. A path joins the list only
# once nothing under test/, scripts/lib/, scripts/test-shell.sh or
# scripts/test-spawn-orchestrator*.sh references it — re-run that search before
# adding one.
#
# Both fail toward running: `true` needs positive evidence, and everything
# else is `false` — a git error, an unknown ref (a shallow clone that cannot
# see the base), or an EMPTY diff. That last one matters most: "every changed
# file is under dev_docs/" is vacuously true of zero files, and a classifier
# that let vacuous truth through would skip CI precisely when it could not see
# the diff.
#
# A path on either list still counts as code when it is a shell or bats file:
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
  printf 'docs_only=false\nshell_free=false\n'
  exit 0
fi

if [ -z "$files" ]; then
  echo "ci-docs-only: empty diff; treating the change as code" >&2
  printf 'docs_only=false\nshell_free=false\n'
  exit 0
fi

docs_only=true
shell_free=true
while IFS= read -r file; do
  case "$file" in
    *.sh | *.bash | *.bats)
      echo "ci-docs-only: $file is a shell file" >&2
      docs_only=false
      shell_free=false
      break
      ;;
    dev_docs/*) ;;
    scripts/task-dashboard/* | scripts/test_task_dashboard*.py)
      if [ "$docs_only" = true ]; then
        echo "ci-docs-only: $file is outside dev_docs/" >&2
      fi
      docs_only=false
      ;;
    *)
      echo "ci-docs-only: $file is outside dev_docs/ and within reach of the shell suites" >&2
      docs_only=false
      shell_free=false
      break
      ;;
  esac
done <<EOF
$files
EOF

printf 'docs_only=%s\nshell_free=%s\n' "$docs_only" "$shell_free"
