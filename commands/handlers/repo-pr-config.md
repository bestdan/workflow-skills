# repo-pr handler — /todo-config setup

The `repo-pr` handler is the default. It captures todos as markdown files via PR (works with `/process-todo` and `/list-todos`). There are no prerequisites to verify here — `gh` auth and git plumbing are exercised at the time of `/add-todo`, not at config time.

## Steps

1. **No verification.** Nothing to check at config time. (At `/add-todo` time, the handler will fall through `--remote` → `--subagent` → `--local` as needed; if `gh` auth is broken it lands on `--local` and stages into the current branch.)

2. **Mention the optional auto-merge workflow.** Tell the user that if they want `todo-add` PRs to land on `main` automatically without manual review, they can install a GitHub Action that auto-merges PRs with the `todo-add` label. This is optional; without it, `todo-add` PRs just queue up for normal review. A minimal workflow looks like:

   ```yaml
   # .github/workflows/todo-add-automerge.yml
   on:
     pull_request:
       types: [opened, labeled]
   jobs:
     automerge:
       if: contains(github.event.pull_request.labels.*.name, 'todo-add')
       runs-on: ubuntu-latest
       permissions: { pull-requests: write, contents: write }
       steps:
         - run: gh pr merge --auto --squash "$PR"
           env: { PR: "${{ github.event.pull_request.html_url }}", GH_TOKEN: "${{ secrets.GITHUB_TOKEN }}" }
   ```

   The user can paste this into their repo if they want it; do not write it for them from `/todo-config`.

3. **Return the config block** to `/todo-config` so it can write the file:

   ```yaml
   handler: repo-pr
   ```

   No nested block is needed — `repo-pr` has no configurable settings.
