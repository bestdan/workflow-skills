---
created: 2026-11-12
question: "What did splitting the orchestrator test harness into parts cost and save, and how does ShellCheck's time grow with a file's length?"
feeds: "https://github.com/bestdan/workflow-skills/pull/362"
---

# Splitting the orchestrator test harness (measured 2026-08-15)

**Splitting the monolithic `scripts/test-spawn-orchestrator.sh` into parts cut
two costs at once: the suite's wall time from about 58s to about 21s, and its
ShellCheck bill from about 33s to about 9s.** ShellCheck's time grows roughly
quadratically with a file's length, which is why the lint saving was so large.

These measurements were taken for PR #362 on 2026-08-15 and lived in
`dev_docs/gate-performance.md` until #1504 moved them here. Unless a section
says otherwise they come from a 4-core Intel Xeon @ 2.80GHz Linux box, warm
(the mypy pin fetched, the dprint plugins compiled), at a time when the bats
files still ran serially. The split made 7 parts; the `reserve` part and its
row below were added on 2026-09-09 (#551), and the suite has 11 parts now.
Every line count below is as of those dates.

## Wall time per part

| part            | wall (alone) | lines |
| --------------- | ------------ | ----- |
| `exit-contract` | ~16.5s       | 1253  |
| `status-report` | ~11.8s       | 678   |
| `doctor`        | ~8.2s        | 1132  |
| `supervisor`    | ~5.6s        | 653   |
| `alarm`         | ~5.2s        | 475   |
| `profile`       | ~5.0s        | 882   |
| `restack`       | ~4.2s        | 538   |
| `reserve`       | ~1.3s        | 280   |
| serial sum      | ~58s         |       |
| via the driver  | **~21s**     |       |

The serial sum matched the monolith's ~58s, because every part rebuilds the
prelude's fixture bundle. Running the parts concurrently is what pays for that
overhead. An interleaved A/B of the full `check.sh`, driver concurrent against
driver serial:

```
driver concurrent   42.7s  43.5s  43.6s
driver serial       65.2s  69.5s  68.2s
```

The same configuration measured about 41s on the otherwise idle box, and the
early isolated runs read about 10% faster than the final interleaved ones: the
measuring load itself drifts, which is why the comparison was interleaved.

The `doctor` part first measured 37.8s and settled at about 8s once its caches
were warm.

## ShellCheck's cost by file length

With the gate's own invocation (`shellcheck -s bash --severity=warning -e
SC1090,SC1091`), over syntactically valid prefixes of the monolith (each checked
with `bash -n` first, because a prefix cut mid-construct makes ShellCheck stop
at a parse error and look fast):

| lines | wall   |
| ----- | ------ |
| 1101  | 1.43s  |
| 1733  | 3.10s  |
| 2249  | 4.54s  |
| 3923  | 12.86s |
| 5033  | 26.23s |
| 5816  | 31.03s |

The same assertions linted as 10 files instead of 1 cost 8.3s instead of 33.3s,
with 343 more lines in total.

The coefficient is not universal. `scripts/spawn-orchestrator.sh`, then 6408
lines, linted in about 5.5s, while the monolith took about 31s at 5816 lines.
The largest single-file lint costs at the time were
`scripts/test-research-spike.sh` (about 10s, 3862 lines) and
`scripts/spawn-orchestrator.sh` (about 5.5s).

## Coupling the split found

A lexical `$NAME` scan missed two classes of coupling between parts:

- **Bare names inside arithmetic.** `NOW_EPOCH` was read only as
  `$((NOW_EPOCH + 3600))`, which carries no sigil. It moved into the prelude.
- **Fixture files, not variables.** The exit-contract part read `$CX/auth.log`
  and `$CX/weird.log`, built by the classify-exit tests about 1300 lines
  earlier. The same class produced the prelude's shared fixture bundle:
  `cf.sb`, `wl.json`, `prompt.txt` and the generated `launch.sh`/`job.plist`
  were side effects of the render tests that three other parts consumed.

## Assertion counts

The split suite reported 759 passed and 14 skipped on Linux, against the
monolith's 726/14. The extra 33: the epilogue's per-part assertions (two
notifier-guard structural checks and three caller-repo checks, then ×7 instead
of ×1) gave +30 net of the whole-suite notifier count that moved to the
driver, and the per-PATH guard loop covered 8 composite PATHs where the
monolith covered 5, giving +3. `PSSTUB_PATH`, `SRW_PATH` and `SRL_PATH` were
defined later in the monolith than the point where it asserted, so they were
unreachable there; the epilogue runs last in every part, so the split covers
them.

On macOS the same suite reported 777 passed and 0 skipped: the 14 Linux skips
ran for real, plus three host-fixture assertions Linux reports as bare notes.
