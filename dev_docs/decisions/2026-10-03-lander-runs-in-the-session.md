---
created: 2026-10-03
status: accepted
convention: ../merge-autonomy.md
---

# The lander is a plugin script the session runs after co-review

## Context

Something has to merge an `auto`-tier PR once its checks and review are green,
and that something must not be the authoring agent's own judgement
([`2026-10-03-rules-tier-models-only-demote.md`](2026-10-03-rules-tier-models-only-demote.md)).
[The merge-autonomy design](../designs/2026-10-03-graduated-merge-autonomy.md) (PR #952)
weighed three homes for it: a script the session runs, a GitHub Actions
lander, and a scheduled sweeper. The repo already puts decisions of this shape
in tested scripts (`pr-fix-guard.sh`, `tier-coverage.py`). An Actions lander
would need a copy in each of four repos and, in workflow-skills, a
bypass-capable token so its merge triggers the release. A blind `/consult`
picked the session script at 75%, with the crux "if the `co-review` status
could not be read from the session, an Actions lander or a sweeper wins". It
can be read.

The owner's agent settings hold every merge command under `ask`, and a guard
hook refuses any other merge route it can see in the command text. Allowing
the merge commands outright would let the authoring agent merge directly and
skip `merge-tier.py`, because the required checks carry no tier signal.

## Decision

`scripts/land-pr.sh <pr>` (logic in `land-pr.py`) runs in the session at
`/deliver-task`'s hand-off, after co-review. It merges only when the PR is
open, not a draft and based on the default branch; `merge-tier.py` says
`auto`; the co-review evidence for the exact head shows nothing that demotes;
the `co-review` status on that head is `success`; and every check on the head
is green. It squash-merges with `--match-head-commit` pinned to the head it
judged, and the squash body carries the audit record: the reasons, the
evidence pointers, and the trailers `Landed-by: land-pr.sh` and
`Co-review-head: <sha>`, so the record lands atomically with the merge. The
`landed-by-agent` label and a PR comment follow as best effort.

Merge authority reaches the session only through one allow rule naming the
installed plugin's `land-pr.sh` by absolute path, globbed over the version
directory. A repo-relative or working-tree copy is never allowed, because the
authoring agent can edit it. Raw merge commands stay `ask`. The guard hook is a
precondition of that allow rule: if the hook goes, the rule goes with it.

## As shipped

- `--dry-run`, the shadow lander, PR #1346 (issue #1315), run at every
  `/deliver-task` hand-off by PR #1406 (issue #1318).
- Real landing behind the `AGENT_LAND` repo variable, PR #1412 (issue #1316),
  with the failed-merge re-read of PR #1422; the hand-off step, PR #1425 (issue
  #1319). Unset means off.
- A stacked PR, based on its parent's branch, always holds: its policy would be
  read from a branch an agent can edit. `/deliver-task --no-land` keeps an
  `/auto-pilot` stack parent from landing mid-run, so a stack lands bottom-up
  by a human. The design did not cover this case.
- The post-merge net: `/revert-landing` and `merge-audit.py --mode landed`,
  PR #1437 (issue #1323). The landed audit keys on the `Landed-by` trailer and
  names any such merge without the label.
- On 2026-11-11 `AGENT_LAND` was unset in all four repos and no allow rule for
  `land-pr.sh` existed in the owner's settings: every repo is still in shadow.
  The allow rule is added when a repo leaves shadow; until then an unattended
  run that reaches the real landing gets a permission denial, which
  `skills/deliver-task/references/landing.md` treats as a hold.

## Consequences

- Good, because one tested copy serves every repo, and the merge needs no
  token beyond the session's.
- Good, because the audit record cannot be lost between merge and label: it is
  in the commit.
- Bad, because a PR whose session ended before hand-off never lands itself; a
  scheduled sweeper would cover it and is not built.
- Bad, because the guard reads only command text. A merge inside a script the
  agent wrote is invisible to it, and an unsandboxed command or an Edit could
  modify the installed `land-pr.sh`. The installed copy is outside the
  sandbox's write set, which makes it tamper-resistant, not tamper-proof.
- Bad, because the `Landed-by` trailer is self-attested: any squash body can
  carry it.

## Revisit when

- The session can no longer read the `co-review` status or the evidence
  record, which is the consult's crux for an Actions lander or a sweeper.
- PRs routinely finish review after their session has ended, so landing at
  hand-off misses many `auto` PRs: add the sweeper.
- Co-review moves out of the session, so an Actions lander could run with
  credentials the authoring agent does not hold.

## Confirmation

`scripts/test_land_pr.py` and `scripts/test_revert_landing.py`. Nothing
mechanical ties the allow rule to the guard hook; that rests on the owner's
settings.

## Alternatives

- **GitHub Actions lander.** Rejected: four workflow copies, and in
  workflow-skills a bypass-capable token for the merge to trigger the release.
- **Scheduled sweeper.** Deferred to Phase 2, for PRs whose session ended, not
  a starting point.
- **Allow the merge commands outright.** Rejected: the authoring agent could
  merge directly and skip the tier.
