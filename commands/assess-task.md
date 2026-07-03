---
description: Profile a coding task along stable dimensions — complexity, creativity, scope, autonomy, speed/cost sensitivity, verification criticality — and return a structured task_profile block plus a routing label, usable by select-coder (routing), break-down-task (sizing), and promote-tasks (confidence)
allowed-tools: Glob, Grep, Read, AskUserQuestion, Skill
argument-hint: "<task description> | --plan <name>"
---

# Assess Task

Invoke the **assess-task** skill with the arguments as given. The skill
(`skills/assess-task/SKILL.md`) owns all behavior — the `task_profile`
contract, the dimension rubric, and label derivation. This command adds
nothing beyond routing; do not re-derive or restate the rubric here.

- `<task description>` — the task to profile.
- `--plan <name>` — profile each task file in `dev_docs/tasks/<name>_plan/`
  and emit one `task_profile` block per file.
