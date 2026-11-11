# Landing at hand-off

Step 7 of `/deliver-task` runs the merge lander twice
(`dev_docs/decisions/2026-10-03-lander-runs-in-the-session.md`). First in
**shadow**: it posts a "would land: yes/no" comment on the PR and never merges.
Those comments are the shadow record that `scripts/merge-audit.py --mode shadow`
counts toward graduation, so every delivered PR should get one. Then for
**real**, which merges only when the repo has switched landing on — see "Then
land it" below.

## When

Once per hand-off, after step 7 has tried to mark the PR ready and then write
the handler review state. A PR left draft for a human hand-off still gets the
run; its hold reason is `PR is a draft`, which is the true answer.

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

## Then land it

Right after the shadow run, run the lander without `--dry-run`, unsandboxed —
unless `/deliver-task` was given `--no-land`. Then skip this section and note
`lander: skipped (--no-land)` in the process notes: the task is a stack parent
in an `/auto-pilot` run, and in a repo that deletes merged branches (GitHub's
`deleteBranchOnMerge`), landing it would delete the branch a child is, or
will be, based on.

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/land-pr.sh" <pr> --repo <owner/name>
```

It reads the repo variable `AGENT_LAND` first and holds unless it is `on`, so
on a repo that has not switched landing on it prints only `LAND: hold
reasons=AGENT_LAND is off or unset`. Otherwise it repeats every check the shadow
run made — `auto` tier, co-review evidence and a `success` status on the exact
head, checks green, the PR based on the default branch — and squash-merges
pinned to that head. Its last line decides what follows:

- **`LAND: landed sha=<sha>`** — the PR is merged, and that merge is the
  verification `/sweep-for-complete` would otherwise wait for. Complete the
  task with the handler's `/complete-task` phase, passing `assume_verified:
  true` (no one is there to confirm) and a `comment_body` naming the merge sha.
  On repo-pr the merged PR is itself the completion, so write nothing. A
  failed completion write is noted as `completion: <what failed>` in the process
  notes; the PR is merged, so the tracker's own merge-driven completion finishes
  it later: `/sweep-for-complete` on Linear, GitHub's `Closes #<n>` close on
  gh-issue, Jira's GitHub integration on Jira.
  Under `/auto-pilot`, the `LAND:` line is what the orchestrator records.
- **`LAND: hold reasons=...`** — the PR stays at `needs_review` exactly as step
  7 left it. A hold is data, not a failure: it becomes a "For you" item only
  when its reason is one the human has to act on.
- **Anything else** — a non-zero exit, a missing script, or a permission denial
  (an unattended run with no allow rule for the script): note `lander: <what
  failed>` in the process notes and leave the task at `needs_review`. Never
  retry, and never merge any other way.

The `LAND:` line, with any `MERGE:`, `LABEL: error` or `COMMENT: error` line
before it, goes verbatim into "Review and evidence". A stacked task's PR is
based on its parent's branch, so the lander always holds it, and the parent
itself is not landed mid-run (`--no-land` above); a stack lands bottom-up, by a
human.
