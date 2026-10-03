#!/usr/bin/env python3
"""PreToolUse guard: deny a branch switch in a checkout pinned to one branch.

A repo opts in with ``git config --local hooks.pinnedBranch main``, and from
then on a ``git checkout <branch>`` or ``git switch <branch>`` aimed at that
repo's MAIN worktree is denied before it runs. Every other repo is untouched,
because the guard asks git for the key and gets nothing.

Why a repo wants this: when a machine points at one working tree (a hooks
path, tool pins, symlinks, agent config read from it), swapping that tree's
branch reconfigures the machine, and nothing errors when it happens. Branch
work belongs in a worktree.

WHY A PreToolUse GUARD AND NOT A GIT HOOK. git has no ``pre-checkout`` hook:
``githooks(5)`` documents none that runs before a checkout. ``post-checkout``
runs after the worktree is already rewritten, and ``reference-transaction``,
which can abort a HEAD update from its ``prepared`` phase, does not work
either: git writes the index and working tree BEFORE updating HEAD, so a veto
leaves HEAD on the pin with the other branch's files staged, which is worse
than the checkout it refused. Denying the tool call is the only point in the
sequence that is genuinely before the mutation.

It is deliberately not the only possible defence. This guard reads the Bash
command as *text*, so it is a heuristic and fails open on shapes it cannot
parse, and it sees Claude Code alone: another CLI agent or a human terminal
never reaches it.

NOT YET REGISTERED in hooks/hooks.json, on purpose. This version judges every
git call against the payload's cwd (or a resolved ``git -C <dir>``) and does
not track ``cd`` across segments. Registered as is, it would judge
``cd <worktree> && git checkout -b x`` against the pinned main checkout and
refuse it: a false positive on the exact workflow the pin pushes people
toward. Registration waits for ``cd`` tracking.

Reads the hook payload as JSON on stdin; emits a PreToolUse decision on stdout.
Anything not matched produces no output and falls through to the normal flow.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

# A hook runs as a standalone script, not as part of a package, so the shared
# parser is found by this file's own directory rather than by an install path.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from bash_command import expand, git_calls, segments  # noqa: E402

# Flags of `checkout`/`switch` that take a SEPARATE value token, so the token
# after them is that value and never the switch target. `-b`/`-B`/`-c`/`-C`/
# `--orphan` do name the target, and are handled by _target() rather than here.
#
# `-t`/`--track` deliberately absent: git-checkout(1) spells it
# `-t, --track[=(direct|inherit)]`, an OPTIONAL value attached with `=`, so it
# consumes no following token. Listing it here made _target skip two tokens and
# swallow the target, so `git checkout -t origin/foo` — a real branch creation
# and switch, per "--track without -b implies branch creation" — returned None
# and was never judged.
_FLAGS_WITH_VALUE = {"--conflict", "--pathspec-from-file"}
_NAMES_TARGET = {"-b", "-B", "-c", "-C", "--orphan"}

# Patch mode never switches branches: it picks hunks out of a tree-ish into the
# working tree, so a target beside it is a source to restore from, not a
# destination.
_PATCH = {"-p", "--patch"}


def _target(args: list[str]) -> tuple[str, bool] | None:
    """``(target, is_new_branch)`` for a checkout/switch, or None for neither.

    None covers the shapes that move no HEAD: a file checkout (`--` present, or
    a lone `.`), and a bare `git checkout` with no target at all.

    The second element matters because it says whether the target can be
    checked against the repo. A `-b`/`-c` target is a branch being created, so
    it resolves to nothing yet and is unambiguously a switch; a bare target is
    a word that might be a branch, a tag, a sha — or a filename, which is the
    case that must not be treated as a switch.
    """
    if any(tok in _PATCH for tok in args):
        return None  # `git checkout -p <tree-ish>` restores hunks
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return None  # `git checkout -- <path>` restores files
        if tok in _NAMES_TARGET:
            return (args[i + 1], True) if i + 1 < len(args) else None
        if tok in _FLAGS_WITH_VALUE:
            i += 2
            continue
        # `git checkout -` is "the previous branch", not a flag. It resolves
        # through `@{-1}` rather than as a ref, so it is flagged as "new" to
        # skip the ref check, and it is treated as a violation because from the
        # pin it always leads off the pin. That is not quite "by definition":
        # a checkout already sitting off the pin has `-` pointing back AT the
        # pin, and there the denial is wrong. Left as is: reaching that state
        # takes an actor the hook missed, the denial is fail-closed, and
        # resolving it costs a git call on every `-` to fix a shape nobody has
        # hit.
        if tok == "-":
            return "-", True
        if tok.startswith("-"):
            i += 1
            continue
        if tok == ".":
            return None
        # A bare target only switches when it is the LAST positional. Anything
        # after it — a `--`, or another positional — makes it a tree-ish to
        # restore paths from: `git checkout other -- file.txt` and
        # `git checkout other file.txt` both move no HEAD. Reading the first
        # positional and stopping denied both.
        rest = args[i + 1 :]
        if any(a == "--" or not a.startswith("-") for a in rest):
            return None
        return tok, False
    return None


def _moves_head(cwd: str, target: str) -> bool:
    """Whether a bare `git checkout <target>` would switch rather than restore.

    Without this, `git checkout README.md` — an ordinary file restore, which
    moves no HEAD — is read as a switch to a branch called README.md and
    denied. An existing path wins: where the word is both, git itself refuses
    as ambiguous, and treating it as a path means the guard declines to judge
    rather than blocking a command git was going to reject anyway.
    """
    if os.path.exists(os.path.join(cwd, target)):
        return False
    if (
        _git(cwd, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
        is not None
    ):
        return True
    # git's DWIM: `git checkout foo` with no local `foo` but exactly one
    # `<remote>/foo` creates the local branch and switches to it. That resolves
    # no local ref, so the check above misses it — and it is a common shape,
    # since it is how you pick up a branch pushed from another machine.
    return bool(
        _git(cwd, "for-each-ref", "--format=%(refname)", f"refs/remotes/*/{target}")
    )


def _git(cwd: str, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _violation(cwd: str, target: str) -> tuple[str, str] | None:
    """``(pinned, target)`` when this switch is refused, else None."""
    pinned = _git(cwd, "config", "--get", "hooks.pinnedBranch")
    if not pinned:
        return None
    # Only the main worktree is pinned. A linked worktree has its own gitdir
    # under .git/worktrees/<name>, so the two paths differ there — which is how
    # the normal workflow (branch work in a worktree) stays unaffected.
    git_dir = _git(cwd, "rev-parse", "--path-format=absolute", "--git-dir")
    common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if not git_dir or git_dir != common:
        return None
    if target.removeprefix("refs/heads/") == pinned:
        return None  # returning to the pin is always allowed
    return pinned, target


def _reason(pinned: str, target: str) -> str:
    return (
        f"This checkout is pinned to '{pinned}' — refusing to switch it to '{target}'. "
        "Branch work belongs in its own worktree, not in this checkout. "
        "To unpin it for good: git config --unset hooks.pinnedBranch"
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed payload: don't interfere with normal flow
    if data.get("tool_name") != "Bash":
        return
    cmd = data.get("tool_input", {}).get("command", "")
    base = data.get("cwd") or os.getcwd()

    # `segments` yields two token forms per segment, so a call can be seen
    # twice; any deny prints once and returns, so a duplicate is harmless.
    for forms in segments(cmd):
        for tokens in forms:
            for dir_override, subcommand, args in git_calls(tokens):
                if subcommand not in {"checkout", "switch"}:
                    continue
                found = _target(args)
                if not found:
                    continue
                target, is_new = found
                # A relative `-C` is relative to the SHELL's cwd, not to
                # whatever directory this hook process happens to run in.
                cwd = expand(dir_override, base) if dir_override else base
                if not cwd or not os.path.isdir(cwd):
                    continue  # a path we cannot resolve is not a repo we can judge
                if not is_new and not _moves_head(cwd, target):
                    continue
                violation = _violation(cwd, target)
                if violation:
                    print(
                        json.dumps(
                            {
                                "hookSpecificOutput": {
                                    "hookEventName": "PreToolUse",
                                    "permissionDecision": "deny",
                                    "permissionDecisionReason": _reason(*violation),
                                }
                            }
                        )
                    )
                    return


if __name__ == "__main__":
    main()
