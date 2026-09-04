# Atomic claim lock — shared by the jira and gh-issue handlers

Read by `commands/handlers/jira-claim.md` and `commands/handlers/gh-issue-claim.md`
("Claim the issue"). It defines the **one** primitive both handlers lock on, the
fallback for environments that cannot use it, and how a claim is released.

> **`<KEY>`** below is the handler's issue identifier — the Jira key (`PLAT-142`) for
> `jira-claim.md`, the bare issue number (`142`) for `gh-issue-claim.md`. **`<branch>`**
> is the handler's deterministic branch name, which is also the lock ref: `task/<KEY>`
> for jira, and `<branch_prefix>task-<KEY>` for gh-issue, whose prefix comes from the
> `gh-issue.branch_prefix` config key (see `gh-issue-claim.md` → "Branch name"). Only
> two properties matter to this file: both racers compute the **same** name from the
> issue alone, and the name is a legal ref. Everything below is written in terms of
> `<branch>`.

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
`git ls-remote --heads origin <branch>` in pre-flight; creating the ref at **claim**
time (rather than at PR time, ~an hour of work later) is what turns that read into a
real lock instead of a TOCTOU probe spanning the whole execution.

> **Do not "simplify" this to `git push origin <branch>`.** Measured against a real
> GitHub remote: two racers both cut `<branch>` from the same `origin/<base>` tip, so
> they push the **identical sha** — the loser's push reports `Everything up-to-date` and
> **exits 0**, and both sessions conclude they won. That is the same both-confirm bug
> this file exists to fix, one layer down. `--force-with-lease=refs/heads/<branch>:`
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
  -f "ref=refs/heads/<branch>" -f "sha=$base_sha"
