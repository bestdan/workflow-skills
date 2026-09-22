---
created: 2026-09-22
status: accepted
issue: https://github.com/bestdan/workflow-skills/issues/840
---

# The eval suite's failures are not all routing failures

## Context

`scripts/eval.sh` reported every miss as `❌ FAIL — skills invoked: none` and
deleted the run log, pass or fail. #840 recorded ~2 of 14 rows failing that way
on every run, on a rotating cast, and asked for three things: retain the log,
identify the cause, and validate any fix against the rate rather than a streak.

## What the instrument now separates

One string covered at least six different defects. `scripts/eval-triage.py`
names which, from the same log the harness already had:

| cause             | what actually happened                                                                          |
| ----------------- | ----------------------------------------------------------------------------------------------- |
| `no-result-event` | the run was killed or crashed — no terminal `result` event                                      |
| `api-error`       | the run finished, the API under it failed                                                       |
| `max-turns`       | the ceiling was hit with the skill unfired                                                      |
| `not-surfaced`    | the skill was never in the session's listing to be chosen                                       |
| `wrong-skill`     | another skill fired — a real description collision                                              |
| `no-skill-chosen` | surfaced, clean run, model answered anyway — the routing miss                                   |
| `dirtied-repo`    | the row wrote into the checkout ([#829](https://github.com/bestdan/workflow-skills/issues/829)) |

Only the last two are about descriptions. #834 rewrote two descriptions and the
rows failed again, which is consistent: four of these seven causes cannot be
moved by a description at all.

## The measurement, and what it did not show

Three consecutive full runs, same host, 2026-09-22: **41 of 42 rows passed.**
One failure, `wrong-skill`. `orchestrate-coders` and `select-coder` — which
#840 recorded failing in 3 of 4 runs — passed all three times.

**#840's defect did not reproduce.** Not "was fixed": nothing here changes
routing. The rate moved from ~14% per row to ~2% between two sittings days
apart on one machine, so whatever drives it is not in this repository. The
practical consequence is in `evals/README.md`: a baseline is only comparable to
a fix measured in the same sitting.

Two things were ruled out rather than merely doubted. The old pass check was
**not** silently failing on JSON spacing — the stream is compact, and both old
greps match a real log, replayed against the one retained failure. And the new
check is strictly narrower than the one it replaced: the old regex accepted
`"name":"<skill>"` anywhere in the log, which matches a file path or an agent
name, so it could produce false passes but never a false `none`.

## What the runs did find

**A row killed at the 300s cap can still pass.** `local-review` took 54s in one
run and was killed at the cap in the next — and passed, because its `Skill`
call had already landed. The harness printed the same `✅ PASS` both times.

This is the mechanism that would produce #840's signature exactly, so it is
worth stating precisely what is and is not established. A killed row is a coin
flip: the skill fires before the kill and the row passes, or it does not and the
log ends with no `Skill` call and no `result` event — which the old harness
printed as `skills invoked: none`. The cast rotates because which row runs long
is noise, not a property of a skill, and `orchestrate-coders`/`select-coder` are
adjacent in the manifest, so a slow patch takes out neighbours.

**The failing half of that flip was never observed here** — only the passing
half. The mechanism is inferred from one kill, and this record does not claim it
as #840's cause. It is now instrumented: a truncated run is reported as
`⚠ PASS on a truncated run` and keeps its log, on a pass, because those passes
are the population the misses are drawn from.

**`num_turns` is not comparable to `--max-turns`.** One run reported
`num_turns: 8` under `--max-turns 6`. Whatever `num_turns` counts, it is not
what the flag bounds, so #840's "is it the ceiling?" question cannot be settled
by comparing the two numbers — `error_max_turns` in the `result` event can.

**A prompt can have two right answers.** `prompts/task.txt` routed to `task`
twice and `add-task` once, and both are correct: `task`'s description covers
capture, and `add-task` is the command that performs it. A single expected name
there is a row that fails a third of the time for behaving correctly. The
manifest now accepts `a|b`.

**The eval sessions run `git add`.** #829 found a case writing a report into the
checkout; it also **stages** what it writes, and a broad add swept three
unrelated untracked files from the working tree into the index during these
runs. Nothing was committed. #838's guard detects the residue and deliberately
does not revert it.

**#838's guard cannot see an ignored path.** `git status --porcelain` omits
ignored files, so a case writing into `.claude/` or `temp/` moves neither of its
signals — an eval case did create
`skills/analysis-pipeline/example/.claude/`. That same property is why this
harness keeps its own logs under `temp/`: they cannot convict the next row.

## What is still open

The cause of a `skills invoked: none` failure is **not** identified, because no
such failure occurred in 42 rows. The instrument to identify the next one is in
place, which is #840's first "done when" and the prerequisite for its second.
The truncation mechanism is the leading hypothesis and is now measured rather
than argued: if the `causes:` tally comes back dominated by `no-result-event`
with `rc=124`, raising `CAP` is the fix, and the truncated-pass warning will
have flagged it before it fails.

## Revisit when

- **A `skills invoked: none` failure is caught with its log.** That is the
  evidence this record is missing. Read `cause` and `rc` first: `no-result-event`
  with `rc=124` confirms the truncation mechanism and makes raising `CAP` the
  fix; `no-skill-chosen` refutes it and puts the problem back on descriptions.
- **`⚠ PASS on a truncated run` starts appearing on several rows per run.** The
  cap is then too low for the suite as it stands, and the rows are passing on
  luck. Raise `CAP` rather than waiting for them to flip.
- **A ranking check over descriptions disagrees with the suite again.** #828's
  puzzle was two measurements of two different strings; the next such
  disagreement should be checked against `surfaced` and `cause` before either
  side is believed.
- **`num_turns` and `--max-turns` become comparable**, or the `result` event
  grows a field that says the ceiling was hit. The `max-turns` cause currently
  rests on `subtype == "error_max_turns"` alone.
- **The suite is ever run in CI.** It never has been, so every rate in this
  record is from one machine and one account, which is the most likely
  explanation for the gap between #840's ~14% and the ~2% measured here.
