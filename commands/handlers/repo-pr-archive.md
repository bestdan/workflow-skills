# repo-pr handler — /archive-tasks flow

Invoked from `/archive-tasks` when `handler: repo-pr` is configured (the default
handler). Moves stale **`status: done`** task markdown files out of
`dev_docs/tasks/` into an archive directory so `/promote-tasks` and `/do-tasks`
scans stay fast and bounded. There is no external cap here — this is hygiene that
keeps the file-based scans small.

> **Preserve, never delete.** This handler **moves** files (with `git mv`), it
> does not `rm` them, so completed work stays in history and on disk. The
> archive dir is excluded from the command scans, not from the repo.

## What counts as terminal here

The `repo-pr` handler's terminal state is the file's `status: done` frontmatter.
But note the lifecycle wrinkle from `skills/task/SKILL.md`: the `repo-pr` handler
**deletes** a task file when its claim PR converts to the `task-loop` review PR,
so most completed work never leaves a `done` file behind — `done` is normally a
**PR-derived** signal, not a file. This archive step therefore only catches the
files that _do_ carry `status: done` on disk (e.g. tasks completed in place,
epic rollups marked `status: done`, or externally-set done files). That's the
correct, conservative scope: it never has to guess at PR state.

## Steps

1. **Ensure the archive dir exists.** The archive dir is fixed at
   `dev_docs/tasks/_archive/` (a constant, not configurable — the four task-file
   scans hardcode the same path, so it must not drift). Create it if needed:

   ```bash
   mkdir -p "$(git rev-parse --show-toplevel)/dev_docs/tasks/_archive"
   ```

2. **Find candidates.** Selection is deterministic and lives in
   `scripts/task-scan.py --archive-candidates --older-than N` (that script is the
   authority for this logic — do not re-derive it here):

   ```bash
   uv run "${CLAUDE_PLUGIN_ROOT}/scripts/task-scan.py" \
     --archive-candidates --older-than N \
     "$(git rev-parse --show-toplevel)/dev_docs/tasks"
   ```

   It scans `dev_docs/tasks/` (excluding `_archive/`), keeps only cards with
   `status: done`, and resolves each one's completion date with the same
   three-way fallback: `completed` if present, else the file's last git-commit
   date (`git log -1 --format=%cs -- <file>`), else (if that is empty too — the
   file is uncommitted/untracked) **today's date** — so a freshly written,
   not-yet-committed `done` file has age 0 and is conservatively left in place
   until it has a real date. It emits `candidates: [{slug, path, completion_date,
   completion_date_source, age_days}, ...]` for cards whose resolved date is
   more than `N` days before today. Never move a file in any non-`done` status,
   whatever its age.

3. **Always print the candidate list first** (path + completion date). If
   `dry-run`, stop here and report "nothing archived (dry-run)".

4. **Move them.** For each candidate, `git mv` it into the archive dir
   (preserving the filename), so it drops out of the `/promote-tasks` and
   `/do-tasks` scans (both already scan `dev_docs/tasks/` and will now also need
   to skip `_archive/` — they exclude it via the same `-not -path '*/_archive/*'`
   guard). Do **not** stage anything else, and do **not** commit — the next git
   operation picks up the moves, mirroring `/promote-tasks` step 3 and
   `/doctor --fix`.

5. **Report.** The count moved, the archive dir they landed in, and (dry-run) the
   candidate list with "nothing archived".

> **Scan exclusion.** The archive dir lives under `dev_docs/tasks/`, so any
> command that `find`s task files must exclude `_archive/`. `/promote-tasks` and
> `/do-tasks` (repo-pr path) scan with the `-not -path '*/_archive/*'` guard;
> keep that in sync if the default archive dir name changes.
