---
created: 2026-09-21
status: accepted
issue: https://github.com/bestdan/workflow-skills/issues/828
---

# A same-named command's `description` is what routes, not `SKILL.md`'s

## Context

`select-coder` and `orchestrate-coders` failed `scripts/eval.sh` in both full runs
on 2026-09-21, and failed the same way — **no skill invoked at all**, not a wrong
one. #828 recorded the puzzle that made a description fix look unpromising: the
same 14 prompts scored against every skill `description` as a typed Choice put
both skills **first**, by margins of 0.41 and above, in four consecutive passes.
The descriptions discriminated on paper while the live session loaded nothing.

Both halves of that were true, because they were measuring two different strings.

## What was actually wrong

Where this plugin ships a `commands/<name>.md` beside a `skills/<name>/SKILL.md`,
the **command's** `description` is what appears in the model's skill listing. The
`SKILL.md` `description` is shadowed and never reaches the model, so it cannot
route anything.

Six names are in that position, and all six diverge:

| name                 | `SKILL.md` opens                       | `commands/` opens (what the model sees) |
| -------------------- | -------------------------------------- | --------------------------------------- |
| `assess-task`        | "Use when you need a structured read…" | "Profile a coding task along…"          |
| `auto-pilot`         | "Unattended autonomous mode…"          | "Launch an unattended auto-pilot run…"  |
| `deliver-task`       | "Take ONE identified task…"            | "Deliver ONE task through…"             |
| `orchestrate-coders` | "Use when the user wants…"             | "Orchestrate coding work across…"       |
| `select-coder`       | "Use when choosing which coder…"       | "Recommend which coder agent…"          |
| `tutor`              | "Teach the user, incrementally…"       | "Teach the user until…"                 |

The `SKILL.md` descriptions are trigger-shaped — they open "Use when…" and carry
worked example phrasings. The command descriptions are noun-phrase capability
summaries written for the slash-command picker, which is a different job. Four of
the six are eval manifest rows: three of the suite's four non-clean rows, plus its
one shadowed pass.

| manifest row         | shadowed | run 1 | run 2 |
| -------------------- | -------- | ----- | ----- |
| `select-coder`       | yes      | FAIL  | FAIL  |
| `orchestrate-coders` | yes      | FAIL  | FAIL  |
| `tutor`              | yes      | FAIL  | PASS  |
| `assess-task`        | yes      | PASS  | PASS  |
| `task`               | no       | FAIL  | PASS  |
| the other 8 rows     | no       | PASS  | PASS  |

Three of the four failing rows across the two runs are shadowed.
`assess-task` survives its shadowing by luck rather than by design: its command
description happens to list its six dimensions, and its eval prompt names those
same six almost verbatim, so the weaker framing still wins on overlap alone.

**So it was not the description wording, and not a collision with a neighbouring
skill.** It was the third option #828 listed — how the plugin surfaces these two.

### The 0.41 margin, which #828 asked to be measured rather than assumed

#828 flagged that the tightest margin anywhere in the typed run (0.41,
`select-coder` vs `orchestrate-coders`) sat on exactly these two skills, and called
two cases a coincidence until someone measured it. It got measured, accidentally,
and the answer has two halves.

**The margin itself was not the cause.** `jev-description-collision.py`'s
`load_descriptions()` globs `skills/*/SKILL.md`, so that number is a property of
text the live model never reads for these two names. A tight margin there cannot
produce a live routing failure, because nothing live consumes it.

**The adjacency it reflects is real, and it does bite — in the surfaced text.**
The first attempt at this fix widened `orchestrate-coders`' command description
with "farm it out" / "don't write it yourself" examples. That phrasing is the
opening clause of `select-coder`'s own eval prompt ("Before farming them out,
figure out which agent and model is the best fit"), and `select-coder` promptly
failed a run with `skills invoked: none` — the no-fire signature of a model that
cannot cleanly separate two candidates, not a mis-route to the neighbour. Backing
that out in favour of an explicit boundary clause on each description fixed it.

So the two skills are genuinely adjacent in meaning; that adjacency narrows the
typed margin _and_ makes them the pair most in need of an explicit boundary in the
text that actually routes. The margin was a symptom of the same adjacency, read off
the wrong string.

### Measurements

| stage                                   | `select-coder`       | `orchestrate-coders` |
| --------------------------------------- | -------------------- | -------------------- |
| before (#828's two runs)                | FAIL, FAIL           | FAIL, FAIL           |
| trigger clause only, no boundary clause | PASS, PASS, **FAIL** | PASS, PASS, PASS     |
| trigger clause + boundary clause        | PASS, PASS, PASS     | PASS, PASS, PASS     |

The middle row is why the boundary clause is in the fix rather than only the
trigger phrasing: two green runs looked like enough and were not, which is the
point #828 made when it asked for three consecutive.

## Decision

**Fix the surfaced string: the `description` in `commands/select-coder.md` and
`commands/orchestrate-coders.md`.** Each now opens with its `SKILL.md`'s "Use
when…" trigger clause, keeps its own capability tail so the slash-command picker
still reads well, and closes with a **boundary clause naming the other skill** —
`select-coder` says choosing is the whole job and it never dispatches, even when
the tasks are about to be farmed out; `orchestrate-coders` says the dispatching
is the ask, and that choosing the agent is `select-coder`. The boundary clause is
load-bearing, not decoration: see the middle row of **Measurements** above.

`SKILL.md` is deliberately left alone. Editing it would change the typed check's
margins and nothing else — the reverse of what is wanted.

**Consequence for #828's third acceptance condition.** It asks that the typed
check be re-run if a `description` changes, since its margins move. They do not
move here: the check reads `skills/*/SKILL.md`, and this change touches only
`commands/`. Nothing to re-run, and that non-movement is itself the finding — the
cheap check cannot predict this suite for any of the six shadowed names.

## Revisit when

- **A seventh shadowed name, or a divergence in one of the six.** Nothing enforces
  the two descriptions staying in step; this was found by hand. A gate check and
  re-pointing `jev-description-collision.py` at the surfaced text are the
  recurrence-prevention half, deliberately left out of this change and filed
  separately.
- **`tutor` flapping again.** It is shadowed and unfixed here, because #828 scoped
  itself to the two stable failures.
- **`task` failing again.** It is the one non-clean row this explains nothing
  about; it is unshadowed and carries two manifest rows under one skill name.
- **The harness surfacing skills differently.** The shadowing is host behaviour,
  observed rather than specified; if a future Claude Code prefers `SKILL.md`, this
  fix becomes inert and the `SKILL.md` text takes over again.
