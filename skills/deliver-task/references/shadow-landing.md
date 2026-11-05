# Shadow landing at hand-off

Step 7 of `/deliver-task` runs the merge lander in **shadow** (Phase 0 of
graduated merge autonomy, `dev_docs/designs/2026-10-03-graduated-merge-autonomy.md`).
It posts a "would land: yes/no" comment on the PR and never merges. Those
comments are the shadow record that `scripts/merge-audit.py --mode shadow`
counts toward graduation, so every delivered PR should get one.

## When

Once per hand-off, after the PR's final state is set: after `gh pr ready` where
step 7 readies a draft, and after the PR opened ready otherwise. A PR left a
draft still gets the run; its hold reason is `PR is a draft`, which is the true
answer.

## Run it

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/land-pr.sh" <pr> --repo <owner/name> --dry-run
```

`<owner/name>` is the PR's repository, which on gh-issue can differ from
`gh-issue.repo`. The script needs the network (it calls `gh`), so run it
unsandboxed. It prints three lines, `COMMENT:`, `TIER:` and `LAND:`; the header
of `scripts/land-pr.py` defines them.

**It is never fatal.** A non-zero exit, a `COMMENT: error`, or a missing script
is noted in the hand-off summary's process notes (`shadow lander: <what
failed>`), and the hand-off continues. Don't retry it and don't change the PR to
make it pass; a hold is data, not a failure.

## Record it

- **Hand-off summary:** the `LAND:` line goes verbatim into "Review and
  evidence".
- **Tracker:** the `TIER:` line goes on the task as one comment, `/deliver-task:
  merge tier <auto|human|unknown> — <first reason>`, where the first reason is
  the text before the first semicolon (no reason for `auto`). The tier is computed
  with `AGENT_LAND` taken as on, so it says whether the PR is one that lands
  itself once landing is switched on. `/sweep-for-complete` and the review
  queue can then tell which PRs will land themselves. Post it with the
  handler's comment write: `gh issue comment` on gh-issue, `save_comment` on
  Linear, `addCommentToJiraIssue` on Jira. On repo-pr the PR is the tracker, so
  post it on the PR with `gh pr comment <pr> -R <owner/name> --body-file
  <file>`. The lander's own comment is no substitute: while `AGENT_LAND` is off
  it lists `tier human: AGENT_LAND=off` even for a PR whose tier is `auto`. A
  failed tracker write is noted the same way as a failed run.
