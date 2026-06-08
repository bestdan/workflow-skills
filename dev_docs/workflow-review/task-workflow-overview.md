# The Task Workflow, End to End

_A plain-language tour of how `workflow-skills` turns "we should come back to
this" into a merged PR — written for someone meeting the system for the first
time._

---

## The one-sentence version

You capture follow-up work as you notice it, the system scores and prioritizes
it, and an autonomous agent picks up the ready work, does it on its own branch,
and opens a PR for review — all without losing the context of _why_ the task
existed in the first place.

It is a **repo-native kanban** that an AI agent can both **fill** and **drain**.

---

## The mental model: a 7-column kanban

Every piece of work is a card that moves left-to-right through seven columns.
Each move between columns is triggered by a specific command or event — nothing
moves by magic.

```
new ──▶ needs_refinement ◀──▶ ready ──▶ in_progress ──▶ needs_review ──▶ done
                                            │
                                            └──▶ blocked ──▶ in_progress

(expired: auto-pruned if the card's `expires` date passes before it ships)
```

| Column             | A card lands here when…                           | …and leaves when                       |
| ------------------ | ------------------------------------------------- | -------------------------------------- |
| `new`              | you capture it with `/add-task`                   | `/promote-tasks` scores it             |
| `needs_refinement` | the scorer wasn't confident, or a human paused it | a human edits it and marks it `ready`  |
| `ready`            | the scorer was confident                          | `/do-tasks` claims it                  |
| `in_progress`      | an agent claims it                                | the work is done and a review PR opens |
| `blocked`          | an agent/human hits a wall                        | the blocker clears                     |
| `needs_review`     | the agent opens its PR                            | the PR merges (or closes)              |
| `done`             | the PR merges                                     | — (terminal)                           |

The key idea: **a card is just a markdown file with YAML frontmatter** (or, if
you choose, a Linear issue). One card = one PR-sized chunk of work (≤ ~300 lines
across ≤ ~5 files). Anything bigger gets split first.

---

## The five everyday commands

| Command              | What it does                                                                    |
| -------------------- | ------------------------------------------------------------------------------- |
| **`/add-task`**      | Capture follow-up work _with full context_ and file it to the configured place. |
| **`/promote-tasks`** | Score every `new` card and promote the confident ones to `ready`.               |
| **`/do-tasks`**      | Pick up ready work, do it, and open a PR. The single "execute" verb.            |
| **`/list-tasks`**    | Render the whole board as a kanban view.                                        |
| **`/task-config`**   | Choose _where_ tasks live (repo files, GitHub Issues, Jira, or Linear).         |

Two more rounding out the set: **`/push-plan`** (sync a locally-drafted plan to
your tracker) and **`/doctor`** (diagnose and repair the setup).

---

## Walking through the lifecycle

### 1. Capture — `/add-task`

While you're deep in a feature branch, you spot a stale flag or a missing test.
Instead of context-switching, you say `/add-task`. The system:

