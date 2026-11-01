#!/usr/bin/env python3
"""Split candidate issues by whether their author may direct an agent.

On a public repo anyone can open an issue, and the gh-issue loop turns an issue
body into an agent's instructions: `/promote-tasks` scores it and `/do-tasks`
builds it, unattended, on the owner's machine with the owner's credentials. So
whoever wrote the issue gets to prompt that agent. This script is the gate: an
issue is trusted only when GitHub reports its author's `author_association` as
one the repo's owner type allows:

- **User-owned repo:** `OWNER` or `COLLABORATOR`, the owner and the people they
  invited.
- **Organization-owned repo:** those, plus `MEMBER`. GitHub reports anyone whose
  access comes through the org, team grants included, as `MEMBER`, so leaving it
  out would make the owner's own colleagues, and usually the owner too, untrusted
  there. The cost is that every org member can direct the agent on that org's
  repos, which is the org's own trust boundary.

`CONTRIBUTOR` is never trusted: it means only that the author once had a PR
merged, which a stranger can arrange.

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

USER_REPO_TRUSTED = frozenset({"OWNER", "COLLABORATOR"})
ORG_REPO_TRUSTED = USER_REPO_TRUSTED | {"MEMBER"}


def run_gh(args):
    """Run `gh` and return (returncode, stdout, stderr). The seam the tests stub."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout, proc.stderr


def trusted_associations(repo):
    """The associations trusted on `repo`, decided by who owns it.

    An owner type other than `Organization` gets the narrower user-repo set, so
    an unexpected value fails toward trusting fewer authors.
    """
    code, out, err = run_gh(["api", f"repos/{repo}"])
    if code != 0:
        raise SystemExit(f"gh api failed for {repo}: {err.strip() or out.strip()}")
    owner_type = (json.loads(out or "{}").get("owner") or {}).get("type")
    return ORG_REPO_TRUSTED if owner_type == "Organization" else USER_REPO_TRUSTED


def fetch_issue(repo, number):
    code, out, err = run_gh(["api", f"repos/{repo}/issues/{number}"])
    if code != 0:
        raise SystemExit(
            f"gh api failed for {repo}#{number}: {err.strip() or out.strip()}"
        )
    return json.loads(out or "{}")


def classify(repo, numbers, allowed=None):
    """Split `numbers` into trusted and untrusted.

    A caller that classifies one issue at a time passes `allowed` from one
    `trusted_associations()` call, so the owner type is read once per run.
    """
    if allowed is None:
        allowed = trusted_associations(repo)
    trusted, untrusted = [], []
    for number in numbers:
        issue = fetch_issue(repo, number)
        association = issue.get("author_association") or "NONE"
        author = (issue.get("user") or {}).get("login") or ""
        if association in allowed:
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
