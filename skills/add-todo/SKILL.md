---
name: add-todo
description: Capture follow-up work as a structured todo file in the local project's todo folder. Use when the user runs `/add-todo [description]` or otherwise wants to file a follow-up task for later processing.
allowed-tools: Bash(git *), Bash(date *), Glob, Grep, Read, Write
argument-hint: [description of the follow-up work]
---

# Add Todo

Capture follow-up work with full context as a local markdown file in the project's todo folder.

This skill **only files the todo**. It does not commit, push, open a PR, or do the work the todo describes. 

## Steps

### 1. Gather context

Collect automatically (run these in parallel):
- Current branch: `git rev-parse --abbrev-ref HEAD`
- Current diff summary: `git diff --stat HEAD`
- Today's date: `date +%Y-%m-%d`
- Expiry date (30 days): `date -v+30d +%Y-%m-%d` (macOS) or `date -d '+30 days' +%Y-%m-%d` (Linux)

When invoked as `/add-todo <description>`, `$ARGUMENTS` holds that description — use it as the title seed. If no arguments are given, ask the user what follow-up work they want to capture.

### 2. Determine the todo folder

Check whether the project already has a folder for todo work. Resolve the repo root with `git rev-parse --show-toplevel`, then use Glob to look for these folders, in priority order, and pick the first that exists:

`dev_docs/todos`, `dev_docs/todo`, `todos`, `todo`, `.todos`, `docs/todos`

- If one is found, use it.
- If none exist, default to `dev_docs/todos/` (Write creates it in step 7).

Use the resolved folder as `<todo_dir>` for the rest of the steps.

### 3. Generate the slug

From the title, create a kebab-case slug:
- Lowercase, strip filler words (the, a, an, for, in, on, at, to, of)
- Max 50 chars
- Example: "Remove stale zsh alias for foobar" -> `remove-stale-zsh-alias-foobar`

### 4. Check for slug collisions

Use Glob (pattern `<todo_dir>/<slug>.md`) to check whether a todo with this slug already exists. If a collision is found, append `-2`, `-3`, etc. until unique.

### 5. Draft the todo

Write the todo as a YAML frontmatter block followed by markdown sections.

Auto-populate these frontmatter fields:
- `created`: today's date (ISO format)
- `source_branch`: current branch
- `status`: `unclaimed`
- `expires`: 30 days from today
- `priority`: `low` (default, ask user if they want different)

From conversation context and diff, draft:
- `title`: from user description or `$ARGUMENTS`
- `size`: rough effort estimate — `xs`, `s`, `m`, `l`, or `xl` — inferred from the scope of the task and related files
- `related_files`: files from current diff or conversation that are relevant
- `tags`: infer from context (e.g., `cleanup`, `tests`, `docs`)
- **Context** section: why this work was noticed
- **Task** section: concrete steps to complete it
- **Acceptance Criteria**: definition of done

The resulting file looks like:

```markdown
---
title: Remove stale zsh alias for foobar
created: 2026-05-21
source_branch: danielegan/add-todo
status: unclaimed
expires: 2026-06-20
priority: low
size: s
related_files:
  - dotfiles/zsh/aliases.zsh
tags: [cleanup]
---

## Context

Noticed while editing aliases that `foobar` points at a binary that no longer exists.

## Task

- Remove the `foobar` alias from `aliases.zsh`.

## Acceptance Criteria

- `alias foobar` is gone and shell startup emits no errors.
```

### 6. Present for review

Show the user the full draft and ask for confirmation. They can adjust priority, add/remove files, or edit the task steps.

### 7. Write the file

After confirmation, write the drafted content to `<todo_dir>/<slug>.md`. Write creates the directory if it doesn't exist.

### 8. Confirm

Tell the user:
- The path of the file written (`<todo_dir>/<slug>.md`)
- That it's a local, uncommitted file available for later processing
