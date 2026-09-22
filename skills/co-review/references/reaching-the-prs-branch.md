# Reaching the PR's branch

Two questions the default disposition has to answer before it spends anything on
reviewers. Both are pointed at from `SKILL.md`'s pre-flight sections; this file
holds the reasoning.

## Why staleness checks `headRefName`

Step 12 pushes **the current branch** (`git push`, or `git push -u <remote> HEAD`).
So the ref a staleness check must protect is whichever branch the session is
standing on — a local copy behind its remote means a rejected push, or upstream
commits clobbered by a force.

Naming `headRefName` is only correct because the working-directory pre-flight makes
the two the same ref. It stops on `foreign` and on `absent`, and — the part that
matters here — it now also stops on `unknown` in the default disposition. Once all
three non-`ok` verdicts stop, a run that reaches staleness is provably standing on
`headRefName`, and checking it is checking the push target.

**Getting this wrong is how #843 was filed, and the first attempt at fixing it got
it wrong the other way.** The original checked "the current branch" while reasoning
about "the ref step 12 pushes to", which read as a mistake but was accidentally
correct. Swapping the name to `headRefName` without closing `unknown` made the
reasoning read correctly while the code validated a ref that could differ from the
push target — the same defect, relocated. The stop is the fix; the name is
bookkeeping that follows from it.

So if `unknown` is ever relaxed back to warn-and-continue, this section is wrong
again and staleness has to go back to reading the current branch.

## Reaching an existing branch from a worktree-isolated session

On Claude Code, the tools that move a session between trees are:

- **`EnterWorktree` with `name`** — creates a **new** worktree on a **new** branch
  cut from `main`. It cannot land on an existing branch; that is not what it is for.
- **`EnterWorktree` with `path`** — enters a worktree that **already exists**. If the
  PR's branch is checked out somewhere, this is the answer, and it is what the
  `foreign` verdict says to do.
- **`git worktree add <path> <existing-branch>`** — works, and is the documented
  fallback for harnesses with no worktree tool. The path is model-supplied, so it
  raises an approval prompt that no allow rule lifts.

That prompt is the whole distinction between the attended and unattended cases, and
it is why the `absent` verdict gives three answers rather than one:

| where                                 | route                                                               |
| ------------------------------------- | ------------------------------------------------------------------- |
| the main checkout                     | check the branch out here                                           |
| a worktree-isolated session, attended | offer `git worktree add`; the user approves the path, then enter it |
| unattended                            | no route — the prompt cannot be answered                            |

**The unattended row is the only one with no way through**, so do not state the dead
end unconditionally. An attended session has a working route and removing it would
cost a recovery that exists.

### What to do when there is no route

**Use `--post`.** It never writes to the working tree, so it does not need the branch
at all: the review runs, the findings are reconciled, and they are posted to the PR
rather than applied. The review is the expensive part and it is unaffected.

The default disposition's only product that needs the branch is the fix commit.
Trading that for posted findings is a smaller loss than abandoning a paid-for review.

Under `--non-interactive` this is **not** an automatic fallback: `--post` publishes to
GitHub, and switching disposition unattended would post a review nobody asked for.
`absent` stays a logged hard error there, and it is cheap because it fires before any
reviewer is dispatched.
