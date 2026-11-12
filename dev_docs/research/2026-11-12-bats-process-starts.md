---
created: 2026-11-12
question: "On the work laptop, does splitting the slowest bats files or starting fewer processes in them shorten the gate?"
feeds: "https://github.com/bestdan/workflow-skills/issues/1496"
---

# The slowest bats files on the work laptop (2026-11-12)

**Splitting the slowest bats files would not shorten the gate on the work
laptop, because the gate is bounded by how many processes the whole run
starts. Starting fewer processes in the three heaviest hook suites (PR #1505)
took the full gate from a median of 182s to 173s.**

All measurements are from the 18-core work laptop, unsandboxed, 2026-11-12,
for #1496. They lived in `dev_docs/gate-performance.md` until #1504 moved them
here. The machine-wide ceiling on process starts behind them is in
[the process-start record](2026-11-12-gate-process-start-ceiling.md).

## Why splitting would not help

- **The gate leaves the cores idle.** A full run took 181s wall for 227s user
  and 340s sys: about 3 of 18 cores busy on average, with sys above user.
- **Process starts hit a ceiling.** N concurrent loops of `/usr/bin/true`
  level off at 580–720 starts/s in total from N=9 up to N=72;
  `python3 -c pass` levels off at about 375/s from the same N=9. One loop
  alone manages 272 starts/s.
- **Running the bats files in parallel barely beats running them in series.**
  The 40 files run one after another took 293s; `scripts/test-shell.sh` runs
  them in parallel in 180s.
- **A file is slow mainly because it shares the machine.**
  `guard-pinned-branch` took 30s alone, 46s beside five other slow files, and
  167s in the full gate. Inside each slow file the cost is spread evenly
  (median 0.5–1.4s a test), so no single test dominates.

Splitting a file redistributes the same process starts across more jobs, so on
this machine it buys nothing.

## Starting fewer processes (PR #1505)

Two changes in `guard-pinned-branch`, `guard-foreign-worktree` and
`worktree-create-hook`:

- **Build fixtures once.** `setup_file` builds the fixture repos, and each test
  copies them with one `cp -R` instead of about fifteen to twenty `git` calls.
- **Build hook payloads in bash.** `json_string` in `test/test_helper.bash`
  replaces the `python3` that built each payload, and `guard-foreign-worktree`
  reads the verdict with a pattern match instead of a second `python3`.

Interleaved, three passes each: the full gate went from a median of 182s
(181–187s) to 173s on every pass. `guard-pinned-branch` alone went from 30.5s
to 21.7s.

Solo CPU (user plus sys) of the heaviest bats files after the change:
`guard-foreign-worktree` 17.1s, `co-review-ledger-sync` 16.5s,
`guard-pinned-branch` 14.9s, `worktree-remove` 12.8s. The 40 files together
take about 164 CPU-seconds.
