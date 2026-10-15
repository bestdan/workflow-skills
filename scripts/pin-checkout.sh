#!/usr/bin/env bash
# Pin this repo's main checkout to one branch, and install the post-checkout
# hook that puts it back when something checks out another branch there.
#
#   scripts/pin-checkout.sh <branch>
#
#   exit 0   pinned, and the hook is installed (or already was)
#   exit 3   pinned, but core.hooksPath is set: no hook installed, and the
#            message says where one belongs. A hooks dir that already honours
#            the pin needs nothing more.
#   exit 4   refused: a different post-checkout hook already exists. Nothing
#            is written — not even the key — so the re-run after merging the
#            hooks by hand is a clean one, and the message gives the key alone.
#   exit 1   a write failed (the sandbox, most likely)
#   exit 2   not run from a non-bare repo
#   exit 64  bad or missing branch name
#
# Run it OUTSIDE the Claude Code sandbox, which denies writes to .git/config
# and .git/hooks. From a linked worktree it still pins the main checkout: the
# key goes in the shared config and the hook in the common git dir.
#
# The pin itself is what scripts/guard-pinned-branch.py reads; the hook
# (scripts/lib/pinned-checkout-hook.sh) is the backstop for checkouts that
# guard never sees.

set -uo pipefail

err() { printf '%s\n' "$1" >&2; }

branch="${1-}"
if [ -z "$branch" ] || [ $# -ne 1 ]; then
  err "usage: pin-checkout.sh <branch>"
  exit 64
fi
# Keep the name it prints: it expands `@{-1}`, and a literal shorthand stored as
# the pin never equals the current branch, so the hook would restore forever.
if ! resolved="$(git check-ref-format --branch "$branch" 2>/dev/null)"; then
  err "pin-checkout: '$branch' is not a valid branch name"
  exit 64
fi
branch="$resolved"
if [ "$(git rev-parse --is-inside-work-tree 2>/dev/null)" != true ]; then
  err "pin-checkout: run this inside a repo's working tree (bare repos have no checkout to pin)"
  exit 2
fi

template="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/pinned-checkout-hook.sh"
common_dir="$(git rev-parse --path-format=absolute --git-common-dir)" || exit 2
# Set-ness comes from the exit status, not the value: an empty hooksPath still
# stops git reading .git/hooks.
hooks_set=0
git config --get core.hooksPath >/dev/null && hooks_set=1
hook="$common_dir/hooks/post-checkout"

# Decide before writing anything, so a refusal leaves the repo untouched.
if [ "$hooks_set" = 0 ] && [ -e "$hook" ] && ! cmp -s "$template" "$hook"; then
  err "pin-checkout: $hook already exists; refusing to overwrite it."
  err "  Merge the pin check from $template into it, then set the pin alone:"
  err "    git config --local hooks.pinnedBranch $branch"
  exit 4
fi

git config --local hooks.pinnedBranch "$branch" || exit 1
echo "Pinned the main checkout to '$branch' (hooks.pinnedBranch)."

if [ "$hooks_set" = 1 ]; then
  # --git-path resolves hooksPath the way git does when it runs a hook.
  dir="$(git rev-parse --path-format=absolute --git-path hooks)"
  err "core.hooksPath is set, so git never runs .git/hooks; installed no hook."
  err "  The backstop belongs in $dir/post-checkout, and it must switch back to"
  err "  hooks.pinnedBranch — $template is one that does."
  exit 3
fi

if [ -e "$hook" ]; then
  echo "post-checkout hook already installed: $hook"
else
  mkdir -p "$common_dir/hooks" || exit 1
  tmp="$hook.pin-checkout.$$"
  if ! { cp "$template" "$tmp" && chmod +x "$tmp" && mv "$tmp" "$hook"; }; then
    rm -f "$tmp"
    err "pin-checkout: could not install $hook (inside the Claude Code sandbox? run it outside)"
    exit 1
  fi
  echo "Installed post-checkout hook: $hook"
fi

current="$(git symbolic-ref -q HEAD)"
current="${current#refs/heads/}"
if [ "$(git rev-parse --path-format=absolute --git-dir)" = "$common_dir" ] && [ "$current" != "$branch" ]; then
  echo "Note: this checkout is on '${current:-a detached HEAD}', not '$branch'; run: git checkout $branch"
fi
exit 0
