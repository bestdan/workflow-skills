---
description: Recommend which coder agent and model should execute a coding task — probes locally available backends (opus, codex, agy, devin) once into a cached config, scores the task against a capability matrix (correctness, speed, cost, context, creativity, autonomy, verification, data handling, containment), and returns ranked coder specs; usable standalone or as orchestrate-coders --coder arguments
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, WebSearch, WebFetch, AskUserQuestion, Skill
argument-hint: "<task description> [--plan <name>] [--refresh] [-n N]"
---

# Select Coder

Invoke the **select-coder** skill with the arguments as given. The skill
(`skills/select-coder/SKILL.md`) owns all behavior — the availability
pre-flight and its cache, the capability matrix and its refresh protocol,
task profiling, and the routing rules. This command adds nothing beyond
routing; do not re-derive or restate the workflow here.
Treat installed CLI coders as available delegates even though they are not
Claude Agent-tool subagents; the orchestrator dispatches them through Bash.

- `<task description>` — the task to route to a coder.
- `--plan <name>` — score each task file in `dev_docs/tasks/<name>_plan/`
  and emit one coder spec per packet.
- `--refresh` — force a re-probe of backend/model availability.
- `-n N` — top N candidates per task (default 3).
