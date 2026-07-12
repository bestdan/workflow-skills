# Auto-pilot hardening: review feedback

Written after co-reviewing PRs #188–#191 (auto-pilot hardening tasks 11, 14, 15, 16). Three of the four carried defects that a green suite did not catch; two were merge blockers, and one was a reproducible data-loss bug. This is a note about the _class_ of thing that got missed, not about those four PRs.

## Start with what worked

- **#188 was clean under adversarial review.** No defects.
- **The PR bodies are unusually good.** Evidence sections, observed behavior rather than "the suite is green", and a falsifiable "How to evaluate" — _"a 'no' here invalidates the change."_ That is better practice than most engineers manage.
- **The line-survival audit was the right instinct.** After a hand-resolved rebase, checking that none of the parent PR's lines were silently dropped is exactly the paranoia the situation calls for. I re-ran it independently: 101 of 101 lines present in both branches. It passed — and it was auditing the wrong invariant. See "an early return orphans everything downstream".

The failure mode below is not sloppiness. It is a specific blind spot, and it repeated four times.

## The dominant pattern: the test harness diverges from production at exactly the point the invariant is about

Three defects are the same bug in different clothes.

| Test                              | What it asserted against                             | What production actually does                                              |
| --------------------------------- | ---------------------------------------------------- | -------------------------------------------------------------------------- |
| Doctor invariant 5 (orphan prune) | A fixture with the RUN.md **branch cell pre-filled** | Leaves the cell `-` for the whole live-dispatch window                     |
| Doctor invariant 3 (vanished PR)  | A fake `gh` that **exits 0 with empty output**       | Real `gh` **exits non-zero** on a missing PR                               |
| Alarm channel (systemic halt)     | `supervisor-check` **called directly**               | The wrapper runs `supervisor-gate` **first**, which can `exit 0` before it |

In each case the suite was green and the feature did not work:

- Invariant 5 force-removed a **live** worker worktree (reproduced: `WORKTREE DESTROYED`).
- Invariant 3's park-on-vanished-PR path is **unreachable in production** and always would have been.
- The alarm never fired on precisely the wakes that prove a run is stuck.

A stub is a **claim about an external contract**. None of these claims were checked.

**What to do instead**

- Verify each stub's contract against the real thing **once**, and write the check down. `gh pr view 999999; echo $?` takes five seconds and would have killed two of these findings.
- A stub must reproduce the real thing's **failure semantics** — exit codes, not just stdout.
- Where the artifact under test is _generated_ (the launchd wrapper), **drive the real generated artifact**. Never re-implement its call sequence inside the test; that is how the alarm test came to skip the very gate that broke it.

### The same pattern, pointed the other way: side effects escaping the harness

The stub gap has a mirror image, and the suite has it. `bash scripts/check.sh` on any Mac fires **four real macOS desktop notifications** — verified by shadowing `osascript` on `PATH` and counting: 554 tests pass, four calls escape to the real binary.

```
auto-pilot ALARM — com.autopilot.test.fatal   fatal-auth: non-retryable auth failure…
auto-pilot ALARM — com.autopilot.test.np      no-progress: no forward progress…
auto-pilot ALARM — com.autopilot.test.eh      no-progress: …
```

The dedicated alarm tests stub the notifier correctly. The supervisor-check and halt tests trigger an alarm **incidentally** — via `_supervisor_halt` → `alarm` → `_alarm_notify`, which does `command -v osascript` and finds the real one — and those tests never stubbed it. The suite stays green, so nothing flags it, and a fixture's alarm text lands on a human's desktop.

**What to do instead**: shadow every external side-effecting binary (`osascript`, `terminal-notifier`, `open`, `launchctl`) **globally for the whole suite**, not per-test. Per-test stubbing is a discipline that decays: the next test that triggers an alarm incidentally re-opens the hole. Then assert the escape count is **zero** — that is a one-line acceptance criterion, and it is the only thing that keeps it closed.

The general rule: **a test that can reach the real world is a test that will, on someone else's machine, at 3am.** Ask of any suite: what side effects can escape it, and what asserts that none did?

## The bugs live in the window between two writes

Every fixture in the suite is built in the settled, after-both-writes shape. The bugs were all in the gap:

- The RUN.md row is written **before** the branch cell is filled in → a live worker looks like an unclaimed orphan.
- The circuit breaker writes `status: systemic` **before** `supervisor-check` runs → an interrupted wake leaves an unannounced halt.

**What to do instead**: for any state machine, walk each pair of consecutive writes and ask _"what does a concurrent reader see between them?"_ — then fixture **that**. Do it unconditionally for anything destructive. A `git worktree remove --force` earns this treatment on its own.

## An early return silently orphans everything downstream of it

