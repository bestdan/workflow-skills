---
title: "ops: create the dedicated spike test repository — a harmless target for probe fixtures"
priority: high
size: 1
status: new
created: 2026-07-21
source_branch: bestdan/autopilot-e-lite-design
assignee: bestdan
related_files:
  - dev_docs/auto-pilot-e-lite-design-2026-07-21.md
is_blocked_by:
parent: elite_stage0
tags: [e-lite, spike, stage-0, ops, user-run]
---

Plan: [[elite_stage0_plan]]

## Context

§0a: the spike runs from a disposable directory and a **dedicated test repository** — probe 1 dispatches a worker against it, probe 2's tmux fixtures want a harmless checkout, and probe 4 (GitHub authority canary, a later §7a probe whose tests Stage 1's gate reruns against the real App) will install the disposable test App on it. No production repo is ever a spike target.

## Task

- Create `bestdan/autopilot-spike-target` (or similar; e.g. `gh repo create bestdan/autopilot-spike-target --private`): private, trivial contents (README + a small script + a test runnable via a single command documented in the README), no branch protection initially (probe 4 adds rulesets later, per its own kill sheet).
- Cloning as the agent user is not part of this task: the first probe that needs the checkout ([[elite_stage0_task_3]]) clones it into a disposable spike directory (e.g. `/Users/agent/spike/`) — never `/Users/agent/work/`, which is the production execution layout (§0a: the spike runs from a disposable directory).
- Append the repo URL, visibility, and creation date to `dev_docs/elite-spike/environment.md`.

## Acceptance Criteria

- **User-run:** repo exists and is cloneable by the maintainer (`git clone git@github.com:bestdan/<name>` succeeds); URL, visibility, and creation date recorded in `dev_docs/elite-spike/environment.md`; contains README.md, one small script, and one test that passes via a single command documented in the README (e.g. `./test.sh` exits 0); no branch protection or rulesets configured (`gh api repos/bestdan/<name>/rulesets` returns an empty list and the default branch accepts a direct maintainer push).