```

Read the result — it is the election:

- **HTTP 201** → you hold the claim. Check the branch out and proceed to the handler's
  human-visible markers (assign yourself, transition/label), then to "Branch + execute"
  **already on this branch** — do not create it a second time:

  ```bash
  git fetch origin "<branch>" && git switch -c "<branch>" FETCH_HEAD
  ```

- **HTTP 422 `Reference already exists`** → **you lost the race.** Do not build, do not
  touch the issue's assignee or status: they belong to the winner. Report
  `Skipped <KEY>: claim lost — <branch> already exists on origin` and advance to the
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

## Cloud routines: use the comment election, not the ref lock

A routine has two channels to GitHub and only one of them is credentialed:

- **Raw HTTP carries no token.** `gh` is not installed, and `curl` to `api.github.com`
  gets **403** on writes; reads on `/git/refs` were inconsistent between runs, so do
  not rely on that path for reads either. **The `gh api` acquire form above is
  therefore local-only.**
- **The GitHub MCP connector is the routine's real channel**, and
  `mcp__github__create_branch` is a working acquire primitive there — create-only, and a
  duplicate is rejected with `Reference already exists`, the same election semantics as
  `POST /git/refs`.

> **Routines still default to the comment-token election below.** The acquire primitive
> works; the other half of the lifecycle does not.

### Why it is not the default: a routine cannot release

A routine **cannot release the lock it would acquire** — the connector exposes no
delete-branch and no delete-ref tool, and `git push --delete` returns 403. So the bail
path at the end of this file is **unavailable unattended**, and the two failures are not
symmetric:

|                  | left behind     | cost                                                                                                     |
| ---------------- | --------------- | -------------------------------------------------------------------------------------------------------- |
| `create_branch`  | a lock ref      | **permanent** — every later session reads it as a live claim and skips the issue forever                 |
| comment election | a claim comment | **self-healing** — the `T_unclaimed` filter and the state-backed check in step 5 already discard orphans |

"Acquire only when you intend to run to completion" is **not** a mitigation: a crash or a
timeout is precisely the case where intent does not apply, and there is **no stale-ref
sweep in this repo** — `scripts/claim-scan.sh` and `/doctor` both operate on `repo-pr`
claim PRs, not refs. Flipping the default is a one-paragraph change once a sweep exists;
until then a routine that somehow does hold a ref must be cleared from a **local**
session (`git push origin --delete <branch>`).

One caveat if the default ever flips: `create_branch` takes `from_branch`, **not a sha**,
so it cannot pin an exact base. That does not weaken the election — the lock is the
_name_ — but the branch may not sit at the sha the session read earlier.

> **Measured 2026-08-24 against the live API.** Probe transcripts, the verbatim 403
> texts and the full 58-tool connector inventory are in
> [`dev_docs/decisions/2026-08-24-routine-claim-channel.md`](../../dev_docs/decisions/2026-08-24-routine-claim-channel.md).
> **Do not re-derive routine behaviour from documentation** — this file was wrong twice
> that way.

## Batch-dispatched sessions: use the comment election, not the ref lock

A session dispatched by `/do-tasks --all` takes the election below **by choice**,
not because it cannot acquire — it usually can. The reason is the same asymmetry
the routine section gives, arriving by a different route: an unattended session
that crashes or times out after acquiring strands its ref, every later session
reads that ref as a live claim, and there is no stale-ref sweep. A batch fans out
N such sessions at once. `commands/do-tasks.md` §4 step 6 owns the reasoning and
the report string (`claim: comment election (batch dispatch)`, **not** the degrade
line below — no API error happened). Such a session must **not** attempt the
acquire first.

It also runs **one step the election below does not have**, and the step is
load-bearing: a `git ls-remote --heads origin "<branch>"` probe, twice — once
between step 3's comment post and step 3's marker write, and again with step 5's
re-list. The election cannot see a ref, so without those probes a batch session
and a **local** ref-lock session cannot detect each other at all. `do-tasks.md`
§4 step 6 owns both.

## Fallback: comment-token election (environments that cannot acquire)

When the acquire call fails for an environment or permission reason
(never on a 422 — that is a decided race), degrade to the election below and
**say so explicitly** in the report: `claim lock degraded to comment election: <the
API error>`. A silent degrade would claim atomicity the run does not have.

The election is `linear-claim.md`'s, ported to the handler's own comment API
(`addCommentToJiraIssue` / `gh issue comment`). Comment ids are numeric and increase
with creation time on both trackers, so a lowest-id-wins ordering is deterministic —
**verified** on a live Jira instance: ids `324986` (13:02:13) < `324988` (13:02:31) <
`325216` (next day), and editing a comment moves `updated` without changing its `id`.

1. **Record `T_unclaimed`** — the wall-clock time of the last read that showed this
   issue unclaimed (the handler's re-read guard). Claim comments older than it are
   stale orphans from earlier attempts, not competitors.
2. **Mint a unique session token** — `openssl rand -hex 16` (or
   `printf '%s%s' "$(date +%s%N)" "$RANDOM"`), written as
   `<!-- do-tasks-claim:<rand> -->`. No email, hostname, or pid — the election needs
   only uniqueness.

   > **The token is invisible on gh-issue, visible on Jira.** GitHub renders the HTML
   > comment away; Jira does **not** — the Atlassian MCP converts the body to ADF and
   > stores the marker as a literal text paragraph (verified: `{"type":"text","text":"<!--
   > do-tasks-claim:… -->"}`), so it shows verbatim on the issue. Accept that on the
   > degraded path rather than dropping the token — it is the ordering key — but do not
   > tell the user it is hidden.

3. **Post the claim comment first** — it is the lock:

   ```
   Claimed by /do-tasks. Working on branch `<branch>`; PR link will follow.
   <!-- do-tasks-claim:<rand> -->
   ```

   Capture the comment id — the losing branch and the bail path both need it to retract
   **this** session's comment (see step 6). **Then** write the human-visible markers
   (assign yourself + transition/label).
4. **Sleep a jittered ~2–3 s** so the comment and the issue write propagate and racer
   symmetry is broken.
5. **Re-list the comments and elect** — among comments carrying the
   `do-tasks-claim:` marker, keep only those (a) created at or after `T_unclaimed` and
   (b) **state-backed**: the issue currently carries this handler's claim markers
   (assignee set, and the In-Progress status on jira / the `status:3_started` label on
   gh-issue), proving the poster's
   issue write landed. The winner is the **lowest comment id** (equivalently the
   earliest `createdAt`). Both filters are load-bearing: without (a) a stale orphan
   from a dead session is elected and the issue deadlocks with no owner; without (b) a
   session that crashed between the comment and the issue write wins forever.
6. **If your comment wins** → you hold the claim; proceed. **If it does not** → retract
   your own claim comment (below), leave the assignee/status **untouched** (they are the
   winner's), and advance to the next candidate.

   **Retract, per tracker — jira cannot delete.** The Atlassian MCP exposes no
   delete-comment tool (`addCommentToJiraIssue`, `addWorklogToJiraIssue`, … — there is
   no `deleteJiraComment`), so a jira loser **cannot** remove its comment. Instead
   **rewrite the body without the token**, which is what makes it inert for every future
   election: `addCommentToJiraIssue` with `commentId: <your id>` updates in place
   (verified — same id, `updated` advances). Use a body that says the claim was retracted
   and no work started. On gh-issue, delete it outright:
   `gh api --method DELETE repos/<repo>/issues/comments/<id>`.

   > **In a cloud routine this retraction is impossible** — the connector exposes no
   > delete-comment tool, and the `gh api` form cannot run there (see "Cloud routines"
   > above). A losing routine leaves its token behind, which costs hygiene rather than
   > correctness: a loser's id is always higher than the winner's, so it can never win
   > its own race. Treat a leftover routine token as expected, not as a live claim.

   Retraction is hygiene, not correctness: a loser's comment always has a **higher** id
   than the winner's, so it can never win its own race. What it protects is the _next_
   session, whose `T_unclaimed` may fall before a leftover token — the tokenless
   rewrite means there is nothing left to elect.
7. **Read-lag residual.** If the re-list returns **only** your own marker, treat it as
   inconclusive and re-poll once or twice before declaring a win — no server-side CAS
   backs this path, which is why it is the fallback and not the default.

## Release the lock

A claim is released on **bail** (and only there — a successful run's lock ref becomes
the PR's head branch and is cleaned up by the merge):

```bash
git push origin --delete "<branch>"   # only if this session acquired the ref above
```

Delete the remote ref **before** clearing the issue's assignee/status, so the issue
never sits unclaimed while a stale lock ref still blocks the next session's acquire. On
the fallback path there is no ref to delete — **retract** this session's token comment
instead, per step 6 above (rewrite it tokenless on jira, which cannot delete; delete it
on gh-issue). Never delete a `<branch>` ref this session did not acquire.

> **A routine cannot run this step** — which is exactly why routines do not take the
> ref lock in the first place (see "Cloud routines" above). A stranded ref has to be
> deleted from a local session, where both `git push origin --delete <ref>` and the
> equivalent `DELETE .../git/refs/heads/<ref>` API call work.
