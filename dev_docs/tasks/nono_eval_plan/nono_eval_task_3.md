---
title: "nono evaluation — apply the decision rule, graduate findings, delete scaffolding"
priority: medium
size: 1
status: new
created: 2026-07-22
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/nono-evaluation.md
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by: [nono_eval_task_1, nono_eval_task_2]
parent: nono_eval
tags: [e-lite, nono, spike, cleanup]
---

Plan: [[nono_eval_plan]]

## Context

Closes the evaluation. Tasks 1–2 produced up to six measurement rows; §6 of [../../../nono-evaluation.md](../../../nono-evaluation.md) maps their pattern to one of four adoption tiers (full / degraded / network-only / reject), and §7 says exactly which design sections a pass may edit — **only** §2.1, §3.2, Risk #2, Decision #1, plus a dependency note. It changes nothing in §0/§4/§5/§5.3 or the §7a substrate probes. (If task 1 rejected, tasks 2's rows are absent and the reject branch applies directly.)

Per the plan-lifecycle rule, a plan isn't done until its scaffolding is gone: graduate durable findings, then delete this `nono_eval_plan/` folder.

## Task

- **Apply the decision rule (§6).** From the measurement rows, determine the adoption tier and record it as a short decision block appended to `dev_docs/nono-evaluation.md` (result per falsifier → tier → which design edits follow).
- **Edit the design accordingly (§7), if adopting at any tier:** update `dev_docs/auto-pilot-e-lite-design-2026-07-21.md` §3.2 (delete or narrow the `gh` hole), Risk #2 (downgrade), Decision #1 / §2.1 (bearer readability per the achieved tier), and add the nono dependency note (pinned version, CA handling, proxy-as-maintainer-owned-parent). On **reject**, instead record in §3.2/Risk #2 that nono was evaluated and did not fit, and why — so no one rebuilds the containment layer around a capability it lacks. Leave the existing `nono-evaluation.md` pointers in §3.2 and Risk #2 pointing at the (now decision-stamped) probe doc.
- **Graduate + delete.** Ensure the durable outcome lives in `dev_docs/nono-evaluation.md` (the permanent home — it already sits at top level, so no separate `dev_docs/<name>.md` is needed). Then delete the `dev_docs/tasks/nono_eval_plan/` folder.

## Acceptance Criteria

- **Code-enforced:** `dev_docs/tasks/nono_eval_plan/` no longer exists; `git status` shows the deletion staged.
- **User-run:** `dev_docs/nono-evaluation.md` carries the decision block naming the adopted tier (or the reject rationale); on adoption, §3.2 / Risk #2 / Decision #1 / §2.1 of the design reflect the tier and a nono dependency note exists; on reject, §3.2 / Risk #2 record that nono was evaluated and did not fit; no edits leaked into §0/§4/§5/§5.3 or the §7a probes.
