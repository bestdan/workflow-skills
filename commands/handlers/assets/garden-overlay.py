#!/usr/bin/env python3
"""Resolve `garden.importance_overlay` to the file `/garden-backlog` reads.

`commands/garden-backlog.md` §1 and `/doctor` Check 1d both read the key, and
they must agree on what it names. Joined onto the repo root by hand, the value
worked only as a relative path such as `../overlay.md`: an absolute or `$HOME`
value became `<root>//Users/...` or `<root>/$HOME/...`, which names no file,
and `/garden-backlog` stopped (#1563). One function answers for both callers.

The value may be:

- absolute — used as is;
- `~`, `~/...`, `$HOME`, `$HOME/...` or `${HOME}/...` — expanded against the
  `HOME` environment variable, and nothing else (no other variable is expanded,
  so a value never depends on the caller's wider environment);
- anything else — relative to the repo root, `../` included.

The result is normalised but symlinks are not resolved, so the path printed is
the one a reader would recognise.

Reading the YAML is the caller's job: assets here are stdlib-only, so a
non-string value is refused by the caller before this runs.

Usage:
  python3 garden-overlay.py --value '<garden.importance_overlay>' --root <repo root>

stdout is the resolved absolute path, whether or not it is a file. Exit 0 when
it is a file, 3 when it is not (stderr says so), 2 on bad input.
"""

import argparse
import os
import sys
from typing import Optional, Sequence

HOME_PREFIXES = ("${HOME}", "$HOME", "~")


def resolve(value: str, root: str, home: Optional[str]) -> str:
    v = value.strip()
    if not v:
        raise ValueError("garden.importance_overlay is empty")
    for prefix in HOME_PREFIXES:
        if v == prefix or v.startswith(prefix + "/"):
            if not home:
                raise ValueError(f"{value!r} names $HOME, which is unset")
            v = home + v[len(prefix) :]
            break
    if not os.path.isabs(v):
        v = os.path.join(root, v)
    return os.path.normpath(v)


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--value", required=True)
    p.add_argument("--root", required=True)
    args = p.parse_args(argv)
    if not os.path.isabs(args.root):
        print("garden-overlay: --root must be absolute", file=sys.stderr)
        return 2
    try:
        path = resolve(args.value, args.root, os.environ.get("HOME"))
    except ValueError as exc:
        print(f"garden-overlay: {exc}", file=sys.stderr)
        return 2
    print(path)
    if not os.path.isfile(path):
        print(f"garden-overlay: {path} is not a file", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
