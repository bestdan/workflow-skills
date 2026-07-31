# Atomic claim lock — shared by the jira and gh-issue handlers

Read by `commands/handlers/jira-claim.md` and `commands/handlers/gh-issue-claim.md`
("Claim the issue"). It defines the **one** primitive both handlers lock on, the
fallback for environments that cannot use it, and how a claim is released.

> **`<KEY>`** below is the handler's issue identifier — the Jira key (`PLAT-142`) for
> `jira-claim.md`, the bare issue number (`142`) for `gh-issue-claim.md`. Both handlers
> use the same deterministic branch name, `task/<KEY>`.

## Why the assignee cannot be the lock

Both handlers used to confirm a claim by re-reading the issue and checking that the
final assignee was **their own** account. That defeats a _different_ user racing you,
but not a **second session authenticated as the same user** — both write the identical
account id, both read it back, and both conclude they won, then both build the same
issue. `linear-claim.md` documents the same reasoning and elects a winner on an
append-only comment log instead.

The check cannot be repaired in place. Jira's REST API exposes no compare-and-swap on
issue fields (no `If-Match`/ETag on `editJiraIssue`) and GitHub's issue-edit API none
either, so every read-then-write on `assignee` has a window — and same-account racers
are reading for a value that is identical for both. The lock therefore has to sit on a
primitive that _is_ atomic. Assignee, labels, and status stay on as the **human-visible**
claim marker; they no longer decide the race.

The same trap applies to any "atomic" primitive whose success is indistinguishable from
a no-op — see the plain-`git push` warning below, which measured exactly that.

## Primitive: create-only ref creation via the GitHub API (the default)

`POST /repos/<owner>/<repo>/git/refs` **creates** a ref or fails — it never updates one.
A second call for a ref that exists returns **HTTP 422 `Reference already exists`**
regardless of the sha it names, so exactly one caller wins no matter which account each
session is authenticated as. Both handlers already probe
`git ls-remote --heads origin task/<KEY>` in pre-flight; creating the ref at **claim**
time (rather than at PR time, ~an hour of work later) is what turns that read into a
real lock instead of a TOCTOU probe spanning the whole execution.

> **Do not "simplify" this to `git push origin task/<KEY>`.** Measured against a real
> GitHub remote: two racers both cut `task/<KEY>` from the same `origin/<base>` tip, so
> they push the **identical sha** — the loser's push reports `Everything up-to-date` and
> **exits 0**, and both sessions conclude they won. That is the same both-confirm bug
> this file exists to fix, one layer down. `--force-with-lease=refs/heads/task/<KEY>:`
> (create-only lease) does **not** rescue it either: git short-circuits on
> nothing-to-update before evaluating the lease, and also exits 0. A plain push is a
> reliable CAS only when the two racers push **different** commits, which two sessions
> branching from one base do not.

**Acquire** — run this as the _first_ mutating step of "Claim the issue", before any
assignee/status/label write. `<repo>` is the handler's configured repo
(`gh-issue.repo`) or, when unset, the current one (`gh repo view --json nameWithOwner
--jq .nameWithOwner`); `<base>` is the handler's configured base branch, else the repo
default:

```bash
git fetch origin
base_sha=$(git rev-parse "origin/<base>")
gh api --method POST "repos/<repo>/git/refs" \
  -f "ref=refs/heads/task/<KEY>" -f "sha=$base_sha"
```

Read the result — it is the election:

- **HTTP 201** → you hold the claim. Check the branch out and proceed to the handler's
  human-visible markers (assign yourself, transition/label), then to "Branch + execute"
  **already on this branch** — do not create it a second time:

  ```bash
  git fetch origin "task/<KEY>" && git switch -c "task/<KEY>" FETCH_HEAD
  ```

- **HTTP 422 `Reference already exists`** → **you lost the race.** Do not build, do not
  touch the issue's assignee or status: they belong to the winner. Report
  `Skipped <KEY>: claim lost — task/<KEY> already exists on origin` and advance to the
  next candidate (in single/direct mode, stop).
