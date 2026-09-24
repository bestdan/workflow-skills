#!/usr/bin/env python3
"""PreToolUse guard: refuse a write into a worktree this session never entered.

Detection alone was tried first: a statusline that notices the session's edits
landing outside the tree it stands in annotated the problem and refused
nothing, and that did not stop ~30 consecutive ghost-editing calls in the run
behind #827. This is the half that refuses. It moved here from
bestdan/dotfiles#911 because nothing in it is specific to one machine.

WHAT GOES WRONG WHEN NOTHING REFUSES. An agent that edits, commits and pushes
into a worktree it never entered diverges from the human's shell, editor,
prompt and build, with no shared signal between them. Their build builds the
tree they are standing in and verifies nothing, their editor shows stale
content, and a "done" report names files they cannot see. ``git -C`` working
*correctly* is what makes it quiet: nothing fails. The rule it breaks is
"stand in the tree you write", in ``agent-guidance``'s ``portable.md``.

There is a second cost. With many concurrent sessions, where a session stands
is how a terminal sidebar or tab title tells them apart. A session that reaches
into a worktree keeps advertising the checkout it launched from while the work
happens elsewhere, so reaching in defeats that coordination signal, not just
the verification.

WHY THE ``Bash`` MATCHER IS THE LOAD-BEARING ONE. Auto mode instructs the model
to prefer Bash for file changes (bestdan/dotfiles#892), so the #827 edits went
through ``python3 - <<'PY'`` heredocs and never touched an ``Edit``/``Write``
hook. Registering only on ``Edit|Write|NotebookEdit`` would miss the exact
failure this exists for. Both are registered; Bash is the one that fires.

TWO DECISIONS, ONE HOOK.

  1. A write into a *different* linked worktree of the session's own repo is
     DENIED, with ``EnterWorktree`` named in the refusal.
  2. The first write of a session standing in a MAIN checkout on its default
     branch emits a non-blocking warning: write-work belongs in a worktree.
     Non-blocking so a deliberate one-line fix still goes through.

The second is delivered as ``hookSpecificOutput.additionalContext`` rather than
the stderr the issue proposed. Claude Code sends stderr from a hook that exits
0 to the debug log only — never the transcript, and the model never sees it —
so an exit-0 stderr warning would have been invisible to the one reader who can
act on it. ``permissionDecision: "allow"`` is deliberately NOT set: that would
short-circuit the permission system for the very calls this is watching, which
is a far larger change than the warning is worth.

READS ARE NOT DENIED, WRITES ARE. Per-command path flags (``rg <pattern>
<path>``, ``git -C <path> log``) are the recommended way to READ another tree
without moving the session, and bestdan/dotfiles#909 qualified that advice for
writes alone. A guard that denied reads too would contradict it and false-deny
a taught idiom — and a guard that false-denies gets disabled, which costs the
denials that matter. So a foreign path has to carry *write evidence* before it
is refused. The evidence is enumerated in ``_write_targets`` and is a denylist:
unknown shapes fail open. Prose covers what the parser misses; the hook covers
what prose does not stop.

Bypass one command with ``env WORKFLOW_SKILLS_ALLOW_FOREIGN_WRITE=1 <command>``.
The ``env`` form rather than a bare ``VAR=1`` prefix, because some setups run a
guard that denies an assignment-prefixed git so it can still match a
permission rule.

Reads the hook payload as JSON on stdin; emits a PreToolUse decision on stdout.
Anything not matched produces no output and falls through to the normal flow.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import subprocess
import sys

BYPASS = "WORKFLOW_SKILLS_ALLOW_FOREIGN_WRITE"

# --- command parsing ---------------------------------------------------------
# Copied, not reinvented, from bestdan/dotfiles' agents/guard_dangerous_git.py
# and agents/guard_pinned_branch.py, which this guard imported from while it
# lived there. That parser was hardened against redirects, wrappers,
# executors, quoting and segment splitting over half a dozen issues (dotfiles
# #620, #624, #625, #638), and a fresh one would reintroduce every one of them.
# A plugin hook cannot import from a machine's dotfiles, so the pieces this
# guard uses live here.

# Anything that ends a command and starts a new one. Deliberately quote-blind:
# `echo "$(…; git commit)"` really does run git, and every attempt to make a
# regex track quoting failed open.
_SEGMENT_SPLIT = re.compile(r"[|;&\n()`{}]+")

# A whole redirect — operator and target — dropped BEFORE the split, because
# three redirect operators are spelled with a character the splitter treats as
# a separator (`2>&1`, `>&2` on `&`; `>| file` on `|`). The target is one shell
# word, quoted or escaped whitespace included; an fd duplication takes none.
_REDIRECT = re.compile(
    r"""
    (?: & | [0-9]+ )?
    (?: <<< | << | >> | >\| | < | > )
    (?:
        & [0-9]* -?
      | [ \t]* (?: "[^"]*" | '[^']*' | (?: \\. | [^\s|;&()`{}<>] )* )
    )
    """,
    re.VERBOSE,
)

# Wrappers that run the command after them, and take options of their own —
# so a wrapped segment is scanned at every position rather than at its head.
_WRAPPERS = {"rtk", "sudo", "command", "env", "nohup", "time", "exec", "builtin"}
# Shell keywords that can open a segment; they take no options.
_KEYWORDS = {"if", "elif", "then", "else", "do", "while", "until", "!"}
_PREFIX = _WRAPPERS | _KEYWORDS
# Head words that run their arguments, so git can sit anywhere after them.
_EXECUTORS = {"bash", "sh", "zsh", "dash", "ksh", "eval", "xargs", "ssh"}
# git's own options that take a separate value token.
_GIT_OPTS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
}


def _tokenize(segment: str) -> list[list[str]]:
    """Tokenize a segment both ways, because neither alone is enough.

    Stripping quote characters and splitting on whitespace tears a quoted
    argument containing a space; ``shlex`` gets that right but folds
    ``$'…'`` into one token. Both forms are returned and either may match.
    """
    forms = [re.sub(r"\$?[\"']", "", segment).split()]
    try:
        forms.append(shlex.split(segment))
    except ValueError:
        pass  # segment splitting can cut a quote in half
    return forms


def _expand(path: str, cwd: str | None) -> str | None:
    """Resolve a path token the way the shell would, or None if we cannot.

    No shell expansion has happened by the time a hook sees the command, so
    ``$HOME/src/...`` and ``~/src/...`` arrive literally and are expanded here.
    Any OTHER variable makes the path unknowable, where declining beats
    guessing.
    """
    if path.startswith("$HOME/") or path == "$HOME":
        path = os.path.expanduser("~") + path[len("$HOME") :]
    if "$" in path:
        return None
    path = os.path.expanduser(path)
    if os.path.isabs(path):
        return path
    return os.path.join(cwd, path) if cwd else None


# git subcommands that change a working tree, an index, or a branch — the ones
# whose damage is the divergence this guard exists for. A denylist rather than
# a read-only allowlist: git has a long tail of read plumbing (`cat-file`,
# `for-each-ref`, `merge-base`, `name-rev`, …) and an allowlist would refuse
# every one it had not been taught, which is a false denial on a read.
#
# `fetch` is deliberately absent. It writes remote-tracking refs into the
# object store that every worktree of the repo shares, so running it from a
# foreign worktree changes nothing the session's own tree would not have seen
# anyway. `pull` is present: it moves the foreign working tree.
_MUTATING_GIT = {
    "add",
    "am",
    "apply",
    "bisect",
    "checkout",
    "cherry-pick",
    "clean",
    "commit",
    "commit-tree",
    "filter-branch",
    "gc",
    "init",
    "merge",
    "mv",
    "prune",
    "pull",
    "push",
    "rebase",
    "repack",
    "replace",
    "reset",
    "restore",
    "revert",
    "rm",
    "sparse-checkout",
    "switch",
    "update-index",
    "update-ref",
    "write-tree",
}

# Subcommands that read or write depending on their arguments. Each maps to the
# first positional that makes it a read; `_git_writes` handles the rest. Left
# out of `_MUTATING_GIT` above so that the common read forms — `git worktree
# list`, `git stash list`, `git config --get` — are not refused.
_DUAL_READ_POSITIONALS = {
    "worktree": {"list"},
    "stash": {"list", "show"},
    "remote": {"show", "get-url"},
    "submodule": {"status", "summary"},
    "notes": {"list", "show"},
    "reflog": {"show"},
}

# `git config`'s read forms are named by a flag, not a positional: the value
# arguments of `config --get user.name` and `config user.name dan` are shaped
# identically, so only the flag separates them.
_CONFIG_READ_FLAGS = {
    "--get",
    "--get-all",
    "--get-regexp",
    "--get-urlmatch",
    "--list",
    "-l",
}

# `git branch` / `git tag` mutate when one of these is present, and otherwise
# when a bare positional names a ref to create.
_REF_EDIT_FLAGS = {
    "-d",
    "-D",
    "-m",
    "-M",
    "-c",
    "-C",
    "-f",
    "--delete",
    "--move",
    "--copy",
    "--force",
    "--edit-description",
    "--set-upstream-to",
    "--unset-upstream",
}
# Flags of `branch`/`tag` that consume the token after them, so that token is a
# value and not the new ref's name. Without this `git branch --contains HEAD`
# reads as creating a branch called HEAD.
_REF_FLAGS_WITH_VALUE = {
    "--contains",
    "--no-contains",
    "--merged",
    "--no-merged",
    "--points-at",
    "--format",
    "--sort",
    "--color",
    "-u",
    "--set-upstream-to",
}

# Commands whose job is to change a file. A foreign path among their arguments
# is write evidence on its own.
_WRITERS = {
    "tee",
    "cp",
    "mv",
    "rm",
    "rmdir",
    "mkdir",
    "touch",
    "install",
    "ln",
    "truncate",
    "dd",
    "chmod",
    "chown",
    "shred",
    "unlink",
    "patch",
    "rsync",
    "split",
    "tar",
    "unzip",
}

# Interpreters. Their arguments are code, not paths this guard can classify —
# the #827 shape is a foreign path inside a `python3 - <<'PY'` heredoc — so a
# foreign path anywhere near one is treated as a write. This is the guard's one
# deliberately fail-CLOSED rule, and the bypass exists for it.
_INTERPRETERS = {"python", "python3", "node", "ruby", "perl", "php", "osascript", "uv"}

# In-place editors: the flag is what makes them writers. `sed <foreign>` reads.
_INPLACE = {"sed", "perl", "awk", "gawk", "ruby"}

# A redirect that writes. `<`, `<<` and `<<<` read, and an fd duplication
# (`2>&1`, `>&-`) names no file at all, so the negative lookahead is what keeps
# `cmd 2>&1` from reading as a write to a file called `1`.
_WRITE_REDIRECT = re.compile(
    r"""
    (?: & | [0-9]+ )?
    (?: >> | >\| | > )
    (?! & )
    [ \t]*
    ( "[^"]*" | '[^']*' | (?: \\. | [^\s|;&()`{}<>] )+ )
    """,
    re.VERBOSE,
)

PATH_KEYS = ("file_path", "notebook_path")


def _git(cwd: str, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _real(path: str) -> str:
    """Physically resolve a path, so two spellings of one directory compare equal.

    macOS puts the usual scratch roots behind a ``/tmp`` -> ``/private/tmp``
    symlink, and ``git worktree list`` and ``git rev-parse --show-toplevel`` do
    not always agree on which form they print. Comparing the raw strings
    reports the session's OWN worktree as foreign — a false denial on every
    command in the session, which is the fastest possible way to get this hook
    switched off. Carried over from #831, where the
    shell implementation of the same check paid for it.
    """
    return os.path.realpath(path)


def _worktrees(cwd: str) -> list[str] | None:
    """Physical roots of every usable worktree of this repo, or None.

    ``--porcelain -z`` rather than the line-oriented form: with ``-z`` each
    record is NUL-terminated, so a worktree path containing a tab *or* a
    newline is read back intact. The shell implementation of this check could
    only detect the tab and had to refuse to answer on it, because a tab split
    its intermediate records and fabricated a branch name that could collide
    with a real ref. Python reading NUL-delimited records has neither problem.

    A ``prunable`` worktree — one whose directory has been deleted but whose
    record git still lists — is dropped. Treating it as real would let this
    guard deny a command and name a path that no longer exists, so the fix it
    suggests could not be followed. That is the worst answer it can give.
    """
    out = _git(cwd, "worktree", "list", "--porcelain", "-z")
    if out is None:
        return None
    roots: list[str] = []
    path: str | None = None
    prunable = False
    for record in out.split("\0"):
        if record.startswith("worktree "):
            if path is not None and not prunable:
                roots.append(_real(path))
            path, prunable = record[len("worktree ") :], False
        elif record.startswith("prunable"):
            prunable = True
    if path is not None and not prunable:
        roots.append(_real(path))
    return roots


def _inside(path: str, root: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _owning_foreign(path: str, own: str, foreign: list[str]) -> str | None:
    """The foreign worktree holding ``path``, or None.

    The session's own root wins over any foreign root that also contains the
    path, and the longest foreign root wins among the rest. Both matter only if
    worktrees ever nest, which the usual layout does not do — but a
    nested pair would otherwise make the verdict depend on list order.
    """
    if _inside(path, own):
        return None
    best = None
    for root in foreign:
        if _inside(path, root) and (best is None or len(root) > len(best)):
            best = root
    return best


def _positionals(args: list[str], flags_with_value: set[str]) -> list[str]:
    out, i = [], 0
    while i < len(args):
        if args[i] in flags_with_value:
            i += 2
            continue
        if not args[i].startswith("-"):
            out.append(args[i])
        i += 1
    return out


def _git_writes(sub: str, args: list[str]) -> bool:
    """Whether this git subcommand changes anything, given its arguments."""
    if sub in _MUTATING_GIT:
        return True
    if sub in _DUAL_READ_POSITIONALS:
        first = next((a for a in args if not a.startswith("-")), None)
        if first is None:
            # `git remote`, `git stash`, `git reflog` with no positional each
            # default to a listing; `git worktree` and `git submodule` print
            # usage. Neither writes.
            return False
        return first not in _DUAL_READ_POSITIONALS[sub]
    if sub == "config":
        return not any(a in _CONFIG_READ_FLAGS for a in args)
    if sub in {"branch", "tag"}:
        if any(a in _REF_EDIT_FLAGS for a in args):
            return True
        return bool(_positionals(args, _REF_FLAGS_WITH_VALUE))
    return False


def _segments(cmd: str):
    """Yield the token forms of each command segment.

    Redirects are stripped BEFORE the split, for the reason dotfiles
    issue #638 recorded: three redirect operators are spelled with a
    character the splitter treats as a separator (`2>&1` and `>&2` on `&`,
    `>| file` on `|`), so splitting first leaves a bare fd number as the
    segment's head word and the command behind it is never reached. Redirect
    *targets* are therefore recovered from the whole command instead of from a
    segment, in `_write_targets`.
    """
    for segment in _SEGMENT_SPLIT.split(_REDIRECT.sub(" ", cmd)):
        yield _tokenize(segment)


def _bypassed(cmd: str) -> bool:
    """Whether the bypass is set as a real assignment on this command.

    Scoped to the assignment rather than a substring test over the command
    text, which is the point: a substring test is disarmed by anything that
    merely NAMES the variable — in this repo that includes grepping the docs
    that document it.
    """
    for forms in _segments(cmd):
        for tokens in forms:
            for token in tokens:
                if token.startswith(f"{BYPASS}="):
                    return True
    return False


def _git_calls(tokens: list[str]):
    """Yield ``(dir_override, subcommand, args)`` per git call in one segment.

    Head-word anchoring and the wrapper/executor scan are the dotfiles
    parser's; what is kept here — and what that parser normalizes away — is
    ``-C``, which decides WHICH repo
    a command targets and is therefore the whole question.
    """
    head = 0
    wrapped = False
    while head < len(tokens) and (
        tokens[head] in _PREFIX or re.match(r"^[A-Za-z_][\w]*=", tokens[head])
    ):
        wrapped = wrapped or tokens[head] in _WRAPPERS
        head += 1
    if head >= len(tokens):
        return
    scan_all = wrapped or tokens[head] in _EXECUTORS
    for start in range(head, len(tokens)) if scan_all else [head]:
        word = tokens[start]
        if word != "git" and not word.endswith("/git"):
            continue
        i = start + 1
        dir_override = None
        while i < len(tokens) and tokens[i].startswith("-"):
            if tokens[i] in _GIT_OPTS_WITH_VALUE:
                if tokens[i] == "-C" and i + 1 < len(tokens):
                    dir_override = tokens[i + 1]
                i += 1
            i += 1
        if i < len(tokens):
            yield dir_override, tokens[i], tokens[i + 1 :]


def _head_word(tokens: list[str]) -> str | None:
    """The command word of a segment, past any wrapper, keyword or assignment."""
    i = 0
    while i < len(tokens) and (
        tokens[i] in _PREFIX or re.match(r"^[A-Za-z_][\w]*=", tokens[i])
    ):
        i += 1
    if i >= len(tokens):
        return None
    return os.path.basename(tokens[i])


# The two grades of evidence `_write_targets` yields.
#
# DEFINITE is a path something in the command is unambiguously writing: a
# mutating git call's directory, a write redirect's target, a file argument of
# `cp`/`rm`/`tee`/`sed -i`. REACH is a path the command only *reaches into* —
# the target of a `cd`, or anything named near an interpreter whose code this
# guard cannot read.
#
# Both deny a foreign worktree, because a reach into one is the failure. Only
# DEFINITE arms the main-checkout warning, so `python3 scripts/foo.py` in the
# main checkout — a read as far as anything here can tell — does not spend the
# session's one warning on itself.
DEFINITE = "definite"
REACH = "reach"


def _write_targets(cmd: str, cwd: str):
    """Yield ``(resolved path, grade)`` per path this command writes or reaches.

    The evidence, in the order it is looked for:

      - ``cd <path>`` anywhere in the command (REACH). A `cd` that leaves the
        session's working directory does not even persist to the next Bash
        call, so this shape exists only to reach in — which is what makes a
        bare directory count here with no writer beside it.
      - ``git -C <path> <mutating subcommand>``, and a mutating git call with
        no ``-C``, which lands in the session's cwd (DEFINITE).
      - a write redirect's target (DEFINITE).
      - a path argument of a writer command or an in-place editor (DEFINITE),
        or of an interpreter (REACH).
      - every path mentioned anywhere in a command that feeds an interpreter
        inline, via ``-c`` or a heredoc (REACH). The code is opaque there, and
        that is the shape #827 actually took.

    Relative paths resolve against the payload's cwd rather than against a `cd`
    tracked through the command. Tracking it would only matter for a relative
    path resolved after a `cd` into a directory that is not itself foreign, and
    a `cd` into one that IS foreign is already denied.
    """
    inline_interpreter = False
    for forms in _segments(cmd):
        for tokens in forms:
            head = _head_word(tokens)
            # `<<` is asked of the whole command rather than this segment: the
            # heredoc operator is stripped along with the rest of the redirect
            # before the split, and a heredoc BODY is its own segment anyway,
            # since the splitter cuts on newlines.
            if head in _INTERPRETERS and ("-c" in tokens or "<<" in cmd):
                inline_interpreter = True
            for i, token in enumerate(tokens):
                if token == "cd" and i + 1 < len(tokens):
                    resolved = _expand(tokens[i + 1], cwd)
                    if resolved:
                        yield _real(resolved), REACH
            for dir_override, sub, args in _git_calls(tokens):
                if not _git_writes(sub, args):
                    continue
                target = _expand(dir_override, cwd) if dir_override else cwd
                if target:
                    yield _real(target), DEFINITE
            grade = None
            if head in _WRITERS or (
                head in _INPLACE and any(t.startswith("-i") for t in tokens)
            ):
                grade = DEFINITE
            elif head in _INTERPRETERS:
                grade = REACH
            if grade:
                for token in tokens[1:]:
                    if token.startswith("-"):
                        continue
                    resolved = _expand(token, cwd)
                    if resolved:
                        yield _real(resolved), grade
    for match in _WRITE_REDIRECT.finditer(cmd):
        target = match.group(1).strip("\"'")
        resolved = _expand(target, cwd)
        if resolved:
            yield _real(resolved), DEFINITE
    if inline_interpreter:
        # The paths are inside the code, so there is nothing to tokenize. Every
        # absolute-looking run of text in the command is offered instead, and
        # the caller decides which of them lands in a foreign worktree.
        for match in re.finditer(r"(?:\$HOME|~|/)[^\s\"'`,;:)\]}]*", cmd):
            resolved = _expand(match.group(0), cwd)
            if resolved:
                yield _real(resolved), REACH


def _reason(target: str, holder: str) -> str:
    return (
        f"{holder} is a worktree this session has not entered, and this command writes "
        f"into it ({target}).\n\n"
        f"  - Enter it instead: EnterWorktree with path {holder}, or re-run from inside it.\n"
        "  - Do not reach in with `git -C <path>`, `cd <path> && …`, or an absolute path. "
        "That succeeds quietly, so the user's shell, editor, prompt and build stay "
        "pointed at this tree while the work lands in that one — their verification "
        "verifies nothing and their editor shows stale content.\n"
        "  - It also costs any sidebar or tab title its signal: this session keeps advertising "
        "the checkout it launched from while the work happens elsewhere.\n\n"
        'The rule is "stand in the tree you write" (agent-guidance, portable.md). '
        f"To write into it from here anyway: env {BYPASS}=1 <command>."
    )


WARN = (
    "This is the MAIN checkout and it is on the default branch, and this is the "
    "session's first write to it. Write-work belongs in its own worktree: "
    "create the worktree first with EnterWorktree, and do the editing there. "
    "Not blocking — a deliberate one-line fix is fine, and this fires once per session."
)


def _warned_already(session: str | None) -> bool:
    """Whether this session has had the warning, stamping it if not.

    Keyed by session id, and hashed because the id arrives as untrusted hook
    stdin and is interpolated into a path. A session with no id is treated as
    already warned rather than warned on every call.
    """
    if not session:
        return True
    state_dir = os.path.join(
        os.environ.get("TMPDIR", "/tmp"), "claude-foreign-worktree"
    )
    try:
        os.makedirs(state_dir, mode=0o700, exist_ok=True)
        os.chmod(state_dir, 0o700)
        stamp = os.path.join(state_dir, hashlib.sha256(session.encode()).hexdigest())
        if os.path.exists(stamp):
            return True
        with open(stamp, "w"):
            pass
        os.chmod(stamp, 0o600)
        return False
    except OSError:
        return True  # cannot remember: stay quiet rather than warn every call


def _emit(payload: dict) -> None:
    print(json.dumps(payload))


def _deny(target: str, holder: str) -> None:
    _emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _reason(target, holder),
            }
        }
    )


def _warn() -> None:
    # No permissionDecision: this must not touch the permission flow. See the
    # module docstring for why this is not the stderr the issue proposed.
    _emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "additionalContext": WARN,
            },
            "systemMessage": WARN,
        }
    )


def main() -> None:
    try:
        data = json.load(sys.stdin)
    except Exception:
        return  # malformed payload: don't interfere with normal flow

    try:
        tool = data.get("tool_name")
        tool_input = data.get("tool_input", {}) or {}
        cwd = data.get("cwd") or os.getcwd()

        if tool == "Bash":
            cmd = tool_input.get("command", "")
            if not cmd or _bypassed(cmd):
                return
            targets = list(_write_targets(cmd, cwd))
        elif tool in {"Edit", "Write", "NotebookEdit"}:
            raw = next(
                (
                    tool_input[k]
                    for k in PATH_KEYS
                    if isinstance(tool_input.get(k), str) and tool_input[k]
                ),
                None,
            )
            if raw is None:
                return
            resolved = _expand(raw, cwd)
            if not resolved:
                return
            targets = [(_real(resolved), DEFINITE)]
        else:
            return

        if not targets:
            return

        own_top = _git(cwd, "rev-parse", "--show-toplevel")
        if not own_top:
            return  # not in a repo: nothing here is a worktree question
        own_top = _real(own_top)

        roots = _worktrees(cwd)
        if roots is None:
            return  # cannot enumerate: fail open rather than guess
        foreign = [r for r in roots if r != own_top]

        for target, _grade in targets:
            holder = _owning_foreign(target, own_top, foreign)
            if holder:
                _deny(target, holder)
                return

        # No foreign write. The remaining question is whether write-work is
        # happening in a main checkout that should be left alone.
        if not any(g == DEFINITE and _inside(t, own_top) for t, g in targets):
            return
        git_dir = _git(cwd, "rev-parse", "--path-format=absolute", "--git-dir")
        common = _git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
        if not git_dir or git_dir != common:
            return  # a linked worktree: this session is already isolated
        head = _git(cwd, "branch", "--show-current")
        if not head or head != _default_branch(cwd):
            return
        if not _warned_already(data.get("session_id")):
            _warn()
    except Exception:
        return  # a guard that breaks the session is worse than what it prevents


def _default_branch(cwd: str) -> str | None:
    """The repo's default branch, by the strongest evidence available.

    ``origin/HEAD`` is what the remote says, so it is asked first. A repo with
    no remote — every fixture, and plenty of local-only repos — falls back to
    a ``hooks.pinnedBranch`` pin where one is set, then to whichever of ``main``/``master`` exists.
    """
    ref = _git(cwd, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if ref and "/" in ref:
        return ref.split("/", 1)[1]
    pinned = _git(cwd, "config", "--get", "hooks.pinnedBranch")
    if pinned:
        return pinned
    for name in ("main", "master"):
        if _git(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}"):
            return name
    return None


if __name__ == "__main__":
    main()
