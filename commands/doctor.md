---
description: Diagnose and optionally fix the task-loop setup — config validity, handler prerequisites, legacy dirs, schema drift, and hygiene
allowed-tools: Bash(git *), Bash(gh *), Bash(cat *), Bash(find *), Bash(grep *), Bash(uv *), Bash(mkdir *), Bash(rmdir *), Glob, Grep, Read, Edit, Write, AskUserQuestion, mcp__claude_ai_Linear__list_teams, mcp__linear__list_teams, mcp__claude_ai_Atlassian__getAccessibleAtlassianResources, mcp__atlassian__getAccessibleAtlassianResources
argument-hint: "[--fix]"
---

# Doctor

One explicit "diagnose and fix my task-loop setup" entry point. It runs a set of
checks against `dev_docs/tasks/` and the configured handler, prints a
`PASS` / `WARN` / `FAIL` line per check with a remediation hint, and changes
**nothing** unless invoked with `--fix`.

`/doctor` does **not** replace the migrate-on-contact preflight that the other
commands run — that implicit auto-heal stays, so a stale setup keeps working
without anyone knowing `/doctor` exists. `/doctor` is the _explicit_ path: it
surfaces the same drift (and more) in one place and, with `--fix`, applies the
safe mechanical repairs. Both reference the single migration procedure in
`skills/task/SKILL.md`, so the logic lives in one place.

> **No auto-migrate preflight here.** Unlike `/list-tasks` and friends, `/doctor`
> must **not** silently migrate a legacy `dev_docs/todos/` dir before scanning —
> that would defeat the read-only-by-default contract. Legacy dirs are reported
> as the **Legacy dirs** check instead, and only migrated under `--fix`.

## Modes

- `/doctor` — run every check and report. Read-only: never writes, moves, or deletes.
- `/doctor --fix` — run every check, then apply the **safe, mechanical** fixes
  (run the legacy migration, prune expired tasks, fill defaulted fields). Judgment
  calls (unknown `handler:`, failing auth/MCP) stay reported as `WARN` — never
  auto-changed.

## Status vocabulary

- **PASS** — the check found nothing to do.
- **FAIL** — a definite problem with a clear mechanical fix. `--fix` repairs it.
- **WARN** — a problem that needs a human decision (unknown handler, failing
  auth, orphaned branches). Reported under both modes; `--fix` does **not** touch it.

## Steps

### 1. Resolve mode and config

Note whether `$ARGUMENTS` contains `--fix` (default: report-only). Then read the config:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

A missing file is not an error — it means the default `repo-pr` handler. Hold the
parsed `handler:` value (default `repo-pr`) for checks 1 and 2.

### 2. Run the checks

Run all checks regardless of individual outcomes (one bad check must not hide the
rest). Each produces one status line plus, on `WARN`/`FAIL`, a remediation hint.

**Check 1 — Config valid.** The `.task-config.yml` (if present) parses as YAML and
its `handler:` is a known value (`repo-pr` / `gh-issue` / `jira` / `linear`).

- Absent file, or `handler:` is a known value → `PASS`.
- Unparseable YAML → `WARN` (judgment call — never auto-rewritten): "`.task-config.yml`
  is not valid YAML — fix the syntax, or re-run `/task-config` to regenerate it."
- A parsed but unknown `handler:` value → `WARN`: "Unknown handler `<value>` — run
  `/task-config` to fix it."

Either WARN means Check 1 did **not** resolve a known handler — Check 2 keys off that.

**Check 2 — Handler prerequisites.** If Check 1 did **not** resolve a known handler
(invalid YAML or an unknown value), **skip this check and report `WARN`** ("handler
unresolved — fix Check 1 first") rather than defaulting to `repo-pr` prerequisites,
which would produce a misleading PASS/WARN. Otherwise, for the resolved handler,
verify the prerequisites that handler's own doc requires (reference, don't restate
the auth flows):

- `repo-pr` / `gh-issue` → `gh auth status 2>&1` must succeed (PR/issue creation
  needs it). On a TLS/x509/certificate error, note it's likely the sandbox blocking
  keychain access (see `commands/handlers/gh-issue-config.md` step 1).
- `linear` → the Linear MCP is reachable: call `<linear-mcp>__list_teams` and
  confirm it returns teams (see `commands/handlers/linear-common.md` → "Preflight
  pattern").
- `jira` → the Atlassian MCP is reachable: call
  `<atlassian-mcp>__getAccessibleAtlassianResources` and confirm it returns sites
  (see `commands/handlers/jira-config.md` step 1).

Met → `PASS`. Unmet → `WARN` (auth/MCP is a human action, never auto-fixed) with
the handler doc's own remediation.

**Check 3 — Legacy dirs.** A legacy `dev_docs/todos/` (task store) or `dev_docs/todo/`
(plans) directory is present.

- Neither present → `PASS`.
- Either present → `FAIL`: "Legacy `<dir>` found — migrate to `dev_docs/tasks/`."
  The fix is the **Legacy migration** procedure in `skills/task/SKILL.md` (reference
  it; do not restate the `git mv` steps). This is one check among many — not the
  command's reason to exist.

**Check 4 — Schema drift.** Task files whose frontmatter is invalid. This is **two
sub-checks** because they have different sources of truth:

1. **Present-but-invalid fields** — reuse `scripts/validate.py`'s rules (out-of-range
   `size`/`impact`, bad `status`/`priority`, malformed `is_blocked_by`, mistyped
   `assignee`/`parent`, epic-shape violations). Run it and surface its
   `dev_docs/tasks/**` failures:

   ```bash
   uv run "$(git rev-parse --show-toplevel)/scripts/validate.py" 2>&1
   ```

