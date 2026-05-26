# repo-pr handler — /todo-config setup

The `repo-pr` handler is the default. It captures todos as markdown files via PR (works with `/process-todo` and `/list-todos`). There are no prerequisites to verify here — `gh` auth and git plumbing are exercised at the time of `/add-todo`, not at config time.

## Steps

1. **No verification.** Nothing to check at config time. (At `/add-todo` time, the handler will fall through `--remote` → `--subagent` → `--local` as needed; if `gh` auth is broken it lands on `--local` and stages into the current branch.)

2. **Mention the optional auto-merge workflow.** Tell the user that if they want `todo-add` PRs to land automatically, they can install the auto-merge GitHub Action — see `README.md` for the snippet. This is optional; without it, `todo-add` PRs just queue up for normal review.

3. **Return the config block** to `/todo-config` so it can write the file:

   ```yaml
   handler: repo-pr
   ```

   No nested block is needed — `repo-pr` has no configurable settings.
