---
name: plan-with-docs
description: Write a multi-step implementation plan as markdown files under dev_docs/tasks/<name>_plan/ instead of printing it inline, then refine the plan through clarifying questions. Use when the user runs /plan-with-docs, asks to "plan X to files", or has just approved a plan in plan mode and wants it persisted. Default for any plan that is more than ~3 tasks or spans multiple PRs.
---

# plan-with-docs — persist plans as markdown, then refine

Plan mode renders the plan inline via `ExitPlanMode` and that's fine for small plans. For larger projects this skill writes a structured set of markdown files instead, so the plan can be edited, committed, and worked through PR by PR. It also preserves the best part of plan mode — clarifying back-and-forth — by surfacing open questions after the files exist, so the user can refine the plan in place instead of restarting.

A plan is a set of **tasks** in the same format the task loop uses (`skills/task/SKILL.md`). Each `<name>_task_N.md` is born `status: new`, so once the plan exists the user can run `/promote-tasks` and `/do-tasks` over it like any other backlog — plan output feeds the same pipeline.

> **A `<name>_plan/` folder is temporary project-tracker scaffolding, not permanent documentation.** It exists only while the plan is in flight. On a tracker handler, `/push-plan` migrates it out and deletes it at push time (below). On `repo-pr` the files persist as the source of truth — but only until the work is **done**. At plan completion, **graduate then delete**: move any durable wisdom (the final architecture, the load-bearing decisions and their rationale, gotchas a future dev needs) into a permanent top-level doc under `dev_docs/` (e.g. `dev_docs/<name>.md`), then delete the `<name>_plan/` folder and any completed-plan design/notes files it spawned. `dev_docs/tasks/` should hold only live scaffolding — never the residue of finished plans. Bake this cleanup in as the plan's own final task so it isn't forgotten.

> **Task files are prefixed with the plan `<name>` and numbered uniquely across the whole plan.** `is_blocked_by` resolves slugs (filename stems) **globally** across `dev_docs/tasks/**/*.md`, so a bare `task_1.md` collides with every other plan's `task_1.md`, and reused numbers across phases collide within one plan. Prefixing with `<name>` and never reusing a number keeps every slug unique; keep `<name>` short.

> **Legacy migration preflight.** Older versions wrote plans under `dev_docs/todo/`. Before writing, if a legacy `dev_docs/todo/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.

## Output layout

For a plan named `<name>` (snake_case, derived from the project/feature):

```
dev_docs/tasks/<name>_plan/
  <name>_plan.md          # top-level overview + index of tasks (the epic file, NOT a task itself)
  <name>_task_1.md        # one PR-sized task per file, canonical task format
  <name>_task_2.md
  ...
```

If tasks naturally cluster into phases, nest them — but **keep the task numbers unique across the whole plan**, not per-folder:

```
dev_docs/tasks/<name>_plan/
  <name>_plan.md
  phase_1/
    <name>_task_1.md
    <name>_task_2.md
  phase_2/
    <name>_task_3.md
