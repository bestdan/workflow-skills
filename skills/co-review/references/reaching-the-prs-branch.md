# Reaching the PR's branch

Two questions the default disposition has to answer before it spends anything on
reviewers, and one of them has no good answer on some harnesses. Both are pointed
at from `SKILL.md`'s pre-flight sections; this file holds the reasoning.

## Why staleness checks `headRefName`, not "the current branch"

Step 12 commits and pushes fixes to **the PR's head branch**. That is the ref a
staleness check exists to protect: a local copy behind its remote means a rejected
push, or upstream commits clobbered by a force.

"The current branch" is a different ref that is _usually_ the same one. The
working-directory pre-flight runs first and stops a run that is standing
somewhere else, so by the time staleness runs the two normally coincide — which is
exactly why naming the wrong one is easy to miss and survives review.

It is not always the same, though. The working-directory pre-flight
**warns-and-continues on `unknown`** (`git worktree list` failed), and a run that
continues past `unknown` can be on any branch at all. Checking "the current
branch" there validates the one ref nothing will be pushed to and reports
`fresh` — a correct answer to a question that does not matter, which is how #843
was found.

So the rule is written against the ref that receives the push, and holds whether
or not the earlier check reached a verdict.

## A worktree-isolated session cannot get onto an existing branch

This is a harness limitation, stated here because the `absent` verdict would
otherwise tell such a session to do something impossible.

On Claude Code, the tools that move a session between trees are:

- **`EnterWorktree` with `name`** — creates a **new** worktree on a **new** branch
  cut from `main`. It cannot land on an existing branch; that is not what it is
  for.
- **`EnterWorktree` with `path`** — enters a worktree that **already exists**. If
  the PR's branch is checked out somewhere, this is the answer, and it is what the
  `foreign` verdict says to do.
- **`git worktree add <path> <existing-branch>`** — would work, and is the
  documented fallback for harnesses with no worktree tool. But the path is
  model-supplied, which raises an approval prompt that no allow rule lifts, and a
  prompt is a hard stop in an unattended run.

So when the PR's branch is checked out **nowhere** (`absent`), a worktree-isolated
session has no route to it. The main checkout does — a plain checkout — but a
session that has been moved into a worktree is not in the main checkout and must
not reach back into it.

### What to do instead

**Use `--post`.** It never writes to the working tree, so it does not need the
branch at all: the review runs, the findings are reconciled, and they are posted
to the PR rather than applied. The review is the expensive part and it is
unaffected.

The default disposition's only product that needs the branch is the fix commit.
Trading that for posted findings is a smaller loss than abandoning a paid-for
review, and it is the one disposition change that turns this from a dead end into
a different shape of the same job.

Under `--non-interactive` this is **not** an automatic fallback: `--post` publishes
to GitHub, and switching disposition unattended would post a review nobody asked
for. `absent` stays a logged hard error there, and it is cheap because it fires
before any reviewer is dispatched.
