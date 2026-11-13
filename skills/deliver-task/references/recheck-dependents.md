# Re-checking dependents

A task that completes releases the items it was blocking, and those stay
un-scored until a promote run reaches them. `/deliver-task` runs that promote
run itself, narrowed to the task's own dependents: `/promote-tasks after=<id>`.
It runs at one of two points, depending on where the handler records a
completion.

Both follow `commands/promote-tasks.md` with `after=<id>` inline; if the relative
path doesn't resolve, find it with **Glob** (`**/commands/promote-tasks.md`). That
file decides which handlers support the mode and what the run may write. The
run asks nothing, and every gate of a full promote run still applies: a
dependent with another open blocker stays held, and a `human`-tier dependent's
Intent goes to the confirmer before it can become ready.

**Neither is ever fatal.** A failed or refused re-check is noted in the hand-off
summary's process notes as `dependents re-check: <what happened>`, and the
delivery continues. The dependents are then scored by the next full
`/promote-tasks` run.

## `repo-pr`: on the task branch, before co-review

On `repo-pr` a completion is the deletion of the task file, which the merge
carries to the base, and a promotion is an edit to another task file. Both can
only land through a commit, so the re-check runs on the task branch and its
edits ride in the task's own PR. They then land if and only if the task does.

Run it in step 4, before step 5, so co-review reads the promotions and the head
it reviews is the head that is handed off:

1. If `dev_docs/tasks/<slug>.md` still exists on the task branch, remove it
   first, as the handler's delete step does (`git rm`, commit, push). While the
   file is present and not `done` it counts as an open blocker, so the run
   would hold every dependent and write nothing.
2. Follow `commands/promote-tasks.md` with `after=<slug>`, in the checkout that
   has the task branch checked out. The scan reads the checkout it runs in, and
   only there is the task file already gone.
3. If it wrote nothing (no dependents, all held, or it stopped on the legacy
   preflight), commit nothing and move on.
4. Otherwise stage only the task files its report names, commit them as
   `chore(tasks): promote dependents of <slug>`, and push.
5. Name the re-checked slugs and their outcomes in the PR body, under a
   `Dependents re-checked` heading.

A bail after this point finds the task file gone, and the handler's bail path
edits that file. Restore it first (`git checkout <base> -- dev_docs/tasks/<slug>.md`),
then follow the bail path. The promotions commit stays on the unmerged branch
and reaches nothing unless a human later finishes and merges the PR.

## Tracker handlers: after the lander merges

On `gh-issue`, `linear` and `jira` this runs only on `LAND: landed`
([`landing.md`](landing.md) → "Then land it"), after the completion write that
bullet describes. A hold, a skipped lander, or `AGENT_LAND` off means no merge,
so nothing is completed and nothing is re-checked.

When that completion's outcome is **applied** or **already complete**, follow
step 2 of `commands/complete-task.md` ("Re-check dependents") with the task's
id. The order matters: a tracker's own close on merge can lag the merge by
seconds, and a dependents read that sees the blocker still open holds every
dependent. On a handler `/promote-tasks` refuses, report its one-line refusal
and move on.

When the completion write failed, skip the re-check: the blocker is still open,
so it would hold everything.

## Which path re-checks a completion

The path that writes the completion runs the re-check. A task this skill
completed has left its started state, so `/sweep-for-complete` does not select
it and its batch re-check does not include it. A task whose completion write
failed here is completed later by the sweep or by the tracker's own close on
merge, and re-checked by that sweep's batch call or the next full
`/promote-tasks` run. A repeat would write nothing anyway: the mode only touches
un-scored items.
