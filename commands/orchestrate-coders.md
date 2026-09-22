---
description: Use when the user wants the current session to act as an orchestrator that farms coding work out to other coder agents rather than writing the code itself — e.g. "delegate this to codex", "have opus implement these", "orchestrate/supervise coders", "act as the supervisor and split this up", "don't write it yourself, farm it out", or /orchestrate-coders. The session decomposes the task into packets, dispatches each to a selected coder backend (opus subagent, codex, agy, devin, or a custom CLI), verifies the diffs, and integrates the results. Use it when the dispatching itself is the ask; when the ask is only which agent or model each task should go to, that is select-coder.
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, Agent, SendMessage, AskUserQuestion, Skill
argument-hint: "<task> [--coder <backend>[:<model>]]... [-n N] [--plan <name>]"
---

# Orchestrate Coders

Invoke the **orchestrate-coders** skill with the arguments as given. The skill
(`skills/orchestrate-coders/SKILL.md`) owns all behavior — coder-spec syntax,
config resolution, decomposition, dispatch, verification, integration, and the
per-backend mechanics in `skills/orchestrate-coders/backends/`. This command
adds nothing beyond routing; do not re-derive or restate the workflow here.
Do not limit delegation to Agent-tool subagents: `codex`, `agy`, `devin`, and
custom coders are dispatched by Bash through the skill's CLI backend contract.

- `<task>` — what to build; may be omitted when `--plan <name>` names an
  existing `dev_docs/tasks/<name>_plan/` plan to execute.
- `--coder <backend>[:<model>]` — repeatable; which coder(s) execute packets
  and, optionally, the model each runs. Omitted → the skill's config
  resolution decides.
- `-n N` — max packets in flight (default 3).
