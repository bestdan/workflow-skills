# The `co-review` commit status

The procedure behind SKILL.md's status sentences: `pending` in step 3,
`success` or `failure` in step 8, and `failure` when a run stops after
`pending`. The stamp ([`stamping.md`](stamping.md)) tells a human reader which
head was reviewed. The status tells a machine: it is a commit status with
context `co-review`, and a ruleset can require it, so a merge lander can read
"review-complete on this exact SHA" without parsing a comment.

## When

Only in the default disposition, `--non-interactive` included. Never under
`--local`, which has no PR, and never under `--post`, where the PR is someone
else's and a status from this account would gate their merge. Record it as
`status ➖ not applicable — --local` or `— --post` on the reviewer line.

- **`pending`** once step 3's pre-flights have passed, before the bot wait and
  before any reviewer is dispatched. Posting after the pre-flights rather than
  straight after the metadata read means a run that stops on a stale or
  conflicting branch leaves no status at all, which is accurate: nothing was
  reviewed.
- **`success`** at step 8's stamp point, when every planned reviewer class ran
  or was skipped by configuration.
- **`failure`** at the same point otherwise, and also when the run stops for
  any reason after `pending` was posted, so a run never leaves `pending`
  behind.

**Skipped by configuration** means the run never meant to use that reviewer
here: `--remote` (no local agents), `--reviewer-set cheap-single` (the others),
a reviewer not configured or not installed, a failed auth probe or version
gate, an untrusted custom command, or a bot that was not requested
(`AWAIT_REVIEW: not-requested`). **A failure** is a reviewer that was
dispatched and did not finish: it errored, timed out (a CLI reviewer at its
15-minute bound, a bot at its 20-minute one), or exited with no findings and
no `REVIEW_COMPLETE` line. A reviewer that emitted findings but no sentinel
ran.

## Which head

**Always step 3's `headRefOid`**, for all three states. That is the head every
reviewer read and the one the stamp names. It is never the commit step 12
pushes: that commit is unreviewed by construction. So after an auto-fix push
the PR's new head carries no `co-review` status, and a required check waits
for the next round, which is the point.

## The call

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-status.py" --repo <owner>/<name> --sha <headRefOid> --state <pending|success|failure> --description "<one line>" --target-url <PR url>
```

As with the stamp, type the path as SKILL.md renders `${CLAUDE_PLUGIN_ROOT}`,
the absolute plugin path. SKILL.md's `allowed-tools` grants the call. It writes
to GitHub, so run it unsandboxed.

- `--sha` takes the full 40-character sha. The script refuses anything else
  with exit 2, before calling `gh`.
- `--description` is one line, clipped to GitHub's 140 characters: `reviewing`
  for `pending`, the stamp's summary for `success`, and the failed or
  timed-out reviewer classes for `failure` (for example
  `codex ⏱️ timed out · agy ❌ failed`).
- `--target-url` is the PR's URL, so the status links back to the review.

## Reading the outcome

- **Exit 0** prints `STATUS: <state> sha=<short> url=<url>`. Put
  `status ✅ <state>` on section 1's reviewer line.
- **Exit 1** prints `STATUS: error sha=<short> reason=<reason>`. It is a
  warning, never a stop: put `status ❌ not posted — <reason>` on the reviewer
  line and carry on. A missing `success` costs one more round before landing,
  and a failed run costs the whole review. Don't retry in a loop.
- **Exit 2** is a usage error, usually a short sha or an unrendered
  `${CLAUDE_PLUGIN_ROOT}`. Fix the call and run it once more.