#188 added `exit 0` at the pre-invoke gate. It **deleted nothing**. It added a return path _in front of_ two downstream consumers, and two PRs authored in parallel tripped over it independently:

- #191 lost its **alarm scan** (the gate short-circuited before it) — the silent-halt bug the PR existed to fix, reintroduced by the PR merged an hour earlier.
- #190's **heartbeat** landed below the gate on rebase — with **no conflict marker** — so a healthy multi-hour pause would read as `STALL`.

This is why line-survival auditing could not see it: nothing was dropped, something was **bypassed**.

**What to do instead**

- When adding an early exit to a shared path, **enumerate everything downstream** and classify each as _must still run_ or _correctly skipped_.
- Then make the seam **explicit in the code**, so the next person's line lands on the right side by default. The wrapper now reads: `supervisor-scan` above the gate (every wake, supervisor bookkeeping), agent invocation below it — with a comment saying so. Without that, this bug arrives a third time.

## The PR body outran the code

Three load-bearing claims were sincere, well-written, and false:

- _"The scan runs on every wake, including an exit-0 one."_ — False for gate-closed wakes, the only ones that matter.
- _"Every condition must hold, not most of them."_ — The `pending` guard it defended was near-dead code.
- _"The guard's original purpose survives the fix."_ — The new conjunction adds nothing over a carve-out already on `main`.

Sincere-and-wrong is the dangerous combination: the prose is persuasive enough to substitute for re-deriving the behavior from the merged code.

**What to do instead**: every load-bearing claim in a PR body should **name the test that fails when the claim goes false**. If you cannot point at that test, you have written a hypothesis, not a result.

## Two mechanical checks that would have caught most of this

### 1. Mutation-test every new guard

Delete the guard, run the suite, confirm the **specific new tests** go red. Every fix in this batch was verified this way:

| Guard removed                   | Tests that went red |
| ------------------------------- | ------------------- |
| Pre-gate `supervisor-scan` line | 14                  |
| Invariant 5 liveness gate       | 7                   |
| Heartbeat above the gate        | 2                   |

This is the only check that distinguishes **"a test exists"** from **"a test guards"**. It takes a minute. Run against the original invariant-5 test, it would have stayed green with the guard deleted — which is the entire story.

### 2. Grep your own diff for swallowed failures

Same shape, twice: a comment declares a posture, and one path does not implement it.

- `alarm`'s `die` is `exit`, which **escapes `|| true`** — an unwritable sentinel aborted a halt before its teardown.
- Invariant 4 discarded two `gh` write exit codes and then recorded `"repaired"` unconditionally — a **durable false record**, in the skill whose stated purpose is eliminating silent lies.

Search for `>/dev/null 2>&1` with no rc check, and for `|| true` wrapped around anything that can `exit`.

## The one that stings

**Re-run the acceptance criterion after a rebase — not just the suite.**

Every one of these PRs carried a user-run criterion. #191's read:

> Confirm that **within one supervisor interval** you get a desktop notification naming the run and telling you to re-authenticate […] instead of 4 hours of silence. **A "no" — a stalled run that stays silent — invalidates the change.**

Run once against the rebased branch, that check catches the blocker immediately. The author **wrote the exact test that would have found the bug, and then did not run it after the merge that broke it.** A clean rebase and a green suite were taken as sufficient — and the whole thesis of task 18 is that they are not.

So the guidance is not "rebase more carefully." It is:

> **A rebase invalidates behavioral evidence, not just textual mergeability.**

The suite re-runs for free. The acceptance criterion is the one that must be re-run **by hand**, and it is the one that is load-bearing.

### Implication for task 21

If task 21 is going to enforce re-verification after a restack, it should enforce **re-running the PR's own stated user-run criterion against the rebased head** — not line survival, which passed cleanly on both broken branches.

## Checklist

Before opening a PR:

- [ ] Every stub's contract checked against the real binary at least once (exit codes, not just stdout).
- [ ] Generated artifacts are driven **as generated** — the test does not re-implement the call sequence.
- [ ] No side effect can escape the suite: every external side-effecting binary shadowed **globally**, with an assertion that the escape count is zero.
- [ ] Fixtures include the **in-flight** state between each pair of consecutive writes, not only the settled one.
- [ ] Any new early return: everything downstream classified _must-run_ vs _skip_, and the seam made explicit in code.
- [ ] Every load-bearing claim in the PR body names a test that fails if the claim goes false.
- [ ] Each new guard mutation-tested: delete it, confirm the new tests go red.
- [ ] Diff grepped for discarded exit codes and `|| true` around anything that can `exit`.

After any rebase or restack, before merge:

- [ ] Suite re-run (free — necessary, not sufficient).
- [ ] **The PR's own "How to evaluate" criterion re-run by hand against the rebased head.**
- [ ] The merged result audited for changes that landed **without** a conflict marker — those are the dangerous ones.
