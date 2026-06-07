---
name: break-down-task
description: Break a task that is too large for a single PR into smaller, PR-sized component tasks. Use when a task estimates larger than size 5, when /promote-tasks flags "scope exceeds size 5 — split into sub-tasks", or when the user says a task/card/ticket is "too big", "won't fit in one PR", or asks to "split", "slice", "break down", or "chunk up" existing work. Finds natural shear points (vertical/horizontal slices, preparatory refactors, interface-then-impl), and on a HIGH-confidence breakdown replaces the original task with the component tasks chained by is_blocked_by.
---

# break-down-task — slice a too-large task into PR-sized components

A task in the loop is supposed to be **one task = one PR** (size ≤ `5`, ~300 lines / ~5 files — see **Task size** in `skills/task/SKILL.md`). Sometimes a task that looked small grows, or `/promote-tasks` flags it `scope exceeds size 5 — split into sub-tasks`, or the user captured something that was always an epic. This skill takes one such over-sized task, looks for **shear points** — natural seams to cut along — and, when it can do so with high confidence, **removes the original task and writes the component tasks** in its place, chained by `is_blocked_by` so the loop runs them in order.

It is the resolution of the `/promote-tasks` size gate, and the sibling of `plan-with-docs`: `plan-with-docs` slices a _fresh_ idea into tasks; this skill slices an _existing_ task that turned out too big. Both emit canonical task cards (`skills/task/SKILL.md`) born `status: new`, so the output feeds the same `/promote-tasks` → `/do-tasks` pipeline.

## When to use

- A task estimates larger than size `5` (multi-layer rework, many unrelated files, several behaviors at once).
- `/promote-tasks` scored a card LOW with `scope exceeds size 5 — split into sub-tasks`.
- The user points at a specific task/card/ticket and says it's too big, won't fit one PR, or asks to split / slice / break down / chunk it.
- Mid-`/do-tasks`, the executor realizes the claimed task is too large to finish in one PR and bails for breakdown.

**Not** this skill: capturing brand-new follow-up work (`/add-task`), planning a feature from scratch (`plan-with-docs`), or scoring existing cards (`/promote-tasks`). If there is no concrete oversized task yet — just a big idea — use `plan-with-docs`.

## Resolve the handler first

The task store is handler-defined (`dev_docs/tasks/.task-config.yml`; absent → `repo-pr`). Read it the same way `/promote-tasks` does:

```bash
cat "$(git rev-parse --show-toplevel)/dev_docs/tasks/.task-config.yml" 2>/dev/null
```

- **`repo-pr`** (default, or file absent / no `handler:`) → the file path below. Fully supported.
- **`linear`** → the source task is a Linear issue. Do the same analysis, then create the components as **child issues** under the original (or convert the original into a parent/epic) and cancel the now-redundant original if it was a leaf. Use the Linear MCP tools the task commands already use (`list_teams`, `list_issues`, `save_issue`, `save_comment`). Honor the same HIGH/LOW gate — on LOW, leave the issue intact and comment the proposed split for a human.
- **`gh-issue` / `jira`** → **stop** with: "breakdown isn't automated for `<handler>`; split the issue in the tracker directly." (Same stance `/promote-tasks` takes for these.) You may still print the proposed slices so the human can paste them in.
- **Unknown value** → stop with: "Unknown task handler `<value>` in dev_docs/tasks/.task-config.yml. Run /task-config to fix it."

The rest of this skill describes the **`repo-pr` file path**; the analysis (shear points, confidence) is handler-independent.

## Identify the source task

1. **Locate it.** If the user named a slug, file, or issue, use that. Otherwise scan `dev_docs/tasks/**/*.md` and pick the candidate that triggered this — typically the one most recently flagged `needs_refinement` with a `# promoter: scope exceeds size 5` comment, or the one the user is discussing. If ambiguous, ask which task. Never guess and delete the wrong one.
2. **Skip epics.** A file with `type: epic` is a rollup, never a task — don't break it down (see **Epics** in `skills/task/SKILL.md`).
3. **Read it whole.** Frontmatter (`title`, `priority`, `size`, `impact`, `status`, `source_branch`, `source_pr`, `parent`, `is_blocked_by`, `tags`, `related_files`) and the full body (`## Context`, `## Task`, `## Acceptance Criteria`). Open the `related_files` if you need to judge real scope — the breakdown is only as good as your understanding of the work.

## Find the shear points

A **shear point** is a seam where the work naturally separates into pieces that can each ship as an independent, reviewable PR. You are looking for the cuts that yield the _fewest, largest_ slices that are each still ≤ size `5`. Prefer cuts that produce slices which are individually mergeable and leave the system working at each step.

Common seams, roughly in order of preference:

| Seam                              | Cut along…                                                    | Example                                                                    |
| --------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **Vertical slice**                | a thin end-to-end increment of one behavior                   | "Support CSV export" → first just one column set end-to-end, then the rest |
| **Preparatory refactor**          | a no-behavior-change refactor _first_, then the change on top | extract a helper / introduce a seam in PR 1, use it in PR 2                |
| **Interface then implementation** | define the contract (types, API shape, stub) then fill it in  | add the endpoint signature + types, then the handler logic                 |
| **Layer (horizontal)**            | architectural layer                                           | schema/migration → data access → API → UI, one PR each                     |
| **Module / file boundary**        | independent files or packages that don't share a diff         | "update all call sites" → batch by package                                 |
| **Read then write**               | the safe read/observe path before the mutating path           | add metrics/logging first, then act on them                                |
| **Happy path then edges**         | core case first, then error handling / edge cases             | implement the main flow, then validation + failure modes                   |
| **Behind a flag**                 | land dark behind a flag, then flip / clean up                 | ship gated code, then enable + remove the flag                             |

