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

**Only on repos whose owner is listed**, when
`WORKFLOW_SKILLS_CO_REVIEW_STATUS_OWNERS` (comma-separated GitHub owners, case
ignored) is set; unset or empty, every repo gets it. Outside the list the
status is a check no lander reads, on someone else's PRs. The scripts apply
the list themselves, so make the calls below as usual: an unlisted repo comes
back as exit 3 with nothing posted, and carrying is refused the same way.
A repo whose ruleset requires the `co-review` check needs its owner on the
list, or every PR there waits on a check that is never posted.

- **`pending`** once step 3's pre-flights have passed, before the bot wait and
  before any reviewer is dispatched. Posting after the pre-flights rather than
  straight after the metadata read means a run that stops on a stale or
  conflicting branch leaves no status at all, which is accurate: nothing was
  reviewed.
- **`success`** at step 8's stamp point, when **at least two reviewers ran**
  and every other planned reviewer was skipped by configuration. Your own
  review counts as one, so a run that skips down to Claude alone is a
  `failure`, however clean that one review was.
- **`failure`** at the same point otherwise, and also when the run stops for
  any reason after `pending` was posted, whenever the run can still act. A
  crash, a user interrupt or a lost session can still leave `pending` on the
  head. The next run on that head replaces it, because a later status with
  the same context overwrites the earlier one.

**Skipped by configuration** means the run never meant to use that reviewer
here: `--remote` (no local agents), `--reviewer-set cheap-single` (the others),
a reviewer not configured or not installed, a failed auth probe or version
gate, an auth, policy or entitlement error caught from a reviewer's output
(copilot's login or `Access denied by policy` line, devin's `/upgrade to
access this model`), an untrusted custom command, or a bot that was not
requested (`AWAIT_REVIEW: not-requested`). A skip costs nothing by itself, but
it does not count towards the two. **A failure** is a reviewer that was
dispatched and did not finish: it crashed, timed out (a CLI reviewer at its
15-minute bound, a bot at its 20-minute one), or exited with no findings and
no `REVIEW_COMPLETE` line. A reviewer that emitted findings but no sentinel
ran. Any failure makes the status `failure`, even when two others ran.

This is a status rule only. The ledger still records an output-caught auth
error as `failed` ([`recording.md`](recording.md)), because there the question
is how the reviewer behaved, not whether this head was reviewed enough.

## Which head

**Always step 3's `headRefOid`**, for all three states. That is the head every
reviewer read and the one the stamp names. It is never the commit step 12
pushes: that commit is unreviewed by construction. So after an auto-fix push
the PR's new head carries no `co-review` status, and a required check waits
for the next round.

The one exception is a round that passed, below: its `success` is carried to
the fix commit, marked as carried, rather than reposted as a review of it.

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

## Carrying a pass to the fix commit

A round that passes but pushes wording fixes would otherwise leave the PR
waiting on a round that reviews only those fixes, and finds more wording to
fix. So at the very end of step 12, after the evidence re-stamp, and
only when step 12 pushed a fix commit:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-carry.py" --repo <owner>/<name> --pr <n> --reviewed <headRefOid> --head <fix sha> --verdict "<pass|pass with suggestions|blocking>" --applied-blocking <n> --evidence <the evidence file> --summary "<the stamp's summary>" --target-url <PR url>
```

`--verdict` is section 1's verdict word, `--applied-blocking` counts the
applied fixes decorated `(blocking)`, and `--evidence` is the file step 12
just stamped with. Pass the verdict as it is: the script decides, so a round
that did not pass gets a refusal rather than a status. It carries only when
the verdict is `pass`, no applied fix was blocking, the record says
`no further round` and counts no unresolved finding, the reviewed head's own `co-review` status is `success`,
and the fix commit is exactly one commit on top of the reviewed head. Then it
re-stamps the PR for the fix commit, with `carried_from` naming the reviewed
head in the record, and posts `success` on the fix commit described as
`carried from <short sha>`. It writes to GitHub, so run it unsandboxed.

- **Exit 0** prints `CARRY: carried from=<short> to=<short> …`. Put
  `status ✅ carried to <short>` on the Next round section's fix-commit line.
- **Exit 3** prints `CARRY: refused reason=<why>` and wrote nothing. Put
  `status ➖ not carried — <why>` there; the fix commit waits for a round,
  except on an unlisted owner's repo, where no round posts one.
- **Exit 1** is a failed GitHub call, a warning like the others:
  `status ❌ not carried — <reason>`. Don't retry in a loop.

The lander reads `carried_from` and names the reviewed head in its verdict
comment, so a carried head is never mistaken for a reviewed one.

## Reading the outcome

- **Exit 0** prints `STATUS: <state> sha=<short> url=<url>`. Put
  `status ✅ <state>` on section 1's reviewer line.
- **Exit 1** prints `STATUS: error sha=<short> reason=<reason>`. It is a
  warning, never a stop: put `status ❌ not posted — <reason>` on the reviewer
  line and carry on. A missing `success` costs one more round before landing,
  and a failed run costs the whole review. Don't retry in a loop.
- **Exit 2** is a usage error, usually a short sha or an unrendered
  `${CLAUDE_PLUGIN_ROOT}`. Fix the call and run it once more.
- **Exit 3** prints `STATUS: skipped sha=<short> reason=<reason>`: the repo's
  owner is not in `WORKFLOW_SKILLS_CO_REVIEW_STATUS_OWNERS`, and nothing was
  posted. Put `status ➖ not applicable — owner not listed` on the reviewer
  line. The run's later states skip the same way.
