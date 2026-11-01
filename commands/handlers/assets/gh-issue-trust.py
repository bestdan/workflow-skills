#!/usr/bin/env python3
"""Split candidate issues by whether their author may direct an agent.

On a public repo anyone can open an issue, and the gh-issue loop turns an issue
body into an agent's instructions: `/promote-tasks` scores it and `/do-tasks`
builds it, unattended, on the owner's machine with the owner's credentials. So
whoever wrote the issue gets to prompt that agent. This script is the gate: an
issue is trusted only when GitHub reports its author as the repo's `OWNER`, an
org `MEMBER`, or an invited `COLLABORATOR`. `CONTRIBUTOR` is deliberately not
trusted — it means only that the author once had a PR merged, which a stranger
can arrange.

**This script is the only reader of issue text.** Callers list candidates by
number and labels alone (labels are safe: only collaborators can set them), pass
the numbers here, and take `title` and `body` from the `trusted` entries this
prints. An `untrusted` entry carries the number, login and association and
nothing the author wrote, so an injected instruction never reaches the model's
context — not the scorer's, not the claimer's.

The read is REST (`gh api repos/{repo}/issues/{n}`) because `gh issue list
--json` has no author-association field, and because the cloud proxy that
refuses GraphQL serves REST (`dev_docs/nightly-gh-issue-routine.md`). It is a
GET, so this script never writes.

**It fails closed.** Any failed read exits non-zero with nothing on stdout, so a
caller cannot mistake a partial answer for a full one; an unknown or missing
association is untrusted.

Usage:
  python3 gh-issue-trust.py --repo owner/name --issue 7 --issue 9
"""

import argparse
import json
import subprocess
import sys

TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def fetch_issue(repo, number):
    code, out, err = run_gh(["api", f"repos/{repo}/issues/{number}"])
    if code != 0:
        raise SystemExit(
            f"gh api failed for {repo}#{number}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "{}")


def classify(repo, numbers):
    trusted, untrusted = [], []
    for number in numbers:
        issue = fetch_issue(repo, number)
        association = issue.get("author_association") or "NONE"
        author = (issue.get("user") or {}).get("login") or ""
        if association in TRUSTED_ASSOCIATIONS:
            trusted.append(
                {
                    "number": number,
                    "author": author,
                    "title": issue.get("title") or "",
                    "body": issue.get("body") or "",
                }
            )
        else:
            untrusted.append(
                {"number": number, "author": author, "association": association}
            )
    return {"trusted": trusted, "untrusted": untrusted}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True, help="owner/name")
    parser.add_argument(
        "--issue", type=int, action="append", required=True, help="repeatable"
    )
    args = parser.parse_args(argv)
    if "/" not in args.repo:
        raise SystemExit(f"--repo must be owner/name, got {args.repo!r}")
    print(json.dumps(classify(args.repo, args.issue), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
