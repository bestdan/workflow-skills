---
created: 2026-09-21
---

# Can the nightly gh-issue routine run in the cloud? Not yet — `gh` is absent

**Measured 2026-09-21** inside a scheduled routine on environment
`env_01KURKZo3LcfRKBaEZWcbsrk` (`Linear Tidy Routine`), session
`cse_011b38RxrvfmYvwiKYRTHWs4`, against the `bestdan/nightly-gh-issue-routine`
branch. A dated snapshot: it records what was true that day and is allowed to go
stale.

## The question

[`dev_docs/nightly-gh-issue-routine.md`](../nightly-gh-issue-routine.md) stands up a
nightly routine over this repo's board. Its step 0 assertion 4 turns the whole run on
whether `gh` can read the repo, because every label write in the handler shells out to
the CLI (`commands/handlers/assets/gh-issue-state.py`). `gh` had been measured absent
here on 2026-08-24 and present in two of five runs of an earlier routine, unexplained.
This probe settles it for this environment.

Read-only: nine checks, no `--apply`, no label write, no issue or PR touched, and the
one push was `--dry-run`.

## What was measured

| # | Command                                                        | Exit  | Verbatim result                                               |
| - | -------------------------------------------------------------- | ----- | ------------------------------------------------------------- |
| 1 | `claude plugin list`                                           | 0     | `workflow-skills@workflow-skills Version: 2.66.0 … √ enabled` |
| 2 | `command -v gh` / `gh --version`                               | 1/127 | `/bin/bash: line 1: gh: command not found`                    |
| 3 | `gh auth status`                                               | 127   | `/bin/bash: line 1: gh: command not found`                    |
| 4 | `gh issue list --repo bestdan/workflow-skills`                 | 127   | `/bin/bash: line 1: gh: command not found`                    |
| 5 | `gh label list --repo bestdan/workflow-skills`                 | 127   | `/bin/bash: line 1: gh: command not found`                    |
| 6 | `mcp__github__get_me`                                          | —     | `{"login":"bestdan","id":2766380, …}`                         |
| 7 | `push --dry-run origin HEAD:refs/heads/zz-probe-never-created` | 0     | `* [new branch] HEAD -> zz-probe-never-created`               |
| 8 | `echo TMPDIR / CLAUDE_PLUGIN_ROOT`, `ls -d /tmp/claude`        | 0/2   | `TMPDIR=[] PLUGIN_ROOT=[]`, `No such file or directory`       |
| 9 | `python3 --version`                                            | 0     | `Python 3.11.15`                                              |

## The three verdicts

- **The plugin's verbs can run here** (check 1). The environment's setup script
  installs and enables `workflow-skills` before the agent starts, at the version
  `main` was on. `agent-guidance` and `papercuts` come with it.
- **Labels cannot be written here** (checks 2–5). `gh` is not on the box at all — this
  is `command not found`, not a credential or proxy refusal, so nothing about tokens or
  the egress proxy is being measured. Every `status:`/`auto:`/`prio:`/`est:` transition
  the handler makes goes through it.
- **Work could be pushed back here** (check 7). Push auth resolves and the dry run
  reports the ref it _would_ create. Nothing was created; `zz-probe-never-created` does
  not exist. This closes the second of the two risks the runbook opened, and it closes
  it in the good direction.

## What this means for the routine

Assertion 4 fires, so the nightly stops before step 1 and does nothing, every night.
**That is the preflight working, not failing** — the alternative it was written to
prevent is a run that improvises label writes over another channel and leaves an issue
claimed, in the wrong rung, with a PR nobody is watching.

Three things follow, and none of them is "retry it":

1. **`gh` being absent is not the same finding as the 403s** in
   [`2026-09-07-cloud-routine-plugins-and-gh.md`](2026-09-07-cloud-routine-plugins-and-gh.md)
   and [`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md).
   Those measured what a _present_ `gh` could reach through the proxy. Installing `gh`
   in the setup script would make those findings load-bearing again — it does not
   follow from this record that a present `gh` would work.
2. **The raw-REST channel is measured permissive for this write.** A routine `PATCH`ed
   an issue's labels over `curl` and got `HTTP 200` (2026-09-17, recorded in the
   claim-channel file). `gh-issue-state.py` already validates the full label set locally
   against `labels.yml` before any network call, so the enum guarantee the CLI provides
   is not the only one available — which makes a REST channel in that helper a coherent
   option rather than a workaround. It is unbuilt.
3. **`/git/refs` stays closed** (403, measured twice), so the claim lock remains the
   comment-token election whatever happens to the label channel.
   [`commands/handlers/claim-lock.md`](../../commands/handlers/claim-lock.md) owns that
   rule and is unaffected by this record.

## What this record does not say

It does not say `gh` is absent from cloud routines in general — it says it was absent
from this environment on this date, which is the third data point in a series that has
also seen it present. It does not say the setup script cannot install it. And it says
nothing about whether an installed `gh` would authenticate, which is a separate
measurement nobody has made.
