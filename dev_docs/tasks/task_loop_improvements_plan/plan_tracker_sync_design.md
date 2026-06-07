# Design spike — local-first plan→tracker sync

> Part of [[task_loop_improvements_plan]]. Output of [[task_13]]; feeds [[task_14]].
> **Design only** — this document recommends an approach and re-splits the
> implementation. It changes no command or skill behavior.

## Problem

`plan-with-docs` always writes file-based tasks under
`dev_docs/tasks/<name>_plan/`, regardless of the handler configured in
`dev_docs/tasks/.task-config.yml`. So a team on Linear/Jira gets a split brain:
`/add-task` files single tasks to the tracker, but a whole **plan** lands as repo
files that the tracker never sees. The intended flow is **local-first**: draft and
vet the plan locally, then _explicitly_ push the vetted plan to the configured
tracker — never auto-sync on write.

This spike settles the five open questions so [[task_14]] can be built (or
re-split) without guessing.

---

## Recommendation at a glance

| Question          | Recommendation                                                                                                                                                                                                                                                      |
| ----------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Trigger**       | A new `/push-plan <name>` command that resolves the handler like `/add-task`/`/do-tasks`. `repo-pr` ⇒ no-op.                                                                                                                                                        |
| **Vetting gate**  | Every non-epic task file in the plan is `status: ready` **and** an explicit confirmation prompt listing what will be created. No new `vetted:` field.                                                                                                               |
| **Mapping**       | Overview epic → Linear **project** / Jira **epic** / gh-issue **milestone**. Each `task_N.md` → one issue via the existing handler add-flow. `is_blocked_by` translated by pushing in **topological order** and resolving slugs through a live slug→tracker-id map. |
| **Idempotency**   | Record the created id back into each file's frontmatter (`tracker_id`, optional `tracker_url`); the epic file records the project/epic/milestone id. Re-push is **create-missing-only** — a file with `tracker_id` is skipped. No separate manifest.                |
| **Reverse drift** | Out of scope to solve. Local files are **not** deleted; they keep their `tracker_id` as a traceable link. Tracker becomes the source of truth for status/execution.                                                                                                 |

---

## 1. Trigger — `/push-plan <name>` (recommended)

A new top-level command, dispatched off `.task-config.yml` exactly like
`/add-task`, `/list-tasks`, and `/do-tasks`:

- Resolve handler. `repo-pr` (or absent) ⇒ **no-op**: plans already live as files;
  print "plans already live as files for the repo-pr handler — nothing to push"
  and stop. This matches [[task_14]]'s acceptance criterion.
- `linear | jira | gh-issue` ⇒ run the push flow (section 3).

**Why a standalone command, not a flag or a prompt:**

- **vs. `plan-with-docs --push`** (rejected): coupling drafting and pushing fights
  local-first and breaks `plan-with-docs`'s existing contract ("Don't commit.
  Don't open PRs. Just write the files.", `skills/plan-with-docs/SKILL.md`). The
  push must be a deliberate, separate act _after_ vetting, which is iterative.
- **vs. an end-of-review prompt** (rejected): plan review (`plan-with-docs` step 8)
  is a back-and-forth; a plan is rarely "done" at the first stop. A push offered
  there fires too early, and a re-push later needs a command anyway — so the
  command is the primitive, and `plan-with-docs` can at most _mention_ it.
- A command is **re-runnable** (the idempotency story below depends on this),
  **discoverable** alongside the other task-loop verbs, and reuses the existing
  handler-dispatch pattern verbatim.

