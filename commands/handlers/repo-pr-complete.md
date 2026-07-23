# repo-pr handler — /complete-task flow

Invoked from `/complete-task <slug> [--dry-run]` when `handler: repo-pr` is
configured (or the config file is absent). This is a single **mechanical**
phase: given one already-identified task slug, flip its file's frontmatter to
`status: done`. It does **no PR discovery and no merge verification** —
`/complete-task`'s normal trust boundary applies unchanged.

For `repo-pr`, a merged PR is already the primary done signal, and the claim
protocol (`repo-pr-execute.md`) **deletes** the task file when its review PR
is readied — so most completed work never leaves a `done` file behind. This
handler exists only for the residual case `/do-tasks` has no verb for: a task
file still sitting in `dev_docs/tasks/` in a non-terminal `status:` whose work
landed some other way (folded into another PR, done by hand, superseded). If
you find yourself adding PR/merge lookups here, stop — that belongs one layer
up, and `repo-pr` doesn't even have a sweep to own it yet.

## Caller contract

Same fields as `linear-complete.md`'s caller contract:

- **`dry_run`** (bool) — print the plan and stop; no write.
- **`assume_verified`** (bool, default `false`) — when **true**, skip the
  interactive confirmation in step 3 and apply the transition directly. A
  bare manual `/complete-task` never sets this.
- **`comment_body`** (string, optional) — the completion note. `repo-pr` has
  no comment API, so this is appended under a `## Consumer Notes` section in
  the task file instead (created if absent). Omitted defaults to
  `Completed via /complete-task`; an explicit empty string (`""`) means
  **no note appended** — the same omitted-vs-empty distinction
  `linear-complete.md` uses.

## Steps

1. **Resolve the task file.** The identifier is the task **slug** (filename
   stem), matching how `/do-tasks <slug>` addresses `repo-pr` tasks. Slugs
   resolve globally by filename stem (see `skills/task/SKILL.md` and
   `scripts/task-scan.py`), so **Glob** `dev_docs/tasks/**/<slug>.md`,
   excluding anything under `dev_docs/tasks/_archive/`.
   - **No match** → idempotent, no write: "`<slug>`: no task file found —
     already done (merged/archived) or never existed." Do not treat a
     missing file as an error — `repo-pr` deletes task files on review-PR
     readying, so "gone" is the common done state.
   - **More than one match** (two subdirectories sharing a slug) → stop and
     report every matching path; do not guess which one.

2. **Read the frontmatter.** Parse the file's `status:` field.
   - `status: done` → "`<slug>` is already complete (`status: done`) — no
     change made."
   - Any other status → continue.

3. **`--dry-run` and confirmation** (mirrors `linear-complete.md` step 5):
   - **`dry_run: true`** → print the planned transition and **stop, no
     write**:

     ```
     <slug>: <current status> → done
     ```

   - **Otherwise, interactive + `assume_verified: false`** (the default
     manual path) → print the same line, then confirm via `AskUserQuestion`
     before proceeding to step 4. A "no" answer stops here with no write.
   - **`assume_verified: true`** → skip the confirmation entirely and
     proceed straight to step 4.

4. **Apply.** `Edit` the file's `status: <old>` frontmatter line to
   `status: done`. Touch nothing else in the frontmatter, and do **not**
   move the file — moving a `done` file out of `dev_docs/tasks/` is
   `/archive-tasks`'s job (see `repo-pr-archive.md`), not this one's.

5. **Note (optional, caller-supplied).** When `comment_body` is not the
   explicit empty string, append it under a `## Consumer Notes` section at
   the end of the file body — create the section if it doesn't already
   exist; if it does, append as a new paragraph rather than replacing what's
   there. Default to `Completed via /complete-task` when the parameter was
   omitted. Skip this step entirely when the caller passed `comment_body: ""`
   — the explicit no-note signal, per the caller contract.

6. **Report.** The slug, old status → `done`, and whether a note was
   appended (and its text, if short). On dry-run or the already-complete /
   no-file cases, report that instead — never claim a transition that didn't
   happen.
