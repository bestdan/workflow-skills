"""Split a Bash command into segments and tokens, and find the git calls in it.

Shared by the PreToolUse guards under ``scripts/``. A hook runs as a standalone
script rather than as part of a package, so a guard imports this by putting its
own directory on ``sys.path`` first.

A copy of the parser in bestdan/dotfiles' agents/guard_dangerous_git.py and
agents/guard_pinned_branch.py, which is hardened against redirects, wrappers,
executors, quoting and segment splitting. A plugin hook cannot import from a
machine's dotfiles, so the pieces the guards use live here.
"""

from __future__ import annotations

import os
import re
import shlex

# Anything that ends a command and starts a new one. Deliberately quote-blind:
# `echo "$(…; git commit)"` really does run git, and every attempt to make a
# regex track quoting failed open.
SEGMENT_SPLIT = re.compile(r"[|;&\n()`{}]+")

# A whole redirect — operator and target — dropped BEFORE the split, because
# three redirect operators are spelled with a character the splitter treats as
# a separator (`2>&1`, `>&2` on `&`; `>| file` on `|`). The target is one shell
# word, quoted or escaped whitespace included; an fd duplication takes none.
REDIRECT = re.compile(
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
WRAPPERS = {"rtk", "sudo", "command", "env", "nohup", "time", "exec", "builtin"}
# Shell keywords that can open a segment; they take no options.
KEYWORDS = {"if", "elif", "then", "else", "do", "while", "until", "!"}
PREFIX = WRAPPERS | KEYWORDS
# Head words that run their arguments, so git can sit anywhere after them.
EXECUTORS = {"bash", "sh", "zsh", "dash", "ksh", "eval", "xargs", "ssh"}
# git's own options that take a separate value token.
GIT_OPTS_WITH_VALUE = {
    "-C",
    "-c",
    "--git-dir",
    "--work-tree",
    "--namespace",
    "--exec-path",
    "--config-env",
}


def tokenize(segment: str) -> list[list[str]]:
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


def expand(path: str, cwd: str | None) -> str | None:
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


def segments(cmd: str):
    """Yield the token forms of each command segment.

    Redirects are stripped BEFORE the split, because three redirect operators
    are spelled with a
    character the splitter treats as a separator (`2>&1` and `>&2` on `&`,
    `>| file` on `|`), so splitting first leaves a bare fd number as the
    segment's head word and the command behind it is never reached. Redirect
    *targets* are therefore recovered from the whole command instead of from a
    segment, by whichever caller needs them.
    """
    for segment in SEGMENT_SPLIT.split(REDIRECT.sub(" ", cmd)):
        yield tokenize(segment)


def git_calls(tokens: list[str]):
    """Yield ``(dir_override, subcommand, args)`` per git call in one segment.

    Head-word anchoring and the wrapper/executor scan are the dotfiles
    parser's; what is kept here — and what that parser normalizes away — is
    ``-C``, which decides WHICH repo
    a command targets and is therefore the whole question.

    A work tree named by ``--work-tree`` or a ``GIT_WORK_TREE=`` prefix is
    where a mutating subcommand actually writes, so it wins over ``-C``. A
    relative one resolves against the ``-C`` directory, as git does.
    """
    head = 0
    wrapped = False
    while head < len(tokens) and (
        tokens[head] in PREFIX or re.match(r"^[A-Za-z_][\w]*=", tokens[head])
    ):
        wrapped = wrapped or tokens[head] in WRAPPERS
        head += 1
    if head >= len(tokens):
        return
    scan_all = wrapped or tokens[head] in EXECUTORS
    for start in range(head, len(tokens)) if scan_all else [head]:
        word = tokens[start]
        if word != "git" and not word.endswith("/git"):
            continue
        i = start + 1
        dir_override = None
        work_tree = None
        for token in tokens[:start]:
            if token.startswith("GIT_WORK_TREE="):
                work_tree = token[len("GIT_WORK_TREE=") :]
        while i < len(tokens) and tokens[i].startswith("-"):
            if tokens[i].startswith("--work-tree="):
                work_tree = tokens[i][len("--work-tree=") :]
            elif tokens[i] in GIT_OPTS_WITH_VALUE:
                if i + 1 < len(tokens):
                    if tokens[i] == "-C":
                        dir_override = tokens[i + 1]
                    elif tokens[i] == "--work-tree":
                        work_tree = tokens[i + 1]
                i += 1
            i += 1
        if work_tree:
            if dir_override and not os.path.isabs(os.path.expanduser(work_tree)):
                work_tree = os.path.join(dir_override, work_tree)
            dir_override = work_tree
        if i < len(tokens):
            yield dir_override, tokens[i], tokens[i + 1 :]


def head_index(tokens: list[str]) -> int:
    """Index of a segment's command word, past any wrapper, keyword or assignment.

    A wrapper's own options (`sudo -n`) are skipped too. One that takes a
    value (`sudo -u root`) still leaves the value as the head, which fails open.
    """
    i = 0
    wrapped = False
    while i < len(tokens) and (
        tokens[i] in PREFIX
        or re.match(r"^[A-Za-z_][\w]*=", tokens[i])
        or (wrapped and tokens[i].startswith("-"))
    ):
        wrapped = wrapped or tokens[i] in WRAPPERS
        i += 1
    return i


def head_word(tokens: list[str]) -> str | None:
    """The command word of a segment, past any wrapper, keyword or assignment."""
    i = head_index(tokens)
    if i >= len(tokens):
        return None
    return os.path.basename(tokens[i])
