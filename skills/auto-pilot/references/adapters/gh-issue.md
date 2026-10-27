# Auto-pilot gh-issue adapter

The rules shared by every adapter, the eight verbs' contracts, and the WIP gate
are in the [adapter interface](../adapters.md). This file holds only what is
specific to a GitHub-issues source.

Source: a GitHub **milestone**, or an explicit **label query**, in the repo
`gh-issue.repo` names (`handler: gh-issue`); the source syntax lands in
`SKILL.md` with issue #1020.
Either one only **narrows** the handler's candidate query: a ready issue still
needs `status:2_ready` **and** `auto:eligible`, so a human can withhold an issue
from the run exactly as from `/do-tasks`. Native GitHub issue dependencies
(`blocked_by`) are the dependency graph. Every verb delegates to a
`commands/handlers/gh-issue-claim.md` section, a `commands/handlers/assets/`
script, or a plain `gh` call — none are no-ops.

| Verb               | Delegates to                                                                                                                                                                                                                                                                                                                  |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `list_ready`       | `gh-issue-claim.md` **Find candidates**, with the source ANDed into its `--search` (`milestone:"<title>"`, or one `label:"<name>"` term per queried label) — never replacing its `status:2_ready` + `auto:eligible` terms — then `gh-issue-ready.py --issue <n>:<est> … --max-estimate <bound>` over exactly those candidates |
| `dependency_graph` | `gh-issue-graph.py --repo <repo> --json`, scoped with `--milestone` or `--label` to match the source (the same reader `gh-issue-reoptimize.md` **Load — build the graph** uses)                                                                                                                                               |
| `claim`            | `gh-issue-claim.md` **Pre-flight: is work already in flight?** then **Claim the issue** (the ref lock — see below); build on `gh-issue-claim.py branch-name --issue <n> --prefix "<branch_prefix>"`, never a hand-built name                                                                                                  |
| `link_pr`          | `gh-issue-claim.md` **PR** — **only** its `PR opened: <url>` issue comment; the rung stays `status:3_started`. The PR body's `Closes #<n>` is the native link                                                                                                                                                                 |
| `set_needs_review` | `gh pr ready <pr>` under `/deliver-task`'s write-access gate (step 4, **Open the PR**), then `gh-issue-claim.md` **Move to review on PR open** (`gh-issue-state.py` → `status:4_needs_review`); parks instead of handing off a draft — see below                                                                              |
| `flag_for_human`   | `gh-issue-claim.md` **Bail (when execution proves infeasible mid-flight)** label + comment mechanics, **diverged** — see below                                                                                                                                                                                                |
| `comment_progress` | `gh issue comment <n> --repo <repo>`                                                                                                                                                                                                                                                                                          |
| `wip_limit`        | `gh-issue-claim.md` **Pre-claim WIP gate** (`gh-issue-claim.py wip`, which counts this operator's `status:3_started` + `status:4_needs_review`)                                                                                                                                                                               |

**Claim mode: the ref lock, not the comment election.** `/do-tasks`'s batch
dispatch puts its sessions on `claim-lock.md`'s comment-token election because
nothing releases a ref a crashed unattended session acquired (`commands/do-tasks.md`
§4 step 6). An auto-pilot run is different in the two ways that matter: one
serialized orchestrator claims, so there is no claim race to elect over, and a
crashed run that is resumed reconciles its own claim through `--resume`'s **G1**
row ([run-state.md](../run-state.md#crash-reconciliation)) — keep the ref if the claim
is still ours, release it and park if not.

**The lock ref exists from the moment of claim, and resume must not read it as
pushed work.** `claim` (via `gh-issue-claim.py acquire`) creates
`<branch_prefix>task-<n>` in `<repo>` at the base
sha before any implementation, so a crash before the first push leaves a remote
branch with no PR. That matches **G4**'s observed reality ("remote branch exists;
no PR"), whose action — open the PR — fails with no commits and parks a task that
should have resumed. So a remote `<branch_prefix>task-<n>` with **no commits ahead of
its base** (`git rev-list --count origin/<base>..origin/<branch>` is `0`) is "no
branch" for G1/G2 matching, never G4. Test commits ahead, not tip equals base: the
base may have moved since the claim. Linear and plan sources create no ref at claim,
so this reading is gh-issue's alone.

What `--resume` does not cover is a crashed
run **nobody resumes**: its `<branch_prefix>task-<n>` ref stays on origin and every
later session skips the issue, until the crashed-claim sweep in issue #760 exists. Do not
launch unattended runs you do not intend to resume until it does.

**The PR is a draft until hand-off, and that is what keeps G5 and G6 true.**
`.github/workflows/gh-issue-pr-sync.yml` writes `status:4_needs_review` from a PR's
non-draft `opened` event and from `ready_for_review` (`gh-issue-claim.md` **Move to
review on PR open**). A PR opened ready would put the issue in needs-review before
co-review ran — the state the [write order](../run-state.md#write-order) forbids. The
run opens the PR as a **draft** (`/deliver-task` on the gh-issue handler; the
draft-open lands with issue #1023, and until then `/auto-pilot` refuses this
handler), so the
Action stays silent at pr-open and `link_pr` is the URL comment alone. At hand-off
`set_needs_review` marks the PR ready first and then writes the rung itself —
remote before tracker, per the write order. Only a PR that is actually ready gets
the rung: if `viewerPermission` is not `ADMIN`/`MAINTAIN`/`WRITE`, or still cannot
be determined after one re-read, or `gh pr ready` fails, write nothing and call
`flag_for_human` instead (the issue stays `status:3_started`, the PR stays a
draft). Unlike attended `/deliver-task`, the run never hands off a draft. The Action fires on that
`ready_for_review` and races the verb's own write: whichever lands second either
finds the issue already on `status:4_needs_review` and writes nothing, or writes the
same rung again, so the two never conflict. A crash between the two steps leaves a
ready PR on `status:3_started`, which the Action usually advances on its own
(resume then sees **G7**); if it does not, the state is **G6** and re-running this
verb repairs it. Never write the rung first: a crash there leaves
`status:4_needs_review` on a draft PR, which **G7** would record as handed-off with
nothing left to mark it ready. So **G5** (PR open, still `started`) and **G6** (reviewed, still
`started` → set `needs_review`) reconcile exactly as written; for G6, "set
`needs_review`" is this verb, mark-ready included.

**A parked issue still counts toward the WIP gate** — it stays
`status:3_started` — so enough parks stop further claims. That is deliberate: a
parked issue is in flight, waiting on a human.

**`flag_for_human` diverges from Bail on purpose.** **Bail** releases the lock ref,
moves the issue to `status:1_needs_refinement`, and clears the assignee. The
[shared park contract](../adapters.md#flag_for_human-the-shared-park-contract)
keeps it in flight instead:

- **Keep** `status:3_started`, the assignee, and the lock ref.
- Swap the `auto:` rung to `auto:human-review-needed`, as Bail does, and **set
  `prio:0`**. Both in one `gh-issue-state.py --apply`
  carrying the full managed set (`status:3_started,auto:human-review-needed,prio:0`
  plus its `est:` label), never `gh issue edit --add-label`.
- Post the reason comment with `gh issue comment`.

**No stacking within the run.** linear's `list_ready` passes `--stack-in-set`, so a
candidate blocked only by another candidate becomes a stack edge. `gh-issue-ready.py`
has no such mode: it drops any issue with an open blocker, and a blocker stays open
until its PR merges — after the run has handed it off. So a gh-issue run works the
**unblocked frontier** of its source; a dependent waits for the next run.
