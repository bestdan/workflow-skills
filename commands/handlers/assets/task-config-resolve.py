#!/usr/bin/env python3
"""Pick the right source for the task config's committed layer.

`/deliver-task` step 1 fetches the base before the claim acquires the work
branch, and `dev_docs/tasks/.task-config.yml` can move in that fetch — so a
`gh-issue.branch_prefix` read before it and used after it makes the claim lock
`task-<n>` where the fetched config says `<prefix>task-<n>`. Two sessions then
each create a ref the other cannot see and both conclude they won (#748).

Reading the fetched base is only correct when the file is **tracked**, and on
three of the four handlers it is not: `/task-config` puts `dev_docs/tasks/` in
the repo's local exclude for every handler except `repo-pr`. That makes three
cases, not two, and collapsing any pair of them reintroduces the bug:

- **Untracked** — a fetch cannot move a file git does not track, so there is no
  staleness to chase and the working tree is the only and correct source.
- **Tracked and present in `<base>`** — only the fetched revision sees the new
  value. A re-read of the working tree returns byte-identical content, because
  a fetch updates a ref and leaves the index and working tree alone.
- **Tracked but absent from `<base>`** — the config was deleted upstream, so the
  committed layer is **empty**. Falling back to the working tree here is the
  subtle one: the old file is still checked out, so that read succeeds and hands
  back a stale `branch_prefix`, which is #748 again.

Tracked-ness is therefore decided by the index (`git ls-files`), never by
presence in `<base>` — those are different questions and only the first one
distinguishes case 1 from case 3.

This lives in a script because it kept breaking as shell in runtime markdown,
where nothing in the gate can lint it: the probe it sits beside shipped four
defects that way, and this read shipped one more. It deliberately does **not**
parse YAML — assets here are stdlib-only so they run as plain `python3` with no
dependency — so it prints the chosen layer verbatim and leaves the overlay to
the caller, which is not where anything went wrong.

The optional `.task-config.local.yml` override is not this script's business: it
is untracked by construction, so a fetch can never move it and the caller reads
it from the working tree unconditionally.

Usage:
  python3 task-config-resolve.py committed --base main
  python3 task-config-resolve.py committed --base main --root /path/to/repo

stdout is the committed layer, verbatim and possibly empty. stderr names the
source that was chosen, so a surprising resolution is diagnosable. Exit 0 on a
decision, 4 when git could not answer — never a guess.
"""

import argparse
import subprocess
import sys

CONFIG_PATH = "dev_docs/tasks/.task-config.yml"


def run_git(args, root=None):
    """Run `git` and return (returncode, stdout, stderr). The seam the tests stub."""
    cmd = ["git"]
    if root is not None:
        cmd += ["-C", str(root)]
    proc = subprocess.run(cmd + args, capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


class GitUnavailable(Exception):
    """git could not answer, so tracked-ness is unknown and must not be guessed."""


def is_tracked(path=CONFIG_PATH, root=None):
    """Whether `path` is in the index — the question `<base>` presence cannot answer.

    `git ls-files --error-unmatch` exits 0 for a tracked path and 1 for an
    untracked one. Anything else means git itself failed (not a repository, no
    git on PATH), which is not the same as "untracked" and must not be read as
    one: guessing untracked there sends a tracked config's read to the working
    tree, which is the stale-prefix case.
    """
    code, _out, err = run_git(["ls-files", "--error-unmatch", path], root=root)
    if code == 0:
        return True
    if code == 1:
        return False
    raise GitUnavailable(err.strip() or f"git ls-files exited {code}")


def committed_layer(base, path=CONFIG_PATH, root=None):
    """The committed layer's text and the source it came from.

    Returns (text, source) where source is one of `base`, `worktree`, or
    `absent`, for the caller to report. See the module docstring for why the
    three cases cannot be collapsed into two.
    """
    if not is_tracked(path, root=root):
        # Untracked: the file itself is the only source. No revision holds it.
        try:
            with open(path if root is None else f"{root}/{path}") as handle:
                return handle.read(), "worktree"
        except OSError:
            return "", "absent"

    code, _out, _err = run_git(["cat-file", "-e", f"{base}:{path}"], root=root)
    if code != 0:
        # Tracked here, gone in the fetched base: the committed layer is empty.
        # Reading the still-checked-out file would resurrect a stale prefix.
        return "", "absent"

    code, out, err = run_git(["show", f"{base}:{path}"], root=root)
    if code != 0:
        raise GitUnavailable(err.strip() or f"git show {base}:{path} exited {code}")
    return out, "base"


def cmd_committed(args):
    try:
        text, source = committed_layer(args.base, root=args.root)
    except GitUnavailable as exc:
        print(f"cannot resolve the committed config layer: {exc}", file=sys.stderr)
        return 4
    print(f"committed layer source: {source}", file=sys.stderr)
    sys.stdout.write(text)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser(
        "committed",
        help="print the committed config layer from the right source (0 ok / 4 unknown)",
    )
    p.add_argument("--base", required=True, help="the fetched base ref")
    p.add_argument("--root", default=None, help="repository root (default: cwd)")
    p.set_defaults(func=cmd_committed)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
