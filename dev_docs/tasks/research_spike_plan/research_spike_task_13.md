---
title: "research-spike: graduate the durable wisdom and delete this plan folder"
priority: low
size: 2
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/research_spike.md
  - dev_docs/designs/research_spike_skill.md
  - dev_docs/tasks/research_spike_plan/
is_blocked_by: research_spike_task_12
parent: research_spike
expires: 2026-09-30
tags: [research-spike, cleanup]
---

[[research_spike_plan]]

## Context

A `<name>_plan/` folder is temporary project-tracker scaffolding, not permanent
documentation. `dev_docs/tasks/` should hold only live scaffolding — never the
residue of finished plans. A plan is not done until its scaffolding is gone.

There is a second reason this task is not optional here. Task 5 makes
`decided_in:` reject any pointer into a `*_plan/` directory, precisely because
plan folders are deleted. If this plan's folder outlived the work, it would be
a standing counterexample to a rule the tool enforces.

## Task

- **Write `dev_docs/research_spike.md`** — the durable doc. It carries the
  final architecture (the script/LLM boundary, the on-disk convention, the
  record grammar's location), the load-bearing decisions **and their
  rationale**, and the gotchas a future maintainer needs:
  - why the ledger is stored rather than computed on demand;
  - why `suggest` can never fail a run (the 29-hit measurement);
  - why decision readiness is derived and never stored;
  - why both bridges route through receipt cards (URLs are not paths;
    `/push-plan` deletes plan directories);
  - the dprint/generated-block stability trap and how it is verified.
- **Keep `dev_docs/designs/research_spike_skill.md`.** It predates this plan
  and lives in the durable `designs/` tree; link it from the new doc rather
  than folding it in.
- **Delete `dev_docs/tasks/research_spike_plan/`** and any notes files this
  plan spawned.
- Fix any inbound links to the deleted paths (grep for
  `research_spike_plan` before finishing).

## Acceptance Criteria

**Code-enforced:**

- `dev_docs/tasks/research_spike_plan/` no longer exists; `grep -rn
  "research_spike_plan" . .gitignore` returns nothing outside git history.
  **Do not scope this grep to `--include='*.md'`** — that would skip
  `.gitignore`, which is the one file the leftover negation block lives in, so
  the check would pass while the orphan it exists to catch survives.
- `bash scripts/check.sh` green.
- `dev_docs/research_spike.md` exists and is linked from
  `skills/research-spike/SKILL.md`.

**User-run:**

- Read `dev_docs/research_spike.md` cold and confirm you could maintain the
  script from it without opening the design doc or this plan.

## Note on the .gitignore negation

`.gitignore:25` ignores `dev_docs/tasks/*`, so this plan folder carries the
scoped negation the `.gitignore` comment itself documents (plan decision 3):

```
# research_spike_plan is force-tracked while in flight so it is reviewable in
# its PR. research_spike_task_13 deletes these three lines together with the
# folder — a negation outliving its path is the orphaned reference that plan is
# about.
!dev_docs/tasks/research_spike_plan/
dev_docs/tasks/research_spike_plan/*
!dev_docs/tasks/research_spike_plan/*.md
```

**Delete that entire block — the four comment lines included, not just the
three rules — in this task, in the same commit that deletes the folder.**
Removing only the rules would leave comments naming a folder and a task that no
longer exist, which is precisely the orphaned reference this whole skill is
about. Seven lines go, or the cleanup has produced the defect it exists to
prevent.

If the plan is instead migrated with `/push-plan research_spike` before it
completes, that command deletes the local files at push time and the negation
should be removed then — leaving this task with only
`dev_docs/research_spike.md` to write.
