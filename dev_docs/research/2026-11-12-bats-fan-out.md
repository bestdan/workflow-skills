---
created: 2026-11-12
question: "Does running each bats file as its own job shorten the gate, on the 18-core workstation and on CI's macOS runner, and what does it cost?"
feeds: "https://github.com/bestdan/workflow-skills/pull/1488, https://github.com/bestdan/workflow-skills/issues/1452"
---

# Bats fan-out: one job per file (measured 2026-11-11 and 2026-11-12)

**Running each `test/*.bats` file as its own job cut the full gate from 479s
to 217s on the 18-core workstation, and the macOS CI job from a median of 397s
to 209s on the 3-core M1 runner.** It costs no extra CPU on CI, but its tests
together take 6 to 13 times as long to finish there, because 41 processes share 3
cores.

These measurements were taken for #1488 (workstation, 2026-11-11) and #1452 (CI,
2026-11-12, merged as PR #1501). They lived in `dev_docs/gate-performance.md`
until #1504 moved them here. Workstation runs are unsandboxed and warm.

## On the workstation

The 18-core Mac, 2026-11-11, with 40 bats files and 580 tests:

```
bats serial, alone          455s wall   (CPU: 84s user + 128s sys)
bats one job per file       113s wall   (slowest: test/guard-pinned-branch.bats)
full gate, bats serial      479s
full gate, bats per file    217s
```

Serial bats spent more than half its wall time waiting rather than on CPU.

The full gate with bats per file, by check:

| check                                | wall      | why                                                        |
| ------------------------------------ | --------- | ---------------------------------------------------------- |
| `scripts/test-shell.sh`              | **~216s** | 40 bats files, one job each, plus the orchestrator suite   |
| `scripts/test-research-spike.sh`     | ~155s     | hundreds of Python invocations, mostly interpreter startup |
| `scripts/test-pr-review-comments.sh` | ~72s      |                                                            |
| `scripts/test-preflight-consent.sh`  | ~68s      |                                                            |
| `scripts/test-verify-fix.sh`         | ~47s      |                                                            |
| **full `just check`**                | **~217s** | CPU: 245s user + 388s sys; 200–235s over four runs         |
| `just check-fast`                    | ~127s     | bounded by the bats files, which it still runs             |

The gate then started about 140 jobs at once, so a single check's time there
says as much about contention as about the check.
`test-research-spike.sh` took about 81s in a run where the bats files ran
serially and about 155s in one where they ran in parallel. The bats files took
113s run alone in parallel and about 216s inside the full gate.

### Where the timing-warning thresholds came from

The same PR added the gate's timing warnings. The full-gate budget of 300s sits
above the 200–235s spread over four runs and below the 479s of serial bats; the
`--fast` budget of 180s sits above one 127s run. The jump warning's 30s floor
came from contention: single checks swung about 2× between loaded runs, and a
10s floor warned on two different checks in two consecutive green runs
(`test-plan-graph.sh` 14s → 29s, `typecheck.sh` 11s → 25s).

## On the macOS CI runner

CI's `macos` job runs only `scripts/test-shell.sh`. A throwaway branch (#1493,
closed) ran that job as a matrix of six CI jobs, each on its own runner: three
ran all 40 bats files in one serial `bats` process, and three ran one
background process per file, as `main` does. Both arms ran `bats --timing`,
which `main` does not pass. The whole matrix then ran a second time. Run
[43203256575](https://github.com/bestdan/workflow-skills/actions/runs/43203256575),
attempts 1 and 2. Job duration, with the `test-shell.sh` step in brackets:

```
attempt 1   serial     384s (364)   481s (457)   239s (225)*
            per file   209s (187)   175s (157)   268s (249)
attempt 2   serial     445s (424)   397s (374)   332s (313)
            per file   229s (210)   191s (173)   104s  (87)*
```

`macos-latest` is not one machine. The two runs marked `*` landed on an "Apple
M2 Pro (Virtual)" runner with 5 cores and 14 GB; the other ten ran on "Apple M1
(Virtual)" with 3 cores and 7 GB.

| runner      | serial               | per file             |
| ----------- | -------------------- | -------------------- |
| M1, 3 cores | 332–481s, median 397 | 175–268s, median 209 |
| M2 Pro, 5   | 239s (one run)       | 104s (one run)       |

On the M1 runner the slowest per-file run (268s) beat the fastest serial run
(332s), so the gain is outside run-to-run noise, and all twelve CI jobs passed.
The spread within one arm on one runner type was still 90–150s, and the runner
type alone moved a run by about as much.

Per file cost no extra CPU there (attempt 1 on M1: serial 3m57s–4m33s user,
per file 3m31s–4m57s). It cost latency per test. No serial test took over 5s;
per file, 23 to 92 tests per M1 run took over 10s, and the slowest took 61s
against 3.6s serial (`test/guard-foreign-worktree.bats`). Summed over every
test, attempt 1 on M1 came to 2215–3581s per file against 276–357s serial.
Nothing failed. `test/await-pr-review.bats`, whose 5s budget concurrency had
broken on a workstation and which now uses `LAND_TIMEOUT=60`, had a slowest
test of 9.1s.
