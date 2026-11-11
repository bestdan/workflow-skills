---
created: 2026-10-03
status: proposed
tracks: https://github.com/bestdan/workflow-skills/issues/1443
---

# Graduated merge autonomy for the software factory

_Design proposal, 2026-10-03. Evidence:
[the field survey](../research/2026-10-03-agent-merge-autonomy-field.md) and
[sixty days of our own merges](../research/2026-10-03-merge-oversight-history.md)._

## Bottom line

The review-complete gate, the tier script and the lander are built, and every
repo runs the lander in shadow. What remains is the plan below: graduate each
repo out of shadow on its own count, switch landing on there, then build the
Phase 2 gates that widen the `auto` slice one class of PR at a time. How the
built part works today, and how to tune or switch it off, is
[`../merge-autonomy.md`](../merge-autonomy.md). The choices it rests on are
decision records:

- [Review-complete is a required `co-review` status](../decisions/2026-10-03-review-complete-is-a-required-status.md)
- [A tested script sets the merge tier; models can only demote](../decisions/2026-10-03-rules-tier-models-only-demote.md)
- [The lander is a plugin script the session runs](../decisions/2026-10-03-lander-runs-in-the-session.md)
- [`dev_docs/` stays human-tier for the first slice](../decisions/2026-10-03-no-dev-docs-in-the-first-auto-slice.md)
- [Release-cutting titles may land in workflow-skills' first slice](../decisions/2026-10-31-release-cutting-titles-in-the-first-slice.md)
- [Starting thresholds](../decisions/2026-10-03-merge-autonomy-starting-thresholds.md)

How far each stage could go, estimated from the 667-PR history's A/B/C
classes. These are upper bounds, not measurements; shadow mode exists to
replace them with real numbers.

| Stage                                | Merges an agent lands | What it takes                                                                   |
| ------------------------------------ | --------------------: | ------------------------------------------------------------------------------- |
| Phase 1 — landing on, initial policy |           **~20–30%** | 30 clean would-land PRs per repo in shadow                                      |
| Phase 2 — the B→A gates built        |           **~60–70%** | the gate backlog below, one repo at a time                                      |
| Ceiling                              |                  ~80% | —                                                                               |
| Stays with the owner for good        |               ~15–20% | rules every agent loads, permission widening, model semantics, decision records |

## Rollout

1. **Shadow, until the count is met.** Graduation per repo: at least 30
   would-land PRs, none in which the owner made a substantive change, and no
   follow-up fix a gate could not have caught. `scripts/merge-audit.py --mode
   shadow` reports the count. The count is the gate and the duration only an
   estimate: about 5 weeks for workflow-skills at the history's rates. Today
   the shadow comment is posted only at a `/deliver-task` hand-off, so hand-run
   PRs never count; #1443 runs it at the end of every co-review round.
2. **Phase 1, per repo, workflow-skills and dotfiles first.** Set the repo
   variable `AGENT_LAND=on`, and add the one allow rule naming the installed
   plugin's `land-pr.sh` to the owner's agent settings, keeping raw merges
   `ask` and the guard hook that refuses other merge routes in place. Run `scripts/merge-audit.py --mode landed` weekly from then on.
3. **Phase 2.** One gate at a time; widen the matching `auto` globs only after
   that gate's own shadow window.

## Phase 2 gate backlog (B → A)

Each gate converts a class of B PRs; build them in this order, by B volume:

1. Skill-trigger and behavioural evals, made reliable and blocking for
   `skills/`/`commands/` description changes (workflow-skills, agent-guidance).
   This is the largest B class and the hardest gate.
2. Permission-diff classifier for `settings.json`: narrowing, `$HOME`
   equivalents and repins pass; any widening holds (dotfiles).
   `coreview-rule-drift.py` is the starting point.
3. finplan: typed nominal/real money (already proposed), fact-reviewer on
   statute and table PRs, golden worked examples, invariant properties
   (categories sum to total; real values invariant under inflation).
4. Shell under macOS `/bin/bash` 3.2 and guard suites with bypass cases
   (dotfiles).
5. "Check sibling paths for the same bug" in co-review's rubric.

Two smaller pieces belong to the same phase:

- **A scheduled sweeper** that runs the lander on `auto` PRs whose session
  ended before hand-off. The lander runs only at hand-off today.
- **Cross-machine relay through the tracker issue.** Agents on different
  machines post status on the issue and read it on resume, instead of the
  owner carrying messages between them.
