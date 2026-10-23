# Stamping the PR

The procedure behind SKILL.md step 8's stamp sentence. The ledger is local and
write-only, so nothing outside this session can read it. The stamp is the one
record of a co-review that lives on GitHub. It is a single PR comment whose
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
place, and posts one only when there is none. A rerun on an unchanged head
writes nothing. If this account has left more than one stamp, it keeps the
oldest and deletes the rest. It never touches another account's comment.

## Reading the outcome

- **Exit 0** prints `STAMPED: <created|updated|unchanged> <url>`. Put
  `stamp ✅ <created|updated|unchanged>` on section 1's reviewer line.
- **Any other exit** is a warning, never a stop. Put `stamp ❌ not written —
  <the script's error line>` on the reviewer line and carry on. Don't retry
  in a loop. A missing stamp costs the gauge one PR until the next run, and a
  failed run costs the whole review.