- **Any other failure** — 403/404 from a token without write scope, a protected-ref
  ruleset, a branch-pinned environment, a network error — → this is **not** a lost race
  and **not** a held claim. Fall back to the election below; never report a claim you
  did not acquire atomically.

Because the loser detects the loss from the 422, feasibility judging may still run
**before** the claim: two sessions can both judge the same issue, but only one can
acquire, and the other advances deterministically instead of building a duplicate.

Every later `git push` on this branch is an ordinary fast-forward update of a ref this
session owns — the lock is the **creation**, and it is already decided by then.

## Fallback: comment-token election (branch-pinned environments)

Claude Code on the web runs pinned to a fixed `claude/<session>` branch and cannot
create `task/<KEY>` — this is exactly why the `repo-pr` handler locks on a PR rather
than a branch. When the acquire call fails for an environment or permission reason
(never on a 422 — that is a decided race), degrade to the election below and
**say so explicitly** in the report: `claim lock degraded to comment election: <the
API error>`. A silent degrade would claim atomicity the run does not have.

The election is `linear-claim.md`'s, ported to the handler's own comment API
(`addCommentToJiraIssue` / `gh issue comment`). Issue comments are append-only with
monotonic ids on both trackers, so a lowest-id-wins ordering is deterministic:

1. **Record `T_unclaimed`** — the wall-clock time of the last read that showed this
   issue unclaimed (the handler's re-read guard). Claim comments older than it are
   stale orphans from earlier attempts, not competitors.
2. **Mint a unique session token** — `openssl rand -hex 16` (or
   `printf '%s%s' "$(date +%s%N)" "$RANDOM"`), wrapped in an HTML comment so it is
   invisible in rendered markdown: `<!-- do-tasks-claim:<rand> -->`. No email, hostname,
   or pid — the election needs only uniqueness.
3. **Post the claim comment first** — it is the lock:

   ```
   Claimed by /do-tasks. Working on branch `task/<KEY>`; PR link will follow.
   <!-- do-tasks-claim:<rand> -->
   ```

   Capture the comment id; the losing branch and the bail path both delete **this
   session's** comment by id. **Then** write the human-visible markers (assign
   yourself + transition/label).
4. **Sleep a jittered ~2–3 s** so the comment and the issue write propagate and racer
   symmetry is broken.
5. **Re-list the comments and elect** — among comments carrying the
   `do-tasks-claim:` marker, keep only those (a) created at or after `T_unclaimed` and
   (b) **state-backed**: the issue currently carries this handler's claim markers
   (assignee set, and In-Progress status / `auto-claimed` label), proving the poster's
   issue write landed. The winner is the **lowest comment id** (equivalently the
   earliest `createdAt`). Both filters are load-bearing: without (a) a stale orphan
   from a dead session is elected and the issue deadlocks with no owner; without (b) a
   session that crashed between the comment and the issue write wins forever.
6. **If your comment wins** → you hold the claim; proceed. **If it does not** → delete
   your own claim comment, leave the assignee/status **untouched** (they are the
   winner's), and advance to the next candidate.
7. **Read-lag residual.** If the re-list returns **only** your own marker, treat it as
   inconclusive and re-poll once or twice before declaring a win — no server-side CAS
   backs this path, which is why it is the fallback and not the default.

## Release the lock

A claim is released on **bail** (and only there — a successful run's lock ref becomes
the PR's head branch and is cleaned up by the merge):

```bash
git push origin --delete "task/<KEY>"   # only if this session acquired the ref above
```

Delete the remote ref **before** clearing the issue's assignee/status, so the issue
never sits unclaimed while a stale lock ref still blocks the next session's acquire. On
the fallback path there is no ref to delete — delete this session's token comment
instead. Never delete a `task/<KEY>` ref this session did not acquire.
