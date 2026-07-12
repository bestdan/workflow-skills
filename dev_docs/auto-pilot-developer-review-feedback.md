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

## A guard that counts what it catches can never report what got away

Added after co-reviewing #193 (task 26). This one is worth internalizing, because the guard in question was written **specifically** to stop the leak it then failed to stop.

The suite shadows `osascript`/`terminal-notifier`/`open` in a `$GUARD` dir prepended to `PATH`, and asserts two things: the notifiers resolve inside `$GUARD`, and the guard log is non-empty (alarms really did route through it). Both passed. The suite was still firing **15 real desktop notifications per run**.

The reason is structural. Both assertions inspect the **inherited** `PATH`. Every fixture that _overrides_ `PATH` — `PATH="$STUB_PATH" "$SCRIPT" supervisor-check …` — escapes them completely, and those stub dirs carry no `osascript`, so `alarm` resolved `/usr/bin/osascript` and popped a real notification. The guard could not see it: **an escaped call never reaches the guard log**, so the count stayed plausible and the suite stayed green while leaking.

The general form:

> **A detector that measures only its own successes cannot detect its own bypass.** "The guard caught N" is evidence the guard is _reachable_, never evidence it is _unbypassable_.

The fix is to assert the property at its source rather than at the point of capture: for every composite `PATH` the suite constructs, `osascript` must resolve to something inside the test tree. That is checkable without catching anything, and it fails when a new fixture forgets the guard — which is the case the counter is blind to.

Ask of any guard, harness, or invariant check: **what would a bypass look like, and would this assertion still pass?** If it would, the assertion is measuring the happy path.

## The same law, now in the tests themselves (#195–#199)

Added after co-reviewing the second batch. The law above was stated about a *guard*. It is really about **any check**, and the tests are checks. In this batch **the code was largely right and the tests could not detect their own failure** — four times, in four different disguises. Read this section as the general case of the one above, not as a new idea.

### 1. A substring assertion against a whole artifact cannot tell you _where_ the string is (#197)

The test asserted `--park-limit 7` appears in the generated wrapper body. That string is emitted on **two** lines — `supervisor-scan` and `supervisor-check`. Delete it from the `supervisor-scan` emission **alone** — the half **above the gate**, and therefore the only park-storm alarm that runs on a gated wake — and the suite still passes. Duplicated emissions **mask each other**.

**And the mutation test did not catch it, because the mutation was too big.** It removed the flag from *both* sites at once, went red, and proved nothing about a partial bypass.

> **Mutate the smallest unit — one call site, one line — not the feature.** A mutation that deletes the guard everywhere at once cannot detect a partial bypass, which is the failure that actually happens: someone edits one of two emission sites.

Assert **per emission site** (anchor to the line that emits it), not against the concatenated artifact.

### 2. When the human channel and the machine channel disagree, the machine channel is the one that lies (#195)

