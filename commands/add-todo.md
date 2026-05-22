---
description: Capture follow-up work as a structured todo, then deliver it to the configured destination (repo PR, GitHub issue, or Jira)
allowed-tools: Bash(git *), Bash(gh *), Bash(claude *), Bash(date *), Bash(cat *), Bash(find *), Bash(mkdir *), Glob, Grep, Read, Agent, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__claude_ai_Atlassian__searchJiraIssuesUsingJql, mcp__claude_ai_Atlassian__createJiraIssue
argument-hint: [description of the follow-up work]
---

# Add Todo

Capture follow-up work with full context, then deliver it to the destination configured for this repo. Capture is destination-agnostic; the **handler** resolved from `dev_docs/todos/.todo-config.yml` decides where the todo lands. With no config, the default `repo-pr` handler reproduces the original behavior: dispatch an agent to commit the todo file on a branch from main and open a PR, without touching local state.

## Steps

### 1. Gather context

Collect automatically (run these in parallel):
- Current branch: `git rev-parse --abbrev-ref HEAD`
- Repo name: `gh repo view --json nameWithOwner --jq .nameWithOwner`
- Open PR for this branch (if any): `gh pr view --json number --jq .number 2>/dev/null`
- Current diff summary: `git diff --stat HEAD`
- Today's date: `date +%Y-%m-%d`
- Expiry date (30 days): `date -v+30d +%Y-%m-%d` (macOS) or `date -d '+30 days' +%Y-%m-%d` (Linux)

If `$ARGUMENTS` is provided, use it as the title seed. Otherwise, ask the user what follow-up work they want to capture.

### 2. Generate the slug

From the title, create a kebab-case slug:
- Lowercase, strip filler words (the, a, an, for, in, on, at, to, of)
- Max 50 chars
- Example: "Remove stale zsh alias for foobar" -> `remove-stale-zsh-alias-foobar`

### 3. Check for slug collisions

Check if a todo with this slug already exists:

```bash
find "$(git rev-parse --show-toplevel)/dev_docs/todos" -name '<slug>.md' -type f 2>/dev/null
```

If a collision is found, append `-2`, `-3`, etc. until unique.

### 4. Draft the todo

Auto-populate these fields:
- `created`: today's date (ISO format)
- `source_branch`: current branch
- `source_pr`: PR number if one exists for this branch
- `status`: `unclaimed`
- `expires`: 30 days from today
- `priority`: `low` (default, ask user if they want different)
- `size`: estimated task size — `small` / `medium` / `large` (infer from scope, ask user to confirm)

From conversation context and diff, draft:
- `title`: from user description or `$ARGUMENTS`
- `related_files`: files from current diff or conversation that are relevant
- `tags`: infer from context (e.g., `cleanup`, `tests`, `docs`)
- **Context** section: why this work was noticed
- **Task** section: concrete steps to complete it
- **Acceptance Criteria**: definition of done

### 5. Present for review

Show the user the full draft and ask for confirmation. They can adjust priority, size, add/remove files, or edit the task steps.

If the resolved handler (step 6) is `repo-pr`, also ask: **"File for later, or fix now?"**
- **File for later** (default): creates the todo file on main for `/process-todo` to pick up
- **Fix now**: creates the todo file AND immediately dispatches a processing agent to do the work

(Other handlers deliver to an external tracker and have no fix-now option.)

### The drafted todo (handler input)

Once the user confirms, you hold a normalized **drafted todo** that every handler consumes. This is the stable contract between capture and delivery:

| Field           | Description                                                         |
| --------------- | ------------------------------------------------------------------- |
| `title`         | Imperative, < 80 chars                                              |
| `body`          | The Context / Task / Acceptance Criteria markdown                   |
| `priority`      | `low` / `medium` / `high`                                           |
| `size`          | `small` / `medium` / `large` — estimated task size                  |
| `tags`          | List of freeform tags                                               |
| `slug`          | Kebab-case slug from step 2                                         |
| `created`       | ISO date                                                            |
| `expires`       | ISO date                                                            |
| `source_branch` | Branch where the todo was identified                               |
| `source_pr`     | PR number for that branch, if any                                  |
| `related_files` | Paths relevant to the work                                          |

Every handler **must report back the URL of the artifact it created** (PR, issue, or work item) so step 8 can show it.

### 6. Resolve the handler

The destination is configurable. Read the repo config:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/todos/.todo-config.yml" 2>/dev/null
```

Resolve the handler name:
- File absent, or no `handler:` key → **`repo-pr`** (the default — preserves the original behavior).
- `handler: repo-pr | gh-issue | jira` → use that handler.
- Any other (unknown) value → **stop** and tell the user: "Unknown todo handler `<value>` in dev_docs/todos/.todo-config.yml. Valid values: repo-pr, gh-issue, jira. Run /todo-config to set it." Do not silently fall back.

### 7. Deliver via the handler

Each handler's full instructions live in a sibling file: `commands/handlers/<handler>.md`. Use the **Read** tool to load only the resolved handler's file, then follow it, passing the drafted todo from step 5. The handler file owns everything about how the todo lands — preflight checks, dispatch, parsing, and the artifact URL it returns.

If a relative path doesn't resolve, find the file with **Glob** (`**/commands/handlers/<handler>.md`) and Read the result.

Do not embed handler logic here; do not read handler files for handlers other than the resolved one.

### 8. Report

Tell the user which handler ran and the artifact URL it returned (PR / issue / work item). For `repo-pr`, also include what was dispatched (file only, or file + processing) and the dispatch mode used, and that they can monitor with `/tasks`.

