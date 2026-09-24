# Reaching the PR's branch

Two questions the default disposition has to answer before it spends anything on
reviewers. Both are pointed at from `SKILL.md`'s pre-flight sections; this file
holds the reasoning.

## Why staleness checks `headRefName`

Step 12 pushes **the current branch** (`git push`, or `git push -u <remote> HEAD`).
So the ref a staleness check must protect is whichever branch the session is
standing on — a local copy behind its remote means a rejected push, or upstream
commits clobbered by a force.

Naming `headRefName` is correct only because the working-directory pre-flight makes
the two the same ref. On `foreign` and `absent` it moves the session into the
branch's tree and re-runs, continuing only on `ok`. It stops on `unknown` in the
default disposition, and that is the part that matters here. Since no non-`ok` verdict
continues, a run that reaches staleness is provably standing on `headRefName`, and
checking it is checking the push target.

The stop on `unknown` is what makes this hold, not the name. If `unknown` ever
becomes warn-and-continue, staleness must go back to checking the current branch.

## Reaching the branch's tree

`foreign` and `absent` are not questions for the user. The branch is somewhere
else, and going there is mechanical, so co-review takes the route itself.
`scripts/reach-pr-branch.sh` picks it. When the branch is not local, the helper
fetches it first with an explicit `refs/heads/` refspec. That writes no
tracking entry to `.git/config`.

On Claude Code, the tools that move a session between trees are:

- **`EnterWorktree` with `name`** — runs the plugin's `WorktreeCreate` hook,
  which puts the worktree at `<root>/<repo>/<name>` on the branch
  `<prefix><name>` (both from `scripts/worktree-config.sh`). If that branch
  already exists, the hook re-attaches it. If a worktree is already there on
  it, the hook hands it back as-is. The hook supplies the path, not the model,
  so nothing prompts. This is the preferred route. The helper returns it
  (`enter-name`) whenever the branch carries the configured prefix.
- **`EnterWorktree` with `path`** — enters a worktree that already exists. The
  path is model-supplied, so the harness raises an approval prompt that no
  allow rule lifts. The helper returns it (`enter-path`) in two cases: a
  `foreign` tree outside the hook's layout, and an `absent` branch without the
  prefix, after the helper has added a worktree for it under the configured
  root.

Neither route touches `HEAD` in the tree the session is standing in, the main
checkout included. That is why co-review takes the route without asking the user first. Checking the
branch out in place would switch someone's main checkout out from under them.
Adding or entering a worktree leaves every existing tree as it was.

The helper prints one `ROUTE:` line. What to do with it depends on whether
anyone is there to answer the `enter-path` prompt:

| `ROUTE:`                   | attended                                                                                   | unattended (`--non-interactive`)          |
| -------------------------- | ------------------------------------------------------------------------------------------ | ----------------------------------------- |
| `here`                     | proceed                                                                                    | proceed                                   |
| `enter-name name=<n>`      | `EnterWorktree` with `name` `<n>`; no prompt                                               | same                                      |
| `enter-path path=<p>`      | `EnterWorktree` with `path` `<p>`; the user approves the path                              | hard error — the approval cannot be given |
| `none reason=<r>` (exit 1) | stop and name `<r>`: `fetch-failed`, `path-exists`, `worktree-add-failed`, `config-failed` | hard error; `needs-path` is `--no-add`'s  |

Unattended, the helper runs with `--no-add`. An `absent` branch without the
prefix then reports `none reason=needs-path` instead of creating a worktree the
run could not enter. Otherwise that worktree would be left behind with nobody
to remove it.

Every route ends by re-running the pre-flight, and only `ok` proceeds. The
re-run catches a harness without the hook, where `EnterWorktree` with `name`
cuts a new branch rather than attaching this one. It also catches an
`EnterWorktree` call that refused, for example a `name` call from a session
already in a worktree session.

### What to do when there is no route

**Use `--post`.** It never writes to the working tree, so it does not need the branch
at all: the review runs, the findings are reconciled, and they are posted to the PR
rather than applied. The review is the expensive part and it is unaffected.

The default disposition's only product that needs the branch is the fix commit.
Trading that for posted findings is a smaller loss than abandoning a paid-for review.

Under `--non-interactive` this is **not** an automatic fallback: `--post` publishes to
GitHub, and switching disposition unattended would post a review nobody asked for.
A failed route stays a logged hard error there. It is cheap because it fires before
any reviewer is dispatched.