2. **Missing required fields** — `validate.py` is deliberately **lenient**: it only
   validates a field's shape _when present_ and never checks `expires` at all, so it
   does **not** report a card missing a required field. So `/doctor` must check this
   itself, against the **Field reference** in `skills/task/SKILL.md` (required:
   `title`, `priority`, `size`, `status`, `created`, `source_branch`, `related_files`,
   `expires`). Read each non-epic card's frontmatter and flag any required field that
   is absent.

Classify the combined findings:

- **Defaultable / mechanical** (a missing `expires` — default 30 days from `created`)
  → `FAIL`, fixable under `--fix`.
- **Needs judgment** (an out-of-range `size`, an unknown `status` value, a missing
  field with no safe default like `title` or `source_branch`) → `WARN`; point at the
  file and the rule.

No drift → `PASS`. If `uv` is unavailable, fall back to reading the frontmatter shape
rules from `scripts/validate.py` and applying them yourself.

**Check 5 — Hygiene.** Lower-stakes cruft:

- **Expired tasks** — files with `expires` < today while `status` is non-terminal
  (not `done`). These are pruning candidates (see the lifecycle note in
  `skills/task/SKILL.md`). `FAIL`, prunable under `--fix`.
- **Orphan branches / PRs** — cruft from aborted/crashed sessions. **Do not** use
  "open PR with no matching task file" as the signal: by design the task file is
  deleted when the claim PR is converted to the `task-loop` review PR, so an open
  `task-loop` PR _normally_ has no file (that's the `needs_review` queue — see
  `commands/list-tasks.md` step 4 and the PR-derived `needs_review` note in
  `skills/task/SKILL.md`). Flagging those would mark every legitimate in-review PR as
  an orphan. Instead surface:
  - **Stale `task-claim` PRs** — an open `task-claim` PR that never advanced to
    `task-loop` (work finished) or `task-blocked` (parked for a human) is a likely
    abandoned claim. Query it the same way the Claim protocol does and parse the slug
    from the **whole-line** `Claims-task: <slug>` marker (or `headRefName == task/<slug>`),
    not a substring — see the Claim protocol in `commands/handlers/repo-pr-execute.md`:
    `gh pr list --state open --label task-claim --limit 100 --json number,headRefName,body,updatedAt`.
    Surface ones untouched for a while (stale `updatedAt`).
  - **Merged-work local branches** — local `task/<slug>` branches whose PR has already
    merged or closed (`git branch --list 'task/*'` cross-checked against
    `gh pr list --state merged|closed`), safe to delete.

  `WARN` only (deleting an in-flight branch/claim is a judgment call) — list them and
  suggest manual cleanup; never auto-delete, even under `--fix`. Skip this bullet if
  `gh` is unavailable.

- **Config not excluded** (non-`repo-pr` handlers only) — for `gh-issue` / `jira` /
  `linear`, `dev_docs/tasks/.task-config.yml` is local config and belongs in the repo's
  local git exclude. If the file exists but `dev_docs/tasks/` is not listed in
  `.git/info/exclude` (`git check-ignore -q dev_docs/tasks/.task-config.yml` exits
  non-zero), flag it: `WARN`, fixable under `--fix` by appending `dev_docs/tasks/` to
  `$(git rev-parse --git-dir)/info/exclude`. **Skip the flag when the config is already
  tracked/committed** (`git ls-files --error-unmatch dev_docs/tasks/.task-config.yml`
  succeeds — the team chose to share it; excluding a tracked file is a no-op).
  **Skip entirely for `repo-pr`**, which commits task files under `dev_docs/tasks/` and
  must not exclude them — see the `repo-pr` caveat in `commands/task-config.md`.

Nothing found → `PASS`.

### 3. Report (and fix under `--fix`)

**Report-only (default).** Print the status block and stop — no writes:

```
/doctor — repo-pr handler

  PASS  Config valid
  PASS  Handler prerequisites (gh auth ok)
  FAIL  Legacy dirs — dev_docs/todos/ present → migrate to dev_docs/tasks/
  FAIL  Schema drift — 1 file missing `expires` (defaultable)
  WARN  Hygiene — 2 expired tasks; 1 orphan task/ branch

2 fail, 1 warn, 2 pass. Re-run with `/doctor --fix` to apply the mechanical fixes.
```

**`--fix`.** Apply only the **safe, mechanical** repairs, then re-print the block
with each fixed check marked `FIXED`:

- **Legacy dirs** → run the **Legacy migration** procedure from `skills/task/SKILL.md`
  (the same one the implicit preflight uses). Because `--fix` is the explicit
  opt-in, run it directly without the interactive `[migrate / skip once]` prompt.
- **Schema drift (defaultable only)** → fill the defaulted field with `Edit`
  (e.g. `expires` = `created` + 30 days). Leave out-of-range / unknown values as
  `WARN` for the human.
- **Hygiene → expired tasks** → these are non-terminal cards past `expires`; prune
  them (`git rm`) per the lifecycle. Confirm the list with `AskUserQuestion` before
  deleting if there are more than a handful, since a stale `expires` can hide live work.
- **Hygiene → config not excluded** → append `dev_docs/tasks/` to
  `$(git rev-parse --git-dir)/info/exclude` (skip for `repo-pr`, or if the config is
  tracked, per the check above).

Leave every `WARN` (unknown handler, failing auth/MCP, orphan branches) untouched
and still reported — those need a human. Do **not** stage or commit; the next git
operation picks up the changes (mirrors `/promote-tasks` step 3).

> If the check set later grows past the size budget, split **Hygiene** into a
> follow-on command — keep `/doctor` to config + prerequisites + legacy + schema drift.