Rules for good slices:

- **Each slice is ≤ size `5`.** If a proposed slice still looks like `> 5`, slice _it_ again (recurse) or pick a finer seam.
- **MECE.** Together the slices cover the _entire_ original scope (no dropped work) with minimal overlap. Re-read the original `## Task` and `## Acceptance Criteria` and check every item maps into exactly one slice.
- **Clean dependency DAG.** Ordering between slices is expressed with `is_blocked_by`; there must be no cycle. A slice that depends on two earlier ones lists both (`is_blocked_by: [a, b]`).
- **Each slice keeps the build green.** Prefer cuts where every intermediate PR leaves tests passing and the app working. A "preparatory refactor first" cut is usually the way to achieve this.
- **Prefer few, fat slices.** Two size-`3`s beat five size-`1`s. Don't over-shatter — review overhead is real.

## Confidence gate

Score the proposed breakdown **HIGH** only if **all** hold:

- Every component plausibly fits size ≤ `5` (your judgment, weighing each slice's `## Task` steps and `related_files`).
- The slices are MECE against the original scope — nothing dropped, no significant overlap.
- The dependencies form an acyclic DAG and the order makes sense.
- The union of the components' Acceptance Criteria covers the original's Acceptance Criteria.
- You understood the original well enough to slice it (you weren't guessing at what the code does).

Otherwise it's **LOW**.

## Apply

### HIGH confidence → replace the original

1. **Present the proposed breakdown first** — a short numbered list (one line per component: title, size, what it covers, what it's blocked by) plus the seam you cut along. Removing the original is destructive (though git-recoverable), so let the user see the split before it lands. Proceed once they're good with it; if the user invoked this expecting an automatic split and the breakdown is clearly HIGH, a brief confirmation is enough — don't belabor it.
2. **Write the component task files.** One file per component, **canonical task format** (`skills/task/SKILL.md`), born `status: new`:
   - **Slugs must be globally unique** because `is_blocked_by` resolves by slug across `dev_docs/tasks/**/*.md`. Prefix every component with the original slug: `<original-slug>__<short-suffix>` (e.g. `csv-export__schema`, `csv-export__api`, `csv-export__ui`) or `<original-slug>_part_N`. Descriptive suffixes read better in `/list-tasks`. Check for collisions and bump if needed.
   - **Inherit** `priority`, `source_branch`, `source_pr`, `tags`, and `impact` from the original unless a slice clearly differs. Set `created` to today, `expires` to the original's (or 30 days out). Carry the original's `parent` if it had one; if the original was itself a meaningful grouping you may instead set each component's `parent` to the original slug and keep a thin epic — but don't invent an epic just to look tidy.
   - **`related_files`** scoped to _that slice_ only, not the union.
   - **`is_blocked_by`** encodes the order: the first slice has none; each later slice lists the slug(s) it depends on. If the original was itself `is_blocked_by` something upstream, the _first_ component inherits that blocker so the chain stays connected.
   - **Body:** `## Context` (carry the relevant background + a one-line "split from `<original-slug>`" note), a concrete `## Task` for just that slice, and `## Acceptance Criteria` (≥ 1 bullet) covering that slice. Split criteria into **Code-enforced** / **User-run** where useful, per `plan-with-docs`.
3. **Place the files.** Same directory as the original. If the original lived in a `plan-with-docs` plan dir (`<name>_plan/`), keep the components there and follow that plan's unique-numbering rule.
4. **Remove the original task file** (`git rm` or delete in the working tree). The components now carry all its scope.
5. **Don't commit, don't open a PR.** Like `/promote-tasks` and `plan-with-docs`, leave the changes in the working tree for the next git operation (the user, or `/do-tasks`) to pick up.
6. **Report.** Print the removed slug, the new component slugs with their `is_blocked_by` edges (a tiny dependency tree), and remind the user to run `/promote-tasks` to score the new cards and `/list-tasks` to see the board.

### LOW confidence → don't destroy anything

Never delete the original on LOW confidence. Instead:

- Leave the original file in place; set `status: needs_refinement` and `human_approval_requested: true` (add if missing).
- Append your **partial** proposal as an `## Open Questions` section in the original body — the candidate slices you found, where you got stuck (e.g. "couldn't tell if the migration must precede the API change"), and what a human needs to decide. The presence of `## Open Questions` keeps `/promote-tasks` from auto-promoting it until resolved.
- Report what's blocking a confident split so the human can answer, then re-run.

## Relationship to the rest of the loop

- **`/promote-tasks`** flags oversized cards (`scope exceeds size 5 — split into sub-tasks`) but never splits them — this skill is how that flag gets resolved.
- **`plan-with-docs`** uses the same slicing judgment and output format; reach for it when planning from scratch, this skill when an existing task outgrew one PR.
- **`/do-tasks`** runs the components once `/promote-tasks` flips them to `ready`; `is_blocked_by` keeps them in order.

## Legacy migration preflight

Before scanning, if a legacy `dev_docs/todos/` directory exists, run the **Legacy migration** prompt from `skills/task/SKILL.md` to move it to `dev_docs/tasks/`, then continue.
