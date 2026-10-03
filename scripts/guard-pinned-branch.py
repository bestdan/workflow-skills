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

Each git call is judged against the directory it actually runs in: a ``cd``
earlier in the command moves it (see ``_git_calls``), and ``git -C <dir>``
moves it again, relative to wherever the ``cd`` left the shell. So
``cd <worktree> && git checkout -b x`` is judged against the worktree and
allowed, which is the workflow the pin pushes people toward.

Bypass one call with ``env WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1 git checkout
<branch>`` (a bare ``WORKFLOW_SKILLS_ALLOW_HEAD_MOVE=1 git ...`` prefix works
too). Only an assignment on that git invocation counts; naming the variable
anywhere else in the command disarms nothing.

Reads the hook payload as JSON on stdin; emits a PreToolUse decision on stdout.
Anything not matched produces no output and falls through to the normal flow.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys

# A hook runs as a standalone script, not as part of a package, so the shared
# parser is found by this file's own directory rather than by an install path.
sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from bash_command import (  # noqa: E402
    PREFIX,
    expand,
    git_calls,
    segments,
)

BYPASS = "WORKFLOW_SKILLS_ALLOW_HEAD_MOVE"

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

# `-d` is `--detach` on both checkout and switch. It only shapes the bypass
# line, so the command offered moves the checkout the way the refused one would.
_DETACH = {"-d", "--detach"}


def _apply_cd(tokens: list[str], cwd: str | None) -> str | None:
    """The directory a `cd` segment leaves the shell in, or None if unknown.

    None is deliberate rather than a fallback to the old directory: `cd -` and
    `cd $SOME_VAR` are unresolvable here, and judging a later git call against
    the directory the shell has just LEFT is how a false verdict gets made with
    full confidence. An unknown cwd makes the guard decline to judge.
    """
    rest = tokens[1:]
    # `cd -` is the previous directory, which is unknowable here. It has to be
    # tested BEFORE flags are filtered: `-` starts with a dash, so filtering
    # first drops it, leaves no arguments, and resolves `cd -` to $HOME.
    if "-" in rest:
        return None
    args = [t for t in rest if not t.startswith("-")]  # -L / -P / --
    if not args:
        return os.path.expanduser("~")
    return expand(args[0], cwd)


def _bypassed(tokens: list[str]) -> bool:
    """Whether the segment's own prefix assigns the bypass variable.

    Only the wrapper/keyword/assignment tokens ahead of the command word count
    (`env VAR=1 git ...` or `VAR=1 git ...`), so a command that merely NAMES the
    variable (`rg VAR docs`, `echo VAR`) is not bypassed, and the assignment
    disarms the one invocation it prefixes, not the segments after it. Any
    value counts, an empty one or `0` included, as in the dotfiles guard this
    was ported from: typing the variable onto the git call is the deliberate
    act, and the value adds no information. A wrapper option before the
    assignment (`env -i VAR=1 git ...`) ends the prefix scan, so that shape is
    not read as a bypass and is still judged (fail-closed), as is a bypass
    inside an executor (`bash -c "env VAR=1 git ..."`).
    """
    for tok in tokens:
        if tok.startswith(f"{BYPASS}="):
            return True
        if tok not in PREFIX and not re.match(r"^[A-Za-z_][\w]*=", tok):
            return False
    return False


def _git_calls(cmd: str, base: str | None):
    """Yield ``(cwd, subcommand, args)`` per git call, cwd as the shell has it.

    The parsing is ``bash_command``'s (segments, both token forms, and
    ``git_calls`` with its ``-C``/``--work-tree``/``GIT_WORK_TREE`` handling)
    and is left unchanged for its other callers. Two things are layered on
    here, because they are this guard's decisions, not the parser's.

    It tracks ``cd`` across segments, so each git call is judged against the
    directory it actually runs in. Judging every call against the payload cwd
    denied ``cd <worktree> && git checkout -b foo`` (a checkout inside a
    worktree, which the pin explicitly allows) against the pinned main
    checkout. A relative ``-C`` then resolves against the tracked directory.

    And it drops any call carrying the bypass as an assignment on the
    invocation itself (``_bypassed``).

    The cd tracking is linear across segments and models no scope, because the
    splitter has already thrown the scope away: it cuts on ``( )`` and on
    ``&&`` inside quotes alike. So ``(cd X && git checkout foo); git checkout
    bar`` applies X to the second call too (fail-open), and ``bash -c 'cd X &&
    git checkout foo'`` is not seen as a cd at all, since its head word is
    ``bash``; the call inside is judged against the outer cwd (fail-closed).
    Neither is modelled: the only implementable fix for the executor case is
    the same leak the subshell case already has, and neither shape has a reason
    to be written now that a bare ``cd X && git ...`` works. ``pushd``/``popd``
    and ``builtin cd`` are not tracked either, and ``cd -- -dashed`` resolves
    to HOME for the same lack of a reason.

    A ``cd`` into a directory that does not exist when the hook runs declines
    to judge the calls after it, so ``cd /missing; git checkout other`` (or
    ``||``) is fail-open: the cd fails and the checkout runs where the shell
    already was. Keeping the previous directory instead would be a confident
    wrong answer in the common case, because an earlier segment of the same
    command often creates the directory: ``git worktree add <p> -b x main &&
    cd <p> && git checkout -b y`` and ``git clone <url> d && cd d && ...``
    would both be judged against the pinned checkout and denied. For the same
    reason ``mkdir sub && cd sub && git checkout other`` inside the pinned
    checkout is fail-open too.
    """
    cwd = base
    for forms in segments(cmd):
        # shlex's form when it parsed, since it keeps a quoted path with a
        # space in it as one token; the whitespace split otherwise.
        primary = forms[-1] if forms else []
        if primary and primary[0] == "cd":
            cwd = _apply_cd(primary, cwd)
            continue
        # Both forms can yield the same call, so a call can be seen twice; any
        # deny prints once and returns, so a duplicate is harmless.
        for tokens in forms:
            if _bypassed(tokens):
                continue
            for dir_override, subcommand, args in git_calls(tokens):
                # A relative `-C` is relative to the SHELL's cwd, not to
                # whatever directory this hook process happens to run in.
                here = expand(dir_override, cwd) if dir_override else cwd
                yield here, subcommand, args