`plan-with-docs` step 7/8 gains a one-line pointer ("once vetted, run
`/push-plan <name>` to sync to the configured tracker"), nothing more.

## 2. Vetting gate — `status: ready` + explicit confirm

A plan is pushable when **every non-epic task file** in
`dev_docs/tasks/<name>_plan/**` is `status: ready`, and the user confirms a
summary of what will be created.

- `ready` is the existing "this card passed the human/promoter gate" signal
  (tasks are born `new`; `/promote-tasks` flips them to `ready`). Reusing it means
  "vetted" = "promoted + confirmed" — no new state to maintain.
- If any task is still `new`/`needs_refinement`, **stop** and point the user at
  `/promote-tasks` (or let them pass `--force` to push the ready subset — see
  open question O1).
- The epic file (`type: epic`) is exempt from the `ready` check (it is not a task).

**Rejected:** a dedicated `vetted: true` frontmatter field — it duplicates the
information already in `status`, and adds a field to keep in sync.

## 3. Mapping

### 3.1 Overview epic → tracker grouping container

| Handler  | Container     | How                                                                                                                                                                                                                                                  |
| -------- | ------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| linear   | **Project**   | `linear-add.md` already attaches issues to a project. Reuse `linear.default_project` if set; else create a project named after the epic `title` (or prompt, per `linear-add` step 2). Record its id on the epic file.                                |
| jira     | **Epic**      | Create an issue of type `Epic` (the overview `title`), then set each child ticket's `parent` to its key.                                                                                                                                             |
| gh-issue | **Milestone** | Closest native grouping. Create a milestone named after the epic and assign every child issue to it. (If milestone creation is judged out of scope for v1, fall back to a shared `plan:<name>` label and note the downgrade — see open question O2.) |

The plan's `parent: <name>` on each task maps to this container.

### 3.2 Each `task_N.md` → one tracker issue

Walk the plan's non-epic task files and create **one issue per task** by calling
the **existing handler add-flow** (`commands/handlers/linear-add.md`,
`jira.md`, `gh-issue.md`) with the task rendered as a "drafted task" (the
normalized contract in `commands/add-task.md` step 5). **Do not duplicate create
logic** — this is [[task_14]] step 2 and the whole reason the add-flows are
factored the way they are.

The push layer's job on top of the add-flow is only: ordering, slug→id
resolution, the grouping container, and recording ids back.

### 3.3 `is_blocked_by` (slug) → native blocker relationship

The plan encodes ordering as `is_blocked_by` pointing at task **filename stems**
(`<name>_task_1`). Trackers want native ids. Bridge it with two mechanisms:

1. **Push in topological order.** Sort the tasks so every blocker is created
   before its dependents. (`is_blocked_by` may be a string or a list — honor
   both, per the schema.) A cycle is a plan bug — stop and report it.
2. **Maintain a live slug→tracker-id map.** As each issue is created, record
   `<slug> → <tracker identifier>`. When creating a dependent, **translate its
   `is_blocked_by` slugs through the map** _before_ handing the drafted task to
   the add-flow, so the handler receives real tracker ids.

Per-handler translation (this is where the handlers differ, and why the
implementation splits):

| Handler  | Native blocker support                                                                                                                       | Action                                                                                                                                                                                                               |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| linear   | Yes — `save_issue` `blockedBy: [<identifier>]` (`linear-add.md` step 4 already does this **when `is_blocked_by` matches `/^[A-Z]+-\d+$/`**). | The push resolves the slug → the just-created Linear identifier via the map, so it now _does_ match and `linear-add` renders a native blocker. **No change to `linear-add.md` needed** — just feed it a resolved id. |
| jira     | `createJiraIssue` (`jira.md`) has **no** issue-link parameter today.                                                                         | Create all issues first, then a **second pass** adds "is blocked by" issue links via the Jira link API. Requires a new step in the jira path — supports splitting.                                                   |
| gh-issue | None native.                                                                                                                                 | Encode `Blocked by: #<number>` in the body footer (the add-flow already appends a footer) and optionally a task-list in the milestone/epic tracking issue. No hard dependency edge.                                  |

This is the single most important integration insight: **the push layer resolves
slugs to ids and reuses each handler's existing blocker handling**, rather than
each handler learning about plan slugs.

## 4. Idempotency / re-push — ids in frontmatter, create-missing-only

- After creating an issue, **write the tracker id back** into that task file's
  frontmatter: `tracker_id: <identifier>` (Linear `PRE-12`, Jira `PLAT-123`,
  gh-issue `owner/repo#45`) and optionally `tracker_url`. The epic file records
  the project/epic/milestone id the same way.
- **Re-push is create-missing-only:** a task with a non-empty `tracker_id` is
  **skipped** (no duplicate). Only files without one are created. This makes
  `/push-plan` safe to re-run after adding tasks to a plan, or after a partial
  failure mid-push.
- v1 **never updates or deletes** remote issues. A future `--update` could sync
  title/description/blocker edits; out of scope here.

**Why in-file ids, not a manifest:** the id lives next to the thing it
identifies, survives renames/moves, is human-visible in the file, and the
existing validator (`scripts/validate.py`) is **lenient about unknown keys** — it
already passes files with extra frontmatter — so adding the field breaks nothing.
A separate `.plan-sync.json` manifest would be a second source of truth that
drifts from the files it describes.

**Reconciliation of local edits after push:** out of scope for v1. Because
re-push is create-missing-only, editing a task body locally after it was pushed
has no remote effect until a future `--update`. Document this limitation rather
than half-solving it.

## 5. Reverse drift (noted, not solved)

Once pushed, the **tracker is the source of truth** for status and execution —
especially for Linear, where `/do-tasks` runs against the tracker, not the files.
Recommendation:

- **Do not delete** the local plan files (unlike the file handler's
  delete-on-PR-merge). The overview/epic stays useful as plan documentation, and
  each task keeps its `tracker_id` as a traceable back-link.
- Local `status:` becomes **stale** after push; treat the files as a historical
  snapshot. A future `/doctor` ([[task_17]]) check could flag "pushed plan with
  drifted local status" as hygiene. Out of scope here.

---

## Prerequisites & dependencies to flag

1. **Schema follow-up (a [[task_1]] follow-on).** The `tracker_id` / `tracker_url`
   fields (on task files **and** the epic file) are new frontmatter. The validator
   passes unknown keys today, so this is not a hard blocker — but a clean
   implementation adds an optional **string type-guard** for them in
   `scripts/validate.py` and documents them in the field reference / **Epics**
   section of `skills/task/SKILL.md`. Small, and it should land **first** so the
   push has a documented place to write ids.
2. **Value of pushing to gh-issue/jira is capped by `/do-tasks`.** Execution
   (`commands/do-tasks.md` section 3) is **Linear-only** — gh-issue/jira are
   "execution not supported". So a Linear push produces an immediately
   _executable_ board; a gh-issue/jira push produces a board you then work
   manually. This is **not** a blocker for the push itself, but it sets priority:
   **build the Linear path first.** (It also overlaps the gaps captured in PR #31 /
   `handler_parity_followups_plan`.)
3. **Jira issue-links and gh-issue milestones are new handler capabilities.**
   Native blocker links in Jira and milestone grouping in gh-issue are **not** in
   today's `jira.md` / `gh-issue.md` add-flows — implementing them is extra
   per-handler work. This is the main argument for splitting [[task_14]] by
   handler.

---

## Implementation breakdown — what [[task_14]] becomes

The per-handler differences (and the schema prerequisite) make the single
`size: 3` [[task_14]] card too big. Recommended re-split:

- **task_14a — Schema: `tracker_id` / `tracker_url` fields.** Add the optional
  string fields + validator type-guard; document them in the field reference and
  **Epics** section of `skills/task/SKILL.md`. _Prereq for the rest._ (size 2)
- **task_14b — `/push-plan` + repo-pr no-op + Linear path.** The command skeleton:
  handler resolve, vetting gate (§2), topological order + slug→id map (§3.3),
  create-missing-only idempotency (§4) writing ids back. Wire to `linear-add.md`
  (project-as-epic + resolved native `blockedBy`). `repo-pr` ⇒ no-op. Update
  `skills/plan-with-docs/SKILL.md` to document local-first → vet → push.
  _The high-value path — pairs with the existing `/do-tasks` Linear execution._
  (size 3, `is_blocked_by: task_14a`)
- **task_14c — gh-issue + jira push paths.** Milestone/epic grouping and the
  footer / Jira-issue-link blocker translation for the two trackers whose
  execution isn't automated. Lower priority; can trail 14b. (size 3,
  `is_blocked_by: task_14b`)

Recommended order: **14a → 14b → 14c.** Each is ≤ size 3 and one PR.

---

## Open questions for the user (O1–O2)

- **O1 — `--force` partial push?** Should `/push-plan` allow pushing only the
  `ready` subset of a partially-vetted plan (via `--force`), or hard-require the
  _whole_ plan be `ready`? Recommendation: hard-require by default, allow
  `--force` for the ready subset (still create-missing-only, so the rest can be
  pushed later once promoted).
- **O2 — gh-issue grouping grain.** Milestone (native, but the add-flow must learn
  to create/assign milestones) vs. a `plan:<name>` label (zero new capability, but
  weaker grouping). Recommendation: milestone in 14c; label as the documented
  fallback if milestone creation slips.
