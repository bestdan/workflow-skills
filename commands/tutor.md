---
description: Teach the user until they genuinely understand the work — the problem and why it existed, the solution and why it took that shape, and what it impacts. Elicits their understanding first, closes gaps one at a time, quizzes to verify, and tracks mastery in a running checklist. Defaults to the current session; accepts a PR, a diff, a plan directory, or a subsystem.
allowed-tools: Bash, Glob, Grep, Read, Write, Edit, AskUserQuestion, WebFetch, Skill
argument-hint: "[--pr <N> | --diff [<ref>] | <path>]"
---

# Tutor

Invoke the **tutor** skill with the arguments as given. The skill
(`skills/tutor/SKILL.md`) owns all behavior — target resolution, curriculum
construction, the checklist doc, the elicit-diagnose-close-verify loop, the
quizzing rules, and the mastery bar. This command adds nothing beyond routing;
do not re-derive or restate the pedagogy here.

- _(no argument)_ — teach the work of the current session.
- `--pr <N>` — teach a pull request: its diff, description, reviews, and history.
- `--diff [<ref>]` — teach the working tree, or a git range the user names.
- `<path>` — teach a plan directory (`dev_docs/tasks/<name>_plan/`), a file, or a
  subsystem.

Do not end the session until every item on the checklist is verified.