```

Use phases only when there are clear groupings (e.g. "backend → frontend → migration"). Don't invent phases to look organized.

## Steps

1. **Resolve `<name>`.**
   - If the user passed an argument, slugify it (lowercase, snake_case, no trailing `_plan` — the directory adds that).
   - Otherwise pick a short slug from the topic under discussion and confirm in one line.

2. **Confirm the working directory.** Files go under `<repo_root>/dev_docs/tasks/<name>_plan/`. Find repo root with `git rev-parse --show-toplevel`. If not in a git repo, use the current working directory and say so. Create `dev_docs/tasks/<name>_plan/` (and any missing parents) if it doesn't exist — `Write` handles parents, but `mkdir -p` first if you're shelling out.
   - **Warn up front if the task files will be gitignored.** In a git repo (skip this in the non-git fallback above), before writing, check whether the plan path is excluded — anchored at the repo root, since that's where the files land, so the check is correct even when you're invoked from a subdirectory: `git -C "$(git rev-parse --show-toplevel)" check-ignore -q dev_docs/tasks/<name>_plan/<name>_task_1.md` (exit `0` = ignored). If it is, say so **now** rather than letting the user discover it later at commit time — on the `repo-pr` handler the files _are_ the source of truth and being gitignored silently blocks `git add`. Tell the user their options: keep them local-only (fine on a tracker handler, where `/push-plan` migrates them out anyway), un-ignore the path in `.gitignore`, or use `/push-plan` to sync to the configured tracker. Don't auto-edit `.gitignore` — surface it and let the user decide.

3. **Check for collisions.** If `dev_docs/tasks/<name>_plan/` already exists, list its contents and ask whether to **overwrite** (replace existing files), **append** (add new `<name>_task_N.md` files with the next available numbers, leaving existing files untouched and updating only the overview's index), or **pick a new name**. Don't silently clobber. When the existing plan uses `phase_N/` nesting, append defaults to the **last existing phase** unless the user says otherwise; numbering is **unique across the whole plan** (not per-folder — `phase_2/<name>_task_3.md` continues from `phase_1/<name>_task_2.md`), so pick the next free integer across all phases.

4. **Write `<name>_plan.md`** — the overview, written as the plan's **epic file** (see **Epics** in `skills/task/SKILL.md`). Give it epic frontmatter so `/list-tasks` can roll the plan's tasks up under it:

   ```yaml
   ---
   type: epic
   title: <human-readable plan title>
   status: active # active | done | abandoned
   owner: <the user, if known — else omit>
   created: <today, ISO date>
   ---
   ```

   The `type: epic` marker is what keeps `/promote-tasks` and `/do-tasks` from ever scoring or executing the overview as a task (it is the epic, not a task card). Below the frontmatter, keep the body tight:
   - **Goal:** one or two sentences. What we're building and why.
   - **Scope / non-goals:** explicit list of what's _not_ in this plan.
   - **Approach:** the architectural choice and the main tradeoff considered.
   - **Tasks:** numbered list, each linking to its `<name>_task_N.md` (or `phase_N/<name>_task_N.md`). One line per task describing the deliverable.
   - **Open questions:** anything the user still needs to decide. If empty, omit the section — but if you found nothing, double-check; most non-trivial plans have at least one real unknown.

5. **Write each `<name>_task_N.md`** in the **canonical task format** (see `skills/task/SKILL.md` for the full field reference) — a single-PR-sized task:
   - **Frontmatter:** `title`, `priority`, `size` (Fibonacci `1`/`2`/`3`/`5` — see **Task size**; if a task estimates `> 5`, split it), `status: new`, `created`, `source_branch` (the current branch), `related_files` (the files the task touches, `path:line` where useful), `is_blocked_by` (the slug of the earlier task this one depends on — the slug is the prerequisite's filename stem, e.g. `<name>_task_1`; this is how the plan's ordering is encoded), `parent: <name>` (the plan's epic slug, so the task rolls up under the overview — `<name>` is the plan name, the overview stem with `_plan` stripped), `expires`, `tags`.
   - **`## Context`:** the bare minimum a developer needs to start — relevant files, prior decisions, gotchas. Written for someone who has never seen this code.
   - **`## Task`:** concrete edits / new files / commands. Concrete enough that someone (or another agent) can pick it up cold.
   - **`## Acceptance Criteria`:** how we know it's done (≥ 1 bullet, required for promotion). Where useful, split into:
     - **Code-enforced:** automated tests to add or update, lint/type-check commands, CI checks expected to pass. Name the test files and the assertion in plain English.
     - **User-run:** manual checks that aren't automated — e.g. "open `localhost:3000/foo`, click Save, confirm toast appears", "run migration on staging snapshot, verify row count matches".

   Other optional fields are available when they help: `assignee` (who's accountable) and `impact` (Fibonacci value estimate `1`/`2`/`3`/`5`, mirroring `size`). Skip optional frontmatter that doesn't apply. Don't pad. (`parent` is set above to the plan epic; only override it if a task belongs to a different epic.)

6. **Cross-link.** Each `<name>_task_N.md` links back to `<name>_plan.md` at the top. The overview links forward to each task. Encode hard ordering with `is_blocked_by` (slug of the prerequisite task), not just prose.

7. **Report.** Print the directory tree of what was written and the absolute path to the overview. Don't re-print the plan body — the user can open the files. Mention they can run `/promote-tasks` to score the new tasks and `/list-tasks` to see the board. On a tracker handler (Linear/Jira/gh-issue), add a one-line pointer: once they're happy with the plan, `/push-plan <name>` migrates it to the configured tracker (local-first — the files are always written here first; pushing is a separate, explicit step) and then **deletes the migrated local files**, leaving the tracker as the only source of truth. (On the `repo-pr` handler `/push-plan` is a no-op — the files stay and _are_ the source of truth.)

8. **Review with the user.** Surface every open question and judgment call you noted while drafting — assumptions you made, alternatives you considered and rejected, gaps in your understanding of the codebase. Ask them one by one (or grouped if tightly related). Update the affected files in place as the user answers; don't make the user restart the planning round-trip. Stop when the user says the plan is good.

## Rules

- Obsidian-flavored markdown. Wikilinks (`[[<name>_task_1]]`) are fine when files are in the same folder; use relative paths (`phase_1/<name>_task_1.md`) across folders so they render in plain GitHub too.
- One task = one PR. Honor the **Task size** budget in `skills/task/SKILL.md`: if a task would exceed size `5` (~300 lines of diff or > ~5 unrelated files), split it into multiple tasks and chain them with `is_blocked_by`. (This skill slices a _fresh_ plan; when an _existing_ task card outgrows size `5`, the `break-down-task` skill — `skills/break-down-task/SKILL.md` — performs the same split on it.) When a task's size is hard to eyeball, `/assess-task` (`skills/assess-task/SKILL.md`) gives a structured `complexity` + `scope` read to inform the estimate — advisory only; your judgment sets the final `size`.
- No `dprint` post-processing inside this skill — the user runs that themselves.
- Don't commit. Don't open PRs. Just write the files. Plans are **local-first**: they always draft to files under `dev_docs/tasks/<name>_plan/`, regardless of the configured task handler. Syncing a vetted plan to an external tracker (Linear/Jira/gh-issue) is a separate, explicit act — `/push-plan <name>` — never an auto-sync on write. On a tracker handler a successful `/push-plan` writes the overview into the tracker container and then **deletes** the migrated local files, so the tracker becomes the only source of truth (the `repo-pr` handler is exempt — its files stay and _are_ the source of truth).
- If invoked right after plan mode, treat the just-approved plan as the source material — but still run step 8. Plan-mode approval doesn't mean the plan has no open questions, just that the user wanted to exit plan mode.
- Asking clarifying questions is not optional. If you genuinely have none, say so explicitly in step 8 instead of skipping it.
- When in doubt about granularity, err toward more, smaller files. Easier to merge two than to split one.
- **Make the plan's last task the graduate-then-delete cleanup** (see the plan-lifecycle note near the top): a final task, `is_blocked_by` the plan's terminal task(s), that migrates the durable architecture/decisions into a permanent `dev_docs/<name>.md` and deletes the temporary `<name>_plan/` folder and any design/notes files the plan spawned. A plan isn't done until its scaffolding is gone.