- Auto-grabs the branch, the open PR, and the current diff,
- Drafts a structured card — title, priority, a **size estimate** (Fibonacci
  1/2/3/5), the relevant files, a _Context_ section ("written for someone who
  has never seen this code"), concrete _Task_ steps, and _Acceptance Criteria_,
- Shows it to you for a quick edit, then files it.

The card is born `status: new`. Crucially, capture preserves the _why_ — the
single most valuable thing usually lost when you defer work.

### 2. Refine — `/promote-tasks`

A card isn't executable until it's been vetted. `/promote-tasks` scans every
`new` card and runs a **confidence check**:

- Are all required fields present and is the size valid (1/2/3/5)?
- Does it have acceptance criteria and _no_ unresolved open questions?
- Is the priority not `urgent` (urgent is human-only)?
- **Does the described scope plausibly fit in one PR?** (model judgment, not a
  keyword scan — "migrate one constant" passes, "restructure the auth module"
  doesn't)

**HIGH confidence → `ready`.** **LOW → `needs_refinement`**, with a one-line note
on what failed, parked for a human. The promoter only ever touches `new` cards —
humans own everything downstream.

If a card is flagged "too big to fit one PR," the **`break-down-task`** skill
slices it along a natural seam (vertical slice, prep-refactor-first,
interface-then-implementation, …) into PR-sized sub-cards chained by
`is_blocked_by`.

### 3. Execute — `/do-tasks`

This is where the agent drains the board. `/do-tasks`:

- Filters `ready` cards down to the **dependency-ready** ones (every
  `is_blocked_by` blocker resolved),
- Ranks them: **priority → value/effort (`impact ÷ size`) → age**,
- **Claims** the top card (an atomic lock — see below),
- Does the work on its own branch, runs the project's tests/lints,
- Opens a PR and moves the card to `needs_review`.

Modes:

- `/do-tasks` — the single best card.
- `/do-tasks --all` / `-n N` — batch: each task to its **own cloud VM**, bounded
  by a **WIP limit** (default 3) so the human review queue never floods.
- Within a batch, **small tasks auto-execute, big ones are reserved** for a human
  (the `auto_execute_max_size` gate).
- `--local` runs in your current session; `--claim-only` / `--no-claim` split the
  claim and execute halves into composable steps.

### 4. Review → Done

The agent's PR sits in `needs_review`. A human (optionally aided by the
**`/co-review`** skill) reviews and merges. **Merge is the "done" signal** — for
the repo-file handler the card file is deleted when the PR opens, so the open and
merged PRs themselves carry the late-stage state.

---

## Where the work lives: handlers

The same capture-and-execute flow can deliver to four different "back ends,"
chosen once per repo with `/task-config` (stored in
`dev_docs/tasks/.task-config.yml`):

| Handler    | A task becomes…                              | Best for                            |
| ---------- | -------------------------------------------- | ----------------------------------- |
| `repo-pr`  | a markdown file committed via PR _(default)_ | self-contained, no external tooling |
| `linear`   | a Linear issue                               | teams already living in Linear      |
| `gh-issue` | a GitHub Issue                               | lightweight, GitHub-native          |
| `jira`     | a Jira work item                             | enterprise tracker shops            |

**Capability is jagged** — only `repo-pr` runs the _full_ loop today:

| Verb                       | repo-pr | gh-issue | jira | linear |
| -------------------------- | :-----: | :------: | :--: | :----: |
| capture (`/add-task`)      |   ✅    |    ✅    |  ✅  |   ✅   |
| list (`/list-tasks`)       |   ✅    |    ✅    |  ❌  |   ✅   |
| promote (`/promote-tasks`) |   ✅    |    ✅    |  ❌  |   ✅   |
| do — single (`/do-tasks`)  |   ✅    |    ❌    |  ❌  |   ✅   |
| process — batch (`--all`)  |   ✅    |    ❌    |  ❌  |   ❌   |

### The Linear path specifically

With `handler: linear`, the seven kanban columns map onto a team's standard
Linear workflow (`Backlog → Todo → In Progress → Done`) plus four labels — **no
custom states required**:

- `new` / `needs_refinement` → **Backlog** (the latter tagged
  `human-approval-requested`),
- `ready` → **Todo** (tagged `auto-eligible`),
- `in_progress` / `blocked` / `needs_review` → **In Progress** (or **In Review**),
- `done` → **Done**, set _only_ by Linear's GitHub integration on PR merge.

`/do-tasks` on Linear runs **in the current session (foreground)**: it pulls one
unstarted issue small enough to finish (`estimate < max_estimate`), judges "can I
finish this without a human?", claims it atomically (via an `auto-claimed` label
guard), branches using Linear's verbatim branch name, opens a PR with
`Closes <ID>`, and moves the issue to review. It never marks an issue Done itself
— merge does that.

---

## Planning bigger work

Two skills feed _into_ the same pipeline by emitting ordinary `new` cards:

- **`plan-with-docs`** — turns a fresh, multi-PR idea into a directory of
  PR-sized task files under `dev_docs/tasks/<name>_plan/`, with an **epic** file
  on top for rollup, and dependencies wired via `is_blocked_by`. You refine it
  through clarifying questions, then run `/promote-tasks` over it like any
  backlog.
- **`break-down-task`** — does the same slicing for an _existing_ card that grew
  too big.

A finished local plan can be pushed to your tracker with **`/push-plan`** (the
epic becomes a Linear project / Jira Epic / GitHub milestone; tasks are created in
dependency order; it's idempotent and create-missing-only).

---

## How parallel agents don't collide

Each remote agent runs in its own isolated VM with a fresh clone, so filesystem
races are impossible. The only real contention is **two agents claiming the same
`ready` card**. The lock is _not_ a branch name (which fails in branch-pinned
environments like Claude Code on the web) — it's an **open draft PR labeled
`task-claim` that names the slug**, visible to everyone via the GitHub API. The
claim protocol — pre-claim check → acquire → open the draft PR → reconcile
(lowest PR number wins) — closes the race deterministically with no external
locking service.

---

## The supporting cast

- **`/doctor`** — health-checks the setup (config validity, handler
  prerequisites, schema drift, expired-card hygiene); `--fix` applies safe
  mechanical repairs.
- **`/co-review`** — collaborative PR review that can pull in other local agents,
  reconcile against existing GitHub comments, and auto-fix high-confidence items.
- **`analysis-pipeline` / `review-facts`** — a separate family for _auditable_
  quantitative analysis (out of scope for the task loop, but part of the same
  plugin).

---

## TL;DR for a new user

1. Run `/task-config` once to pick where tasks live (or skip it — files are the
   default).
2. Say `/add-task` whenever you notice deferred work; it captures the context for
   you.
3. Run `/promote-tasks` to vet the backlog.
4. Run `/do-tasks` and let an agent turn ready work into reviewable PRs.
5. Review, merge — done.

The whole point: **never lose the "why," and let the boring, well-specified work
drain itself.**
