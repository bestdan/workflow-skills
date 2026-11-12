---
created: 2026-11-12
question: "Where does the full gate's time go on the 18-core work laptop, and does capping how many gate jobs run at once shorten it?"
feeds: "https://github.com/bestdan/workflow-skills/issues/1498"
---

# Gate time on the work laptop: the process-start ceiling (2026-11-12)

**Capping concurrency does not shorten the gate on the laptop, so no cap
ships.** With at most 9 jobs running at once, the full gate took a median of
185s, against 179s uncapped and 177s on `main`. A cap of 18 matched uncapped.
Cap 9 cut the gate's CPU by about 4% (551 against 575 CPU-s), so contention
between gate jobs explains little of the 170 CPU-s gap described below. The
rest of the gate's time is process starts, and the way to win it back is to
start fewer of them.

All measurements are from the 18-core work laptop (host `NYC-BETTERMENT*`),
unsandboxed, on 2026-11-12. Process starts there hit a ceiling for the whole
machine. CrowdStrike Falcon, which vets every launch, is the likely cause; no
run compared this machine with and without it.

## Question

`scripts/check.sh` starts every check at once, and `scripts/test-shell.sh` and
`scripts/test-spawn-orchestrator.sh` start all of their suites at once as well,
so a full run has about 150 jobs going at the start. On the laptop that run
left most of the cores idle. #1498 asked whether starting fewer jobs at a time
would get more done.

## Process starts hit a ceiling

The benchmark runs N concurrent loops of 500 `/usr/bin/true` and reports total
starts per second. The laptop runs Falcon as an Endpoint Security extension
(`com.crowdstrike.falcon.Agent` 8.10 in `systemextensionsctl list`); the other
three machines do not run it.

| N loops | work laptop (18 cores, Falcon) | Mac mini (M4, 10) | MacBook (M3 Pro, 11) | lindev (Linux, 12 threads) |
| ------- | ------------------------------ | ----------------- | -------------------- | -------------------------- |
| 1       | 272                            | 760               | 859                  | 1132                       |
| 9       | 716                            | 3223              | 2452                 | 11673                      |
| 18      | 579                            | 3445              | 3548                 | 13529                      |
| 72      | 619                            | 3126              | 4207                 | 13300                      |

Without Falcon, throughput climbs to about the core count and holds there, at
5 to 21 times the laptop's ceiling. Even a single loop runs 2.8 to 4.2 times
faster. `python3 -c pass` levels off at about 375/s on the laptop from N=9. The
runs from the other machines are in
[PR #1505's comments](https://github.com/bestdan/workflow-skills/pull/1505).

## Where the gate's time goes

A full run took 181s wall for 227s user and 340s sys before #1505, and 173s
after it. That is about 3 of 18 cores busy on average, with sys above user.

Each unit run alone, one at a time, with CPU as user plus sys:

| Unit                                      | Wall (s) | CPU (s)        |
| ----------------------------------------- | -------- | -------------- |
| the 40 bats files, together               | —        | 164            |
| `scripts/test-spawn-orchestrator.sh`      | 71.1     | 127.8          |
| `scripts/lint-shell.sh`                   | 73.5     | 21.5           |
| `scripts/test-research-spike.sh`          | 21.5     | 16.0           |
| `scripts/test-task-dashboard-snapshot.sh` | 8.7      | 6.9            |
| the other ~95 units                       | —        | under 6.4 each |

The orchestrator suite's 11 parts sum to 103 CPU-s when run one by one; the
largest are `exit-contract` (25.0), `doctor` (18.1) and `alarm` (9.5). The
bats files lead with `guard-foreign-worktree` (17.1), `co-review-ledger-sync`
(16.5), `guard-pinned-branch` (14.9) and `worktree-remove` (12.8).

The units sum to about 394 CPU-s alone, against about 566 in a full parallel
run. The 170 CPU-s gap was assumed to be overhead from contention, and was
never proven to be; recovering it was the case for a cap.

## What was ruled out

- **Batching `research-spike.py` calls (#1497, closed as not planned).** That
  harness is about 3% of the gate's CPU. Each of its 233 calls costs about 43ms:
  15 for the interpreter, 15 for imports, 13 for compiling the 4,111-line
  script. The closing comment on
  [#1497](https://github.com/bestdan/workflow-skills/issues/1497) has the trace.
- **A test-side fix for the orchestrator suite.** Its cost is
  `spawn-orchestrator.sh` doing real work. Parsing the 8,507-line script takes
  about 5ms. The `doctor` subcommand averages 382ms over the 41 calls in the
  `exit-contract` and `doctor` parts, spread thinly across many small subshells
  and `git` calls, with no hotspot in the trace. Replacing the per-assertion
  `grep` in the prelude's `have`/`lack` with a bash match would save about
  3 CPU-s.

## The cap (#1498)

The cap was a token pool shared by all three scripts that start gate jobs. The
outermost of them opened a FIFO on a fixed file descriptor and wrote one byte
per slot into it. Each leaf job then read one byte before it started and wrote
it back when it ended. Scripts that only wait on their children took no slot.
A blocking one-byte read is how Bash 3.2 limits jobs without `wait -n`, and it
costs no extra process per job. The `CHECK_JOBS` environment variable set the
slot count and left the gate uncapped when unset.

Interleaved A/B, `scripts/check.sh --no-cache`, three passes of four arms in
rotation. `main` ran from a detached worktree at a4ad7868; the other arms ran
the branch with `CHECK_JOBS` unset, 9 and 18. Wall seconds:

| Pass   | `main` | uncapped | cap 9 | cap 18 |
| ------ | ------ | -------- | ----- | ------ |
| 1      | 182    | 196      | 190   | 174    |
| 2      | 177    | 179      | 185   | 179    |
| 3      | 175    | 177      | 185   | void   |
| median | 177    | 179      | 185   | ~177   |

Cap 18's third pass is void: Homebrew upgraded `git` at 12:02, during that
run, which broke the run's `git` calls and failed one orchestrator assertion
in it. In every branch run, `dprint` and `dev-docs-layout` failed on this
record, then a draft, and neither is a long check.

CPU, `/usr/bin/time -p` around the whole gate, two runs per arm, alternating:

| Arm      | Wall (s)      | User (s)      | Sys (s)       | CPU (s)   |
| -------- | ------------- | ------------- | ------------- | --------- |
| uncapped | 184.1 / 184.3 | 224.3 / 224.0 | 351.9 / 349.0 | 576 / 573 |
| cap 9    | 190.0 / 182.1 | 220.8 / 218.0 | 337.6 / 325.5 | 558 / 543 |

Capped at 9 jobs, the long units queue behind short ones:
`scripts/test-shell.sh` took 184–189s capped at 9, against 172–175s on
`main`. The cap cut sys time slightly but did not bring the CPU anywhere near
the 394 CPU-s the units take alone. So most of that gap is not contention
between gate jobs, and what it is remains unmeasured.

A cap could still help on a machine where starts scale with cores and the gate
oversubscribes them. The gate's time on those machines has not been
measured.
