---
name: plan-with-docs
description: Write a multi-step implementation plan as markdown files under dev_docs/todo/<name>_plan/ instead of printing it inline, then refine the plan through clarifying questions. Use when the user runs /plan-with-docs, asks to "plan X to files", or has just approved a plan in plan mode and wants it persisted. Default for any plan that is more than ~3 steps or spans multiple PRs.
---

# plan-with-docs — persist plans as markdown, then refine

Plan mode renders the plan inline via `ExitPlanMode` and that's fine for small plans. For larger projects this skill writes a structured set of markdown files instead, so the plan can be edited, committed, and worked through PR by PR. It also preserves the best part of plan mode — clarifying back-and-forth — by surfacing open questions after the files exist, so the user can refine the plan in place instead of restarting.

## Output layout

For a plan named `<name>` (snake_case, derived from the project/feature):

```
dev_docs/todo/<name>_plan/
  <name>_plan.md          # top-level overview + index of steps
  step_1.md               # one PR-sized task per file
  step_2.md
  ...
```

If steps naturally cluster into phases, nest them:

```
dev_docs/todo/<name>_plan/
  <name>_plan.md
  phase_1/
    step_1.md
    step_2.md
  phase_2/
    step_1.md
```

Use phases only when there are clear groupings (e.g. "backend → frontend → migration"). Don't invent phases to look organized.

## Steps

1. **Resolve `<name>`.**
   - If the user passed an argument, slugify it (lowercase, snake_case, no trailing `_plan` — the directory adds that).
   - Otherwise pick a short slug from the topic under discussion and confirm in one line.

2. **Confirm the working directory.** Files go under `<repo_root>/dev_docs/todo/<name>_plan/`. Find repo root with `git rev-parse --show-toplevel`. If not in a git repo, use the current working directory and say so. Create `dev_docs/todo/<name>_plan/` (and any missing parents) if it doesn't exist — `Write` handles parents, but `mkdir -p` first if you're shelling out.

3. **Check for collisions.** If `dev_docs/todo/<name>_plan/` already exists, list its contents and ask whether to **overwrite** (replace existing files), **append** (add new `step_N.md` files with the next available numbers, leaving existing files untouched and updating only the overview's index), or **pick a new name**. Don't silently clobber. When the existing plan uses `phase_N/` nesting, append defaults to the **last existing phase** unless the user says otherwise; numbering is per-folder (`phase_2/step_3.md` is independent of `phase_1/step_3.md`), so pick the next free integer within the chosen phase.

4. **Write `<name>_plan.md`** — the overview. Keep it tight:
   - **Goal:** one or two sentences. What we're building and why.
   - **Scope / non-goals:** explicit list of what's *not* in this plan.
   - **Approach:** the architectural choice and the main tradeoff considered.
   - **Steps:** numbered list, each linking to its `step_N.md` (or `phase_N/step_N.md`). One line per step describing the deliverable.
   - **Open questions:** anything the user still needs to decide. If empty, omit the section — but if you found nothing, double-check; most non-trivial plans have at least one real unknown.

5. **Write each `step_N.md`** as a single-PR-sized task:
   - **Title:** what the PR will do.
   - **Context:** the bare minimum a developer needs to start — relevant files (`path:line`), prior decisions, gotchas.
   - **Changes:** concrete edits / new files / commands. Concrete enough that someone (or another agent) can pick it up cold.
   - **Acceptance:** how we know it's done. Split into:
     - **Code-enforced:** automated tests to add or update, lint/type-check commands, CI checks expected to pass. Name the test files and the assertion in plain English.
     - **User-run:** manual checks that aren't automated — e.g. "open `localhost:3000/foo`, click Save, confirm toast appears", "run migration on staging snapshot, verify row count matches". Be explicit so the user knows what they have to do themselves.
   - **Dependencies:** which earlier steps must land first, if any.
   Skip sections that don't apply. Don't pad.

6. **Cross-link.** Each `step_N.md` links back to `<name>_plan.md` at the top. The overview links forward to each step.

7. **Report.** Print the directory tree of what was written and the absolute path to the overview. Don't re-print the plan body — the user can open the files.

8. **Review with the user.** Surface every open question and judgment call you noted while drafting — assumptions you made, alternatives you considered and rejected, gaps in your understanding of the codebase. Ask them one by one (or grouped if tightly related). Update the affected files in place as the user answers; don't make the user restart the planning round-trip. Stop when the user says the plan is good.

## Rules

- Obsidian-flavored markdown. Wikilinks (`[[step_1]]`) are fine when files are in the same folder; use relative paths (`phase_1/step_1.md`) across folders so they render in plain GitHub too.
- One step = one PR. If a step would be > ~300 lines of diff or touch > ~5 unrelated files, split it.
- No `dprint` post-processing inside this skill — the user runs that themselves.
- Don't commit. Don't open PRs. Just write the files.
- If invoked right after plan mode, treat the just-approved plan as the source material — but still run step 8. Plan-mode approval doesn't mean the plan has no open questions, just that the user wanted to exit plan mode.
- Asking clarifying questions is not optional. If you genuinely have none, say so explicitly in step 8 instead of skipping it.
- When in doubt about granularity, err toward more, smaller files. Easier to merge two than to split one.
