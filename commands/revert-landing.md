---
description: Undo a merged PR, usually a bad agent landing, by opening a revert PR of its squash commit, titled as a fix so it ships in the next release. Use on /revert-landing, or when the user asks to revert, roll back or undo a landed or merged PR.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/revert-landing.py":*), Bash(gh repo view:*)
argument-hint: "<pr> [--repo <owner/name>] [--reason <text>]"
---

# Revert Landing

Opens a revert PR for one merged PR. The script does the whole job and checks
its own result; this command runs it and reports what it printed. It never
merges the revert: that goes through review like any other PR.

`--repo` defaults to the current checkout's repository
(`gh repo view --json nameWithOwner --jq .nameWithOwner`). Pass `--reason` when
the user said why; it becomes a line of the revert PR's body.

Run it **unsandboxed** (it calls the GitHub API):

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/revert-landing.py" <pr> --repo <owner/name> [--reason "<text>"]
```

Show its output lines verbatim, then act on the exit code:

- **0**: the revert PR is open and its files mirror the original's. Give the
  user its link.
- **3**: the revert PR is open, but the `CHECK:` line says its diff does not
  simply undo the squash commit, or could not be read. Give the link and the
  `CHECK:` line, and say the revert needs reading before review: a later
  change to the same lines is the usual cause.
- **1**: no revert is open. Report the `REVERT: refused` reason. Do not open
  one by hand or with another tool.

A `NOTE:` saying the commit carries no `Landed-by: land-pr.sh` trailer means a
person merged it, not the lander. The revert is still opened; mention it.

`scripts/merge-audit.py --mode landed` lists the agent's landings and finds the
revert by its title, so don't retitle it. Why landing and its undo work this
way:
[`dev_docs/decisions/2026-10-03-lander-runs-in-the-session.md`](../dev_docs/decisions/2026-10-03-lander-runs-in-the-session.md).