Doctor invariant 5 printed `skipped (undetermined)` to stdout — correct — while its machine-readable summary reported `ok=7 … skipped=0`. **A clean bill of health for a destructive invariant that never ran.** Everything downstream that consumes the summary (an operator scanning it, an alarm rule, task 20's status report) sees green.

> If a check has both a **human** channel and a **machine** channel, **assert them against each other.** A skip must be *counted* as a skip. Silence in the machine channel is indistinguishable from success — which is this whole system's original sin (`exit 0`, `is_error: false`).

### 3. When the protection might be accidental, assert the _attempt_, not the outcome (#195, again)

I5's effect-level assertions ("the WIP survived", "the worktree is still there") **pass with the bug present**. The unfixed code survives *by luck*: `git worktree remove --force` fails on the very corruption that broke the reads. The test was measuring an outcome that two different mechanisms produce — one designed, one accidental — and could not tell them apart.

> Where a destructive step can fail for the **same reason** the guard failed, asserting "nothing was destroyed" is worthless. Assert that the destruction was **never attempted**.

### 4. A doc eviction must be audited claim-by-claim, and a definition has action at a distance (#198)

The `SKILL.md` cut dropped **three claims out of the tree entirely**, including the definition of **"fails outright."** That is not a lost sentence; it is a lost *term*, and the term is load-bearing elsewhere: without it a **park** reads as a failure and gets **re-dispatched** — defeating parking and the retry bound **together**. The verification ("I grepped the reference for it") checked that the *topic* was present, not that the *claim* was.

> An eviction diff is reviewed **claim-by-claim against the destination file**. "The reference covers this topic" is not evidence the claim survived. **Definitions are the highest-risk eviction**, because their removal breaks code that never mentions them.

### 5. Corroboration means a different **writer**, not a different **field** (#196)

This is the deepest one, and the PR's headline claim — *"an authority the agent cannot forge"* — was **false**. `paused_until` is written **by the agent, into the same `RUN.md`, in the same write** as the `status: paused` it is supposed to corroborate. Two fields from one writer are **the same claim twice**. Reproduced: an immortal agent surviving **six consecutive zero-progress wakes**.

> An authority is independent only if a **different process** writes it. A second field in the same agent-written file is not a second authority. Corroborate against something the agent does not control — the **rate-window signal** the pause claims to be waiting on, or supervisor-side state the agent cannot write.

**And the gate compounds it.** Any future `paused_until` closes `supervisor-gate`, which **short-circuits the wrapper before `supervisor-check` ever runs**. So a cap placed in `supervisor-check` sits on the one code path a wedged pause guarantees will never execute. **The cap must live in `supervisor-scan`, above the gate.**

That is the **third** time the above/below-the-gate seam has produced a merge blocker — #191's alarm scan, #190's heartbeat, now #196's cap. It is no longer a mistake; it is a **structural trap**, and the question below belongs in every review:

> **On which wakes does this code actually run?** Anything that must run on a *gated* wake — bookkeeping, alarms, caps, heartbeats, reports — belongs **above** the gate. Anything downstream of the agent belongs below it. If you cannot say which side your line is on, you have not finished the change.

## Checklist

Before opening a PR:

- [ ] Every stub's contract checked against the real binary at least once (exit codes, not just stdout).
- [ ] Generated artifacts are driven **as generated** — the test does not re-implement the call sequence.
- [ ] No side effect can escape the suite: every external side-effecting binary shadowed **globally**, with an assertion that the escape count is zero — and check that no fixture **overrides `PATH`** past the shadow (a guard that counts what it catches cannot see what got away).
- [ ] Fixtures include the **in-flight** state between each pair of consecutive writes, not only the settled one.
- [ ] Any new early return: everything downstream classified _must-run_ vs _skip_, and the seam made explicit in code.
- [ ] Every load-bearing claim in the PR body names a test that fails if the claim goes false.
- [ ] Each new guard mutation-tested: delete it, confirm the new tests go red — and **mutate the smallest unit** (one call site, one line), never the whole feature. A mutation that removes the guard everywhere at once cannot detect a **partial** bypass, which is the failure that actually happens.
- [ ] Every assertion **anchored to the site it is about**. A substring match against a whole generated artifact cannot tell you _where_ the string is, and duplicated emissions mask each other.
- [ ] **On which wakes does this code run?** Anything that must run on a **gated** wake (bookkeeping, alarms, caps, heartbeats, reports) is **above** the gate; anything downstream of the agent is below it. Three merge blockers have come from this seam.
- [ ] Any **machine-readable summary** asserted **against the human channel** — a skip must be _counted_ as a skip. `ok=N skipped=0` for an invariant that never ran is a false clean bill of health.
- [ ] Where a destructive step could fail for the **same reason** the guard failed, assert the destruction was **never attempted** — not merely that nothing was destroyed (that passes by luck).
- [ ] Any "corroboration" comes from a **different writer**, not a second field written by the same actor in the same file. Two fields from one writer are the same claim twice.
- [ ] Any doc eviction audited **claim-by-claim against the destination file** — "the reference covers this topic" is not evidence the claim survived. **Definitions** are the highest-risk eviction: removing one breaks code that never mentions it.
- [ ] Diff grepped for discarded exit codes and `|| true` around anything that can `exit`.

After any rebase or restack, before merge:

- [ ] Suite re-run (free — necessary, not sufficient).
- [ ] **The PR's own "How to evaluate" criterion re-run by hand against the rebased head.**
- [ ] The merged result audited for changes that landed **without** a conflict marker — those are the dangerous ones.