# The checkout spelling of each branch-creating flag, so the bypass line names
# a command that creates the branch rather than one that fails on it. switch's
# `-c`/`-C` are checkout's `-b`/`-B`.
_CREATE_AS = {"-b": "-b", "-c": "-b", "-B": "-B", "-C": "-B", "--orphan": "--orphan"}


def _target(args: list[str]) -> tuple[str, bool, str | None] | None:
    """``(target, is_new_branch, create_flag)`` for a checkout/switch, or None.

    None covers the shapes that move no HEAD: a file checkout (`--` present, or
    a lone `.`), and a bare `git checkout` with no target at all.

    The second element matters because it says whether the target can be
    checked against the repo. A `-b`/`-c` target is a branch being created, so
    it resolves to nothing yet and is unambiguously a switch; a bare target is
    a word that might be a branch, a tag, a sha — or a filename, which is the
    case that must not be treated as a switch.

    The third is the creating flag in checkout's spelling (`_CREATE_AS`), or
    None when no branch is created. Only the bypass line reads it.
    """
    if any(tok in _PATCH for tok in args):
        return None  # `git checkout -p <tree-ish>` restores hunks
    i = 0
    while i < len(args):
        tok = args[i]
        if tok == "--":
            return None  # `git checkout -- <path>` restores files
        if tok in _NAMES_TARGET:
            if i + 1 >= len(args):
                return None
            return args[i + 1], True, _CREATE_AS[tok]
        # `--orphan=<name>` is `--orphan <name>` with the value attached.
        if tok.startswith("--orphan="):
            name = tok[len("--orphan=") :]
            return (name, True, "--orphan") if name else None
        # git also takes a short option's value attached: `-bfeature` is
        # `-b feature`. Matching only the separate-token form let
        # `git checkout -bfeature` create and switch a branch unjudged.
        if len(tok) > 2 and tok[:2] in _NAMES_TARGET:
            return tok[2:], True, _CREATE_AS[tok[:2]]
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
            return "-", True, None
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
        return tok, False, None
    return None


def _moves_head(cwd: str, target: str, subcommand: str) -> bool:
    """Whether a bare `git <subcommand> <target>` would switch rather than restore.

    Without this, `git checkout README.md` — an ordinary file restore, which
    moves no HEAD — is read as a switch to a branch called README.md and
    denied.

    The order follows git's, measured against git 2.43 with a branch and a
    directory sharing one name. A word that resolves as a commit switches,
    whether or not a path of that name exists, for checkout and switch alike;
    checking the path first let `git checkout docs` move the pin whenever the
    repo had a `docs/` directory. Only when the word is NOT a ref does a path
    matter, and only to `checkout`: there git restores the path, or refuses
    as ambiguous when a remote also holds the name, so declining to judge
    blocks nothing git was going to run. `switch` takes no paths at all and
    DWIMs the remote branch regardless.
    """
    if (
        _git(cwd, "rev-parse", "--verify", "--quiet", f"{target}^{{commit}}")
        is not None
    ):
        return True
    if subcommand == "checkout" and os.path.exists(os.path.join(cwd, target)):
        return False
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


def _reason(pinned: str, target: str, detached: bool, create: str | None) -> str:
    # The bypass line is a command to run, so it does what the refused one
    # asked: it carries `--detach` when that detached, and the creating flag
    # when that created a branch — `git checkout x` fails on a branch that does
    # not exist yet. The target is shell-quoted, since a ref name may hold `$`
    # or a quote; ordinary names render unchanged.
    flag = f"{create} " if create else "--detach " if detached else ""
    return (
        f"This checkout is pinned to '{pinned}' — refusing to switch it to '{target}'.\n"
        "Branch work belongs in its own worktree, not in this checkout.\n"
        f"To move this checkout anyway: env {BYPASS}=1 git checkout {flag}{shlex.quote(target)}\n"
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

    for cwd, subcommand, args in _git_calls(cmd, base):
        if subcommand not in {"checkout", "switch"}:
            continue
        found = _target(args)
        if not found:
            continue
        target, is_new, create = found
        if not cwd or not os.path.isdir(cwd):
            continue  # a path we cannot resolve is not a repo we can judge
        if not is_new and not _moves_head(cwd, target, subcommand):
            continue
        violation = _violation(cwd, target)
        if violation:
            detached = any(tok in _DETACH for tok in args)
            print(
                json.dumps(
                    {
                        "hookSpecificOutput": {
                            "hookEventName": "PreToolUse",
                            "permissionDecision": "deny",
                            "permissionDecisionReason": _reason(
                                *violation, detached, create
                            ),
                        }
                    }
                )
            )
            return


if __name__ == "__main__":
    main()
