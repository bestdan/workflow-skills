# The shadow lander after a round

The procedure behind SKILL.md step 12's last sentence. Each round that
co-reviews a PR also runs the merge lander in shadow, so the PR gets the "would
land: yes/no" comment that `scripts/merge-audit.py --mode shadow` counts toward
turning landing on (`dev_docs/designs/2026-10-03-graduated-merge-autonomy.md`,
Rollout step 2). It never merges.

## When

Last in step 12: after the evidence re-stamp and any `co-review-carry.py` call,
so the lander judges the PR as this round leaves it. When the round pushed a fix
that was not carried, the new head has no `co-review` success, and the comment
holds on that. That is the true answer. A PR that is still a draft gets the
hold `PR is a draft` too, and the audit does not count that one against it.

Only in the default disposition, and not in every run of it:

- **Never under `--local` or `--post`.** `--local` has no PR, and under
  `--post` the PR is someone else's.
- **Not under `--non-interactive`.** `/deliver-task` is the caller that passes
  it, and its hand-off runs the shadow lander itself, after it readies the PR
  and with the tracker write that follows
  (`skills/deliver-task/references/landing.md`). Running it here as well would
  post the same comment twice.
- **Not when this run's `co-review` status call came back with exit 3**, which
  means the repo's owner is outside `WORKFLOW_SKILLS_CO_REVIEW_STATUS_OWNERS`
  ([`status.md`](status.md)). The lander has nothing to read there.

Record a skip on section 1's reviewer line as
`shadow ➖ not applicable — <reason>`.

## Run it

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/land-pr-shadow.sh" <pr> --repo <owner/name>
```

Type the path as SKILL.md renders `${CLAUDE_PLUGIN_ROOT}`, the absolute plugin
path. The script calls `gh`, so run it unsandboxed.

`land-pr-shadow.sh` is `land-pr.sh --dry-run --if-policy` with both flags fixed:

- **It cannot merge**, so the skill's `allowed-tools` grants it. `land-pr.sh`
  itself is not granted, because without `--dry-run` it lands the PR, and
  whether any session may run it unprompted is bestdan/workflow-skills#1317's
  decision.
- **`--if-policy` makes a repo with no `.github/merge-policy.yml` on its
  default branch a skip**, with no comment posted, because such a repo takes
  no part in landing.

## Read it

The last line decides what goes on section 1's reviewer line:

- `LAND: skipped reason=<why>` → `shadow ➖ skipped — <why>`.
- `LAND: would-land` or `LAND: hold reasons=...` → `shadow ✅ <that line>`, with
  the `COMMENT:` line's URL. A hold is data, not a failure, and it adds no
  finding or round.
- A `COMMENT: error` line, a non-zero exit, or no `LAND:` line →
  `shadow ❌ <what failed>`.

**It is never fatal.** Don't retry it, and never change the PR to make it pass.
