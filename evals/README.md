# Behavioral evals

Opt-in, non-blocking checks that Claude **auto-invokes the right skill** from a
naive prompt that never names it. This verifies skill _routing_ — that a skill's
**surfaced** `description` triggers are good enough for Claude to pick it
unprompted.

**Which description is surfaced is not always `SKILL.md`'s.** Where this plugin
ships a `commands/<name>.md` beside `skills/<name>/SKILL.md`, the **command's**
`description` is what reaches the model's skill listing and therefore what
decides routing (observed behaviour of Claude Code as of 2026-09-21, not a
documented contract). The `SKILL.md` one never reaches that listing, so it does
not affect the choice; its body is still loaded in full once the skill fires.

**A skill is in that position whenever `commands/<name>.md` exists beside
`skills/<name>/SKILL.md`.** Check for the twin before tuning a description, and
tune the command's, or the change has no effect on this suite. Which names that
covers today, and the measurement behind it:
[the decision record](../dev_docs/decisions/2026-09-21-command-descriptions-shadow-skill-descriptions.md).

These are **not** part of the blocking PR gate: they cost API tokens and are
nondeterministic. Run them deliberately.

## Run

```sh
just eval                 # = scripts/check.sh --with-evals (gate + evals)
scripts/eval.sh           # evals only, all cases
scripts/eval.sh co-review # a single case
```

Needs an authenticated `claude` CLI — `ANTHROPIC_API_KEY` (how CI authenticates)
or a logged-in CLI locally. In CI: the **Evals** workflow, triggered manually
(`workflow_dispatch`), using the `ANTHROPIC_API_KEY` repo secret.

## How it works

For each manifest row, `scripts/eval.sh` runs:

```
claude -p "<prompt>" --plugin-dir <repo> --max-turns N --output-format stream-json --verbose
```

then asserts the run log contains a `Skill` tool invocation matching the expected
skill (tolerant of the `workflow-skills:` plugin prefix). Pattern adapted from
[obra/superpowers](https://github.com/obra/superpowers) `tests/skill-triggering`.

## Add a case

1. Write `prompts/<skill>.txt` — a realistic prompt that triggers the skill
   **without naming it** (mirror the situations in the skill's surfaced
   `description` — the command's, where one shadows it; see above).
2. Add a row to `manifest.tsv`: `<skill>\t prompts/<skill>.txt \t <max_turns>`
   (tab-separated).
3. `scripts/eval.sh <skill>` to check it.

`analysis-conventions` is intentionally absent: it's `user-invocable: false`
(context-load only), so there's nothing to auto-route.

## Companion check: description discriminability

A typed model call scores a prompt against every skill's **surfaced** `description` —
the same string this suite routes on, the command's where one shadows the SKILL.md, per
the section above — and reports two things this harness cannot: the **margin** between
the winning description and the runner-up, and whether **any** skill should fire at
all — measured over 16 prompts
written to need none, which `manifest.tsv` cannot express, since every row there names
an expected skill. Run it when a `description` changes. It makes drift visible; whether
a narrow margin predicts a real routing failure is untested, and the decision record
says so.

```sh
D=dev_docs/research/2026-09-17-jev-applications/references
python3 $D/jev-description-collision.py --suite all --runs 4    # needs a TypeSafe key
```

38 prompts per pass — the 14 manifest rows, 8 probes written to straddle neighbouring
skills, and the 16 that should load nothing. Measured 2026-09-21: **19.2–19.5s per
pass, ~$0.031 for the four-pass run.** Four passes rather than one because the answers
move between runs, so a single pass is an anecdote.

`--suite manifest` is the fast smoke check for a single description edit — the 14 rows
only, ~7s and ~$0.003 per pass. It cannot produce the no-skill rate: the negative
prompts run only under `--suite negative` or `--suite all`.

**It does not answer this harness's question, and a clean run is not evidence that
routing works.** It scores whether the descriptions discriminate, given that a skill
fires. It cannot see a session that loaded no skill at all — which is how
`select-coder` and `orchestrate-coders` failed here on 2026-09-21 while the typed call
reported 14/14. Neither check replaces the other. Why, and the measurement:
[`dev_docs/decisions/2026-09-21-jev-alongside-the-routing-evals.md`](../dev_docs/decisions/2026-09-21-jev-alongside-the-routing-evals.md).

## Extension point: output-quality evals

This harness only checks invocation, not whether Claude _followed_ the skill
well. For quality scoring, layer an LLM-as-judge tool (e.g. promptfoo's
`llm-rubric`, or Anthropic's JSON `expected_behavior` rubric format) over the
same prompts. Deliberately out of scope here — kept separate so a flaky judge
never blocks the deterministic gate.
