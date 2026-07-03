---
description: Orchestrate coding work across coder agents — the session decomposes the task into packets, dispatches each to a selected coder backend (opus subagent, codex, agy, devin, or a custom CLI), verifies the diffs, and integrates the results
allowed-tools: Bash(git *), Bash(cat *), Bash(command -v *), Glob, Grep, Read, Write, Edit, Agent, AskUserQuestion, Skill
argument-hint: "<task> [--coder <backend>[:<model>]]... [-n N] [--plan <name>]"
---

# Orchestrate Coders

Invoke the **orchestrate-coders** skill with the arguments as given. The skill
(`skills/orchestrate-coders/SKILL.md`) owns all behavior — coder-spec syntax,
config resolution, decomposition, dispatch, verification, integration, and the
per-backend mechanics in `skills/orchestrate-coders/backends/`. This command
adds nothing beyond routing; do not re-derive or restate the workflow here.

- `<task>` — what to build; may be omitted when `--plan <name>` names an
  existing `dev_docs/tasks/<name>_plan/` plan to execute.
- `--coder <backend>[:<model>]` — repeatable; which coder(s) execute packets
  and, optionally, the model each runs. Omitted → the skill's config
  resolution decides.
- `-n N` — max packets in flight (default 3).
