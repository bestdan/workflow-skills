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

## A case that writes into the repo fails that row

Each case runs against a writable checkout with `--dangerously-skip-permissions`,
so a realistic prompt can write into the repo under test — and one did, staging a
generated report while the suite called the row a pass. The harness now reads
`git status --porcelain` around every case and fails any row that changed the
tree, naming the paths and listing the row again in the summary.

Two things follow. A row reported this way is a **harness** failure, not a
routing verdict: the case may well have picked the right skill, but it has
changed what every later case sees, so treat the rest of the run as unreliable.
And the residue is left where it is rather than reverted — the suite is often run
from a worktree with work in flight, and a harness that reverts on its own would
eat that work. Inspect and remove it yourself.

The comparison is per case against a rolling baseline, not against a clean tree,
so uncommitted work you already had does not convict a row. It compares the
porcelain list **and** a hash of `git diff HEAD`, because the list carries status
codes and paths but no content: an edit to a file already showing as modified
leaves its line byte-identical. It is also symmetric, so a case that _reverts_
one of your edits is reported too rather than reading as clean.

The path list is read with `-uall`. By default porcelain collapses an untracked
directory to one `?? dir/` entry, so a file written inside it would leave the
output byte-identical — which is the shape of the case actually observed, and
would have gone unseen had the target directory been untracked.

A `<repo>` that is not a git checkout degrades to a no-op. One narrow hole
remains: rewriting a file that was _already_ in the untracked list moves neither
signal, since its entry is unchanged and `diff HEAD` skips untracked content.
Covered by `test/eval-tree-guard.bats`.

## Add a case

1. Write `prompts/<skill>.txt` — a realistic prompt that triggers the skill
   **without naming it** (mirror the situations in the skill's surfaced
   `description` — the command's, where one shadows it; see above).
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
