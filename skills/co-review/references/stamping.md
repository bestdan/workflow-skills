# Stamping the PR

The procedure behind SKILL.md step 8's stamp sentence. The ledger never reaches
the PR, so a reader on GitHub cannot learn from it that the PR was reviewed.
The stamp is the one record of a co-review on the PR itself. It is a single PR comment whose
body opens with a fixed marker:

```
<!-- co-review-stamp v1 head=<40-hex sha> -->
```

followed by one line a human can read. The fleet view's review-queue gauge
(`scripts/task-dashboard/github_fleet.py`) counts a PR as co-reviewed only
when that sha equals the PR's **current** head. The marker format is a
contract with that parser. Change one only together with the other.

## When

Stamp right after the recording call in step 8, in the default disposition,
under `--non-interactive`, and under `--post`. Never under `--local`, which
has no PR. Record it as `stamp ➖ not applicable — --local` on the reviewer
line.

Step 8 is the right point because the review is finished there. The reconciler
has graded every finding, and everything after step 8 acts on those grades
without reviewing anything again.

## Which head

**The stamp names `headRefOid` from step 3's metadata read, which is the head
the diff and every reviewer read.** It never names a commit co-review pushed
itself. Step 12's fix commit is unreviewed by construction: the reconciler
graded findings before the fixes existed, and no step reviews them afterwards.
So once auto-fix pushes, the stamp names the pre-fix head and the gauge reads
the PR's new head as un-reviewed. That is correct, and it is the signal for
the next round that step 12 recommends or declines. A later run that reviews
the new head re-stamps the same comment with that head.

## The call

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-stamp.py" --repo <owner>/<name> --pr <n> --head <headRefOid> --summary "<one line>"
```

**Type the path as SKILL.md renders `${CLAUDE_PLUGIN_ROOT}`**, the absolute plugin
path (the `--plugin-root` value in its allow-rule pre-flight). The variable is
not expanded in this file or in the Bash tool, so a literal copy runs
`/scripts/co-review-stamp.py` and exits `2`.

- `<owner>/<name>` is the repo resolved in step 2. Under `--post` that can be
  a repo other than `cwd`'s.
- `--head` takes the full 40-character sha, never a short one. The script
  refuses anything else, because a short sha would never equal the head the
  gauge compares it against.
- `--summary` is one line naming the reviewers that ran and counting the
  reconciler's verdicts, drawn from the same facts as the reviewer line, for
  example `claude ✅ · codex ✅ · agy ➖ — 7 findings: 2 high, 3 medium, 2 skipped`.
  Never put scores or ledger contents in it.

Call it by the plugin-root path exactly as written, as with the recorder. A
relative `scripts/…` path resolves only inside this plugin's own repo.
SKILL.md's `allowed-tools` grants the call. It writes to GitHub, so run it
unsandboxed.

The script reads the PR's comments, edits this account's existing stamp in
place, and posts one only when there is none. A rerun with the same head and
the same summary writes nothing; a changed summary on the same head edits the
comment in place. If this account has left more than one stamp, it keeps the
oldest and deletes the rest. It never touches another account's comment.

## Reading the outcome

- **Exit 0** prints `STAMPED: <created|updated|unchanged> <url>`. Put
  `stamp ✅ <created|updated|unchanged>` on section 1's reviewer line.
- **Exit 0 with a `co-review-stamp: warning:` line on stderr** means the kept
  stamp was written but a duplicate could not be deleted. The PR names the
  right head, and the next run retries the delete. Put `stamp ⚠️ <outcome> —
  <the warning>` on the reviewer line and carry on.
- **Any other exit** is a warning, never a stop. Put `stamp ❌ not written —
  <the script's error line>` on the reviewer line and carry on. Don't retry
  in a loop. A missing stamp costs the gauge one PR until the next run, and a
  failed run costs the whole review.

## The evidence record

The merge lander needs the round's outcome as data, not prose, and only the
facts it demotes on. So at the end of step 12, in the default disposition
only, co-review stamps again with `--evidence <file>`: the same call, the same
head and summary, plus a JSON file holding one record. The script validates
the record and keeps it in the same comment as a hidden block after the
summary line. A commit status cannot carry it, because its description holds
only 140 characters.

Write the file with the Write tool under
`$HOME/.claude/co-review-input/record/<run_id>.evidence.json`, beside the
recording files, and fill it from this round's final state, after the fixes
and the user's answers:

```json
{
  "head": "<headRefOid>",
  "unresolved_findings": 0,
  "verification_open": 0,
  "consult_calls": [{ "confidence": 85, "kind": "technical" }],
  "next_round": "no further round"
}
```

- `head` is step 3's `headRefOid`, the same sha as `--head`; the script
  refuses a mismatch.
- `unresolved_findings` counts every high or medium finding not applied by
  the end of the round: a deferred judgment call, an open question, an item the
  user declined. Skipped verdicts (wrong, not-applicable, out-of-scope) are not
  unresolved.
- `verification_open` counts verification items left for the user, not yet
  run, or run and inconclusive.
- `consult_calls` has one entry per medium call settled or left provisional
  without the owner: each item step 9's consult decided without asking, and
  each stack-reaching call left provisional under `--non-interactive`. For a
  consult decision, `confidence` is the integer percent it reported (a
  low-stakes call decided without a consultant is `100`), and `kind` is
  `technical`, or `preference` for a call that came down to taste. A
  provisional stack call has `kind: stack` and `confidence: 0`, since no
  consultant decided it. The lander holds the PR on that kind and never
  reads its confidence.
- `next_round` is step 12's recommendation, `another round` or
  `no further round`.

A run that never reaches the end of step 12 leaves the step-8 stamp, which
carries no record, so the lander reads "no evidence" and holds the PR. That is
the intended failure mode. Read it back with
`scripts/co-review-evidence.py --repo <owner>/<name> --pr <n> --head <sha>`,
which prints the record or exits 3 when there is none for that head.
