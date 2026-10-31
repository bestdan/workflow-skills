---
created: 2026-10-31
---

# Fleet view live acceptance, and the first Health snapshot

**Measured 2026-10-31** against the fleet instance on the Mac mini, serving
plugin 2.157.0, with the Mac mini, lindev and the MacBook each running a
collector. This is the record for [issue #1109](https://github.com/bestdan/workflow-skills/issues/1109). Its Health snapshot is the fleet view's starting point. How the instance works is in the live doc's
[Fleet instance](../task-dashboard.md#fleet-instance) section. Tailnet names, addresses and session content are left out because this repo is public.

## Checks

Two checks were not run: the owner chose to skip them, and they stay
unverified.

| Check                                                  | Result         | Evidence                                                                                    |
| ------------------------------------------------------ | -------------- | ------------------------------------------------------------------------------------------- |
| Mac mini: fleet install, tokens, tailnet link, hook on | verified       | the page answers on the tailnet name; the mini's own collector pushes every five minutes    |
| lindev: collector and hook                             | verified       | a fresh schema-5 snapshot every five minutes                                                |
| MacBook: collector and hook                            | verified       | first snapshot received 15 s after `install --collector-only` loaded the agent              |
| Machines: all three in the header, each fresh          | verified       | all three `last_snapshot_at` values within a minute of each other; the owner saw the header |
| Machines: the MacBook catches up after an hour asleep  | **unverified** | not run                                                                                     |
| Needs you: a lindev session ending with a question     | **unverified** | not run                                                                                     |
| Stale: a worktree whose PR merged, with its evidence   | verified       | the Close lane listed one, with the merge as its evidence                                   |
| Health: non-baseline approval and switch figures       | verified       | both have `baseline: false` in the snapshot below                                           |
| Health: wait figures are baseline under 14 days        | verified       | all four wait figures read "baseline: under 14 days of hook data", at 6.2 hook days         |

The Claude Code Remote adapter is off in the live config (`[claude_remote]
enabled = false`), so no check exercised it.

## Setup snags

Neither was a dashboard bug, so neither became an issue.

- **SSH from the MacBook hung silently at signing.** `ssh -v` stopped after
  `Server accepts key`: the agent was waiting for a user-presence confirmation
  that nothing on screen announced. Confirming it unblocked every later step.
- **The MacBook already had a dashboard config,** with `[jira]` off and
  `[github]` on. The `[collector]` table was appended to it rather than
  replacing it.

## First Health snapshot

From `dashboard.py health --since 2026-10-24 --json`, taken after the
MacBook's first snapshot. The window runs from 2026-10-24 through 2026-10-31 (the JSON's `until` of 2026-11-01 is its exclusive end), the
previous window starts 2026-10-16, and the hook had 6.2 days of data.

| Figure                                     | Source             |               Value | Previous | Baseline |
| ------------------------------------------ | ------------------ | ------------------: | -------: | -------- |
| Approval-shaped and bare "y"/"yes" prompts | prompt history     | 7.1 per 100 prompts |      9.1 | no       |
| Switch rate (prompts < 30 min apart)       | prompt history     |               60.9% |    67.8% | no       |
| Prompts with 3+ sessions active            | prompt history     |               74.0% |    88.3% | no       |
| Harness permission prompts                 | session-state hook | 3.8 per 100 prompts |        — | no       |
| Owner waiting on agents, median            | session-state hook |             0.5 min |        — | yes      |
| Owner waiting on agents, p90               | session-state hook |             6.5 min |        — | yes      |
| Agents waiting on owner, median            | session-state hook |             2.1 min |        — | yes      |
| Agents waiting on owner, p90               | session-state hook |            29.3 min |        — | yes      |
| Open PRs awaiting the owner                | GitHub             |                  90 |        — | no       |
| Issues opened : closed                     | GitHub             |       no source yet |        — | no       |
| Designs at proposed                        | GitHub             |                  10 |        — | no       |
| Human-review queue length                  | GitHub             |                1 PR |        — | no       |

The MacBook's first snapshot carried 8,223 prompt-history lines, so the
prompt-history figures shifted when it landed. With only the mini and lindev,
approval-shaped prompts read 6.1 per 100 and the switch rate 55.8%.

Every target but the review queue's is `observed`, because `[health.targets]` is empty. The review queue carries the built-in cap of 5, which 1 PR meets. No other figure is met or missed yet. "Issues opened : closed" has no source because the
GitHub sync mirrors only the issues that need a human.
