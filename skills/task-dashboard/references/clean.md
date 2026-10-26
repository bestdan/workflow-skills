# clean `<view>`: the cleanup procedure

Read by the task-dashboard skill's **clean** section. `DASH` is the
`dashboard.py` call that skill defines; `PLUGIN` is `${CLAUDE_PLUGIN_ROOT}`.

The view is a scan behind the machine: a collector snapshots every five
minutes, a failed repo scan keeps rows from the last good one, and the PR
mirror is as old as the last fleet sync. So the view says what to look at,
never what is true now. Every action below starts with a live check.

## 1. Read the view

```bash
DASH view <view> --json
```

Note `freshness`: a host whose `snapshot_at` is hours old, or whose
`scan_errors` is non-zero, gets nothing acted on alone; its items all go to
the owner. Work through the lanes in order. On Stale, **Close** holds the items
with evidence and **Check** the ones without; nothing in Check is acted on
alone, whatever the live check says.

## 2. What an agent does alone

Only a `worktree` item in Close, on **this** host (its `host` is this
machine's short host name), with `risks` empty. Before acting, re-check it
live, in this order, and stop at the first that fails:

1. The path still exists. If it is gone, there is nothing to do; say so.
2. The branch landed: `PLUGIN/scripts/branch-remove.sh --check <branch> <path>`,
   with the item's `branch` and `path`, exits 0. This is the merged-PR gate the teardown itself uses, so a PR
   merged into a stacked parent, or a tip that moved after the merge, fails
   here.
3. The tree is clean: `git -C <path> status --porcelain` prints nothing.
4. Nothing is unpushed: `git -C <path> log --oneline @{u}..` prints nothing
   (no upstream at all also counts as unpushed work: propose it instead).
5. No session works in it: no process has its cwd under the path
   (`lsof +D <path>` prints nothing).

Then remove it with the action's command, the plugin's own script, by its
absolute path: `PLUGIN/scripts/worktree-remove.sh <path>`. Never `rm -rf` a
worktree, and never `git worktree remove` it by hand.

Nothing else is done alone: not a handoff, a session, a PR or an issue, and
not a worktree in Check.

## 3. What an agent proposes

Everything else, as one list for the owner, grouped by the item's
`action.review` (or its command when it has no review), each line naming the
item, its host, its `why`, and what you would run. Run nothing from the list
until the owner says yes to it, item by item or group by group.

- **Another host's item** gets a script for the owner to run there, never a
  write over ssh: the live checks above, then the command, for each item,
  with `set -e` so a failed check stops it.
- **A handoff past its expiry**: read it first and say what it held, then
  propose deleting the file.
- **An idle session**: name what it was doing; the owner closes it.
- **A PR or an issue past the threshold**: say what it waits on, if the view
  or a `gh` read says.

## 4. Record "leave it"

When the owner says an item should stay, record it so the next cleanup does
not raise it again:

```bash
DASH overlay ignore --source <source> --key <key>
```

using the item's own `source` and `key`. When the owner says "not now"
instead, snooze it: `DASH overlay snooze --source <source> --key <key> --until
YYYY-MM-DD`. Both show on the page at once, and `DASH overlay unignore` undoes
an ignore.

## 5. Report

What was removed alone (with each live check's result), what was proposed and
what the owner decided, and what was ignored or snoozed. Read the view again
and give the new lane counts.
