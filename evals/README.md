# Behavioral evals

Opt-in, non-blocking checks that Claude **auto-invokes the right skill** from a
naive prompt that never names it. This verifies skill _routing_ — that a skill's
`description` triggers are good enough for Claude to pick it unprompted.

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
   **without naming it** (mirror the situations in the skill's `description`).
2. Add a row to `manifest.tsv`: `<skill>\t prompts/<skill>.txt \t <max_turns>`
   (tab-separated).
3. `scripts/eval.sh <skill>` to check it.

`analysis-conventions` is intentionally absent: it's `user-invocable: false`
(context-load only), so there's nothing to auto-route.

## Extension point: output-quality evals

This harness only checks invocation, not whether Claude _followed_ the skill
well. For quality scoring, layer an LLM-as-judge tool (e.g. promptfoo's
`llm-rubric`, or Anthropic's JSON `expected_behavior` rubric format) over the
same prompts. Deliberately out of scope here — kept separate so a flaky judge
never blocks the deterministic gate.
