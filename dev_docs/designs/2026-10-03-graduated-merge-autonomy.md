---
created: 2026-10-03
---

# Graduated merge autonomy for the software factory

_Design proposal, 2026-10-03. Not yet built. Evidence:
[the field survey](../research/2026-10-03-agent-merge-autonomy-field.md) and
[sixty days of our own merges](../research/2026-10-03-merge-oversight-history.md)._

## Bottom line

Let agents land the PRs a **deterministic, tested tier script** clears — not
the PRs an agent _judges_ safe — and only once a **co-review status bound to the
exact head commit** is green. Spend the owner's attention earlier, on intent and
premise, where the history shows it actually changed outcomes, and batch the
rest.

How close this can get, estimated from the 667-PR history:

| Stage                                                  | Merges an agent lands | What it takes                                                                   |
| ------------------------------------------------------ | --------------------: | ------------------------------------------------------------------------------- |
| Today                                                  |                    0% | —                                                                               |
| Phase 0 — review-complete gate + tier script in shadow |                    0% | no autonomy change; closes the 12 merged-mid-review gap                         |
| Phase 1 — auto tier on, initial per-repo policy        |           **~20–30%** | 30 clean would-land PRs per repo in shadow                                      |
| Phase 2 — the B→A gates built                          |           **~60–70%** | the gate backlog below, one repo at a time                                      |
| Ceiling                                                |                  ~80% | —                                                                               |
| Stays with the owner for good                          |               ~15–20% | rules every agent loads, permission widening, model semantics, decision records |

These are upper-bound estimates from the history's A/B/C classes, not
measurements; shadow mode exists to replace them with real numbers.

The single highest-value change is **not** autonomy. It is Phase 0's required
status check: the owner's merge caught no defect that shipped, while merging
before co-review finished produced 10 follow-up fixes.

## Goals and non-goals

**Goals.** Fewer owner turns spent on ritual ("merged, tidy up", bare "yes");
no merge before review completes; agent landing for a slice that grows only on
evidence; the owner's time spent on setup, design, naming and topology — where
the history shows his judgement changes outcomes — rather than on reading
diffs.

**Non-goals.** A model deciding the tier. Agents widening their own
permissions, editing rules every agent loads, or changing finplan's model
semantics unattended — ever. A dark factory: nothing here removes the owner
from the loop for the C class.

## Invariants

Taken from the field survey; every component below serves one.

1. **Rules tier; models only demote.** The tier comes from paths, size and
   title. Co-review, the reconciler and `/consult` can push a PR _up_ to
   human; nothing pushes one down.
2. **Author ≠ lander.** Every agent posts as `bestdan`, so separation cannot be
   by identity. It comes from the lander being a deterministic script whose
   inputs the authoring agent cannot argue with.
3. **Verdicts bind to the head SHA.** A push after review clears the verdict.
4. **Fail closed.** Missing status, unparseable output, a reviewer that timed
   out or failed to run, CI not green, a hold label: hold. A reviewer skipped
   by configuration is not a failure.
5. **The policy that grants autonomy is itself human-tier.** So are
   `.github/workflows/`, the ruleset, and the tier script.
6. **Every agent landing is labelled and auditable** on GitHub, which owns that
   state — not in a file here.

## Components

### 1. Review-complete status check (Phase 0)

`/co-review` posts a commit status, context `co-review`, on the head SHA the
round **reviewed** — step 3's `headRefOid`, the same head its PR stamp names
(`skills/co-review/references/stamping.md`). It never posts on its own fix
commit, which no reviewer has seen. `success` only when every planned reviewer
class ran or was skipped by configuration; `failure` when a reviewer timed out
or the run aborted; `pending` while running. Any new push, co-review's own
included, leaves the new SHA without the status, which is the head binding for
free. So `success` reaches the SHA that merges only through a round that
applied no fixes: a round that fixes something costs one more round, which
reviews the fix commit.

Each repo's existing `protect-main` ruleset adds `co-review` as a required
check. finplan, agent-guidance and dotfiles already require Actions checks with
no bypass actor, so this is one more context on a mechanism already in use. In
workflow-skills the ruleset's admin bypass (`bypass_mode: always`) exists for
the release workflow's `RELEASE_TOKEN`; move that bypass to a dedicated actor
(a GitHub App or deploy key) so the check binds the owner there too.
Overriding it stays possible, but becomes a deliberate click.

This blocks the owner's own early merges as well as agents'. That is the point:
those were human UI merges.

It also makes the local co-review the review cycle that gates, and GitHub's
Copilot review an advisory input that nothing waits on. Today that review
arrives on GitHub's schedule, if it is triggered at all. The `copilot` CLI
already runs locally as one of co-review's reviewer classes, so its view
arrives inside the local cycle, on a bounded timeout, like every other
reviewer.

**How we know it holds.** Three checks, cheapest first:

- **Structural.** Once per repo after enabling, try to merge a PR whose
  `co-review` status is `pending` — once in the UI, once with `gh pr merge` —
  and confirm GitHub refuses both. That proves the ruleset, not the prose.
- **Audit.** A weekly query lists merged PRs whose final head SHA carries no
  `co-review` `success`. It must be empty, apart from deliberate bypasses,
  which the query names.
- **Outcome.** The class "follow-up fix to a PR merged mid-review" — 10 in the
  history — should go to zero. A non-zero count means a path around the check
  exists.

### 2. `merge-tier` script

A typed script in this plugin (`scripts/merge-tier.py` with its test pair, per
the repo's logic-in-typed-files rule). Input: the PR's changed paths and line
counts, title, body, labels, and the repo's policy file. Output: `auto` | `human`,
plus the reasons — every rule that fired.

The policy lives in each repo (e.g. `.github/merge-policy.yml`), lists `auto`
path globs and always-human globs, a size cap, and allowed title types.
Defaults are human; a path matching no `auto` glob is human; an always-human
match wins over any `auto` match; the policy file and the script are always
human. The lander runs the installed plugin's `merge-tier.py` and reads the
policy file from the PR's base branch, never from the PR head or the working
tree, so a PR cannot classify itself with its own edits to either.

Built-in demotions, independent of policy:

- title `!` or a `BREAKING CHANGE` footer;
- in workflow-skills, a `skills/` or `commands/` change with a non-releasing
  type (the #593 class) — already human by path, so this is a lint;
- diff over the size cap (start at 300 changed lines; Intercom's reviewer
  refuses large PRs, and shadow data calibrates the number);
- any `merge:hold` label, or the repo variable `AGENT_LAND=off` (kill switch).

### 3. Evidence demotions from review

The lander reads co-review's summary and demotes to human on: any unresolved
finding of any severity; a verification item marked "inconclusive" or left for
the user; a consult-decided call below 85%, a preference call, or a
stack-reaching call awaiting the owner (component 5); "another round"
as co-review's next-round recommendation. A clean co-review never promotes —
it is a necessary condition for `auto`, not a sufficient one. Reviewer
agreement is weak evidence when reviewers share a model family (the field
survey's Refute-or-Promote and Zietsman results), so `auto` also needs the
executed check — the repo's gate green on the head SHA.

### 4. Lander

`scripts/land-pr.sh <pr>`: runs `merge-tier`, checks the `co-review` status is
`success` on `headRefOid`, checks required CI is green, and merges with
`gh pr merge --squash --match-head-commit <sha> --body <audit>`, where the
squash commit body carries the audit record — the tier reasons, the verdict
SHA and the evidence pointers — so it lands atomically with the merge. The
record ends in two trailers, `Landed-by: land-pr.sh` and
`Co-review-head: <sha>`. The `landed-by-agent` label and a PR comment repeating
the record follow as best effort; every audit keys on the `Landed-by` trailer,
and lists any trailer-carrying merge that lacks the label. `/deliver-task` calls it as a new final step
instead of stopping at hand-off when the tier is `auto`; `/auto-pilot`'s
"nothing is merged unattended" invariant becomes "nothing outside the `auto`
tier is merged unattended".

It runs in the session, after co-review, because the repo already puts
decisions of this shape in tested scripts (`pr-fix-guard.sh`,
`tier-coverage.py`). A GitHub Actions lander would need four workflow copies
and, in workflow-skills, a bypass-capable token for the merge to trigger the
release. A scheduled sweeper is a reasonable Phase 2 addition for PRs whose
session ended, not a starting point.

Merge authority reaches the session only through the lander. Today the owner's
global agent settings hold every merge command under `ask`, and a guard hook
refuses any other merge route it can read in the command text, so each direct
merge is an owner approval. Moving the merge commands to `allow` would let the
authoring agent merge directly and skip `merge-tier`, because the required
checks carry no tier signal. So when shadow mode ends, the one new allow rule
names the installed plugin's `land-pr.sh` by its absolute path, globbed over
the version directory, and raw merges stay `ask`. A repo-relative or
working-tree copy is never allowed: the authoring agent can edit those before
running them, and finplan and dotfiles have no such script. The guard hook is a
precondition of that allow rule, not a convenience: the `ask` rules cover only
the named merge commands, so if the hook is removed, so is the rule.

### 5. Judgement calls and `/consult`

Co-review step 9 already runs `agent-guidance:consult` once over every medium
item except user-preference calls (priority, scope, worth doing), and applies
what consult decides — agreed, at least 70%, cheap to undo — as "Decided
without asking"; only the rest reach the owner. A quarter to a third of the
owner's session turns were bare approvals of calls an independent reviewer had
already endorsed, the class that step absorbs.

Proposal — two changes. First, a consult-decided call may leave the PR `auto` only
when the consultant agreed at **≥85%** — its "deciding fact checked" level,
not the 70% that decides a call today. Below that, the decided call still
applies, but the PR demotes to human. The call, the confidence and the crux
go in the PR body so the morning read can overturn it cheaply. Preference calls
always demote. `/consult` is Claude-only, so its agreement correlates with the
author's; the higher bar is the price of that. Where a cross-family reviewer is
available, prefer asking it.

**Second: a call whose answer reaches down a stack.** In a stack, a call on one PR can
fix something every later PR builds on: an interface, a name, a schema, a
convention. Such a call is never settled by `/consult`, whatever its
confidence, because being wrong costs the whole stack, not one PR. Co-review
step 9 therefore excludes it from the consult batch, as it does preference
calls today. The agent
makes a provisional call, records it as such in the PR body and in
`QUESTIONS.md`, and keeps building the PRs above it on that call. Building is
reversible; landing is not. Nothing at or above that PR lands until the owner
confirms. If he overturns the call, the stack is restacked from that point, and
each PR above it is re-verified, as `/auto-pilot`'s restack already does. The
PRs below it are unaffected and can land, following pstack's rule of landing
only the contiguous verified run from the bottom. A call that touches only its
own PR follows the rule above.

### 6. Move the owner's judgement upstream

The history's real interventions were scope drift, a wrong premise, loose
terminology, model semantics, and policy about the owner's own workflow — most
visible **before** the PR existed. So:

- **Intent check at preflight, not mid-run.** Tasks are already written in
  detail, so the tier can be forecast from the task alone: its
  `related_files` against the repo's merge policy, plus whether it touches a
  finplan model-semantics area or produces a research or decision record.
  `/promote-tasks` runs that forecast. A task forecast `human` is promoted only
  once its intent block is confirmed. That block is three lines: what will
  change, the premise it rests on, and the finish condition. The owner confirms
  these in a batch while grooming the backlog, so no run stops mid-flight to
  ask. If the implementation then touches a path the forecast missed, the
  lander's tier still catches it at merge; the forecast only moves the
  conversation earlier.
- **Historical-narrative detector.** The owner's most repeated correction
  across all four repos (~25 comments) is prose written as a change history
  instead of for the reader. agent-guidance's insider-prose check has no
  signal for it: the chronology regex was dropped for firing on correct prose.
  It is filed as bestdan/agent-guidance#91, which proposes running it at
  review time, where a model is already in the loop.

### 7. Batch what stays human

A morning digest — one issue or one message — of open human-tier PRs, each
with a three-line decision brief: what changed by behaviour, the one thing to
judge, and what a "no" would invalidate (deliver-task's hand-off already
writes most of this). The owner lands a verified stack in one pass rather than
answering per-PR pings. `/auto-pilot`'s `REPORT.md` is the existing seed.

### 8. Task tracking

- **WIP tied to review capacity.** `/do-tasks` and `/auto-pilot` stop claiming
  when the human-tier `needs_review` queue exceeds a limit (start at 5; Osmani's
  3–5). In the history, unattended runs opened PRs faster than the owner
  drained them.
- **Every task carries a finish condition.** Without an objective done-check, a
  task's PR is human tier whatever its diff — Devin's and pstack's clearest
  lesson. `/promote-tasks`' confidence check is where to enforce it.
- **Cross-machine relay goes through the issue.** The owner carried messages
  between agents on different machines. The tracker issue is the shared
  channel; agents post status there and read it on resume, instead of the
  owner relaying.
- **The tier lands in the tracker.** The hand-off records the tier, so
  `/sweep-for-complete` and the digest know which PRs will land themselves.

### 9. Post-merge net

- workflow-skills: a bad landing ships a version; the undo is a revert PR and
  the next release. Keep it fast — a `/revert-landing <pr>` that opens the
  revert with the right title type is cheap.
- finplan: deploy freshness, not just `/health`, which can stay green while
  deploys fail (that gate has since been added).
- Weekly: list merges carrying the `Landed-by` trailer that received a
  follow-up fix within 7 days. Any one whose defect a gate could not catch shrinks that slice.

## Initial per-repo policy (Phase 1)

| Repo            | `auto` candidates                                                                             | Always human                                                                                                                                                      |
| --------------- | --------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| workflow-skills | `scripts/**` whose test pair passes on the head SHA; test-only changes                        | `skills/**`, `commands/**`, `agents/**`, hooks, `plugin.json`, `marketplace.json`, `.github/**`, all `dev_docs/**`, `scripts/merge-tier.py`, `scripts/land-pr.sh` |
| finplan         | tests, CI, `chore`/`refactor` with golden outputs byte-identical, docs that are not decisions | anything that can change a computed number, default, assumption, tax table or success definition; decision docs                                                   |
| agent-guidance  | broken links, tests, harness fixes                                                            | every rule file, skill descriptions, plan and design docs                                                                                                         |
| dotfiles        | personal machine config (shell, terminal, UI), tests, version pins                            | `settings.json` permissions and sandbox, hooks and guards, global `CLAUDE.md`/`AGENTS.md`, anything executable at login, anything referencing a secret            |

`dev_docs/` stays human in every repo for now. In workflow-skills `docs:` PRs
drew the most owner corrections (21%), because they carry research verdicts
and decisions; and the runbooks are agent-instruction files that agents act
on. Graduate a `dev_docs` subset only on shadow data.

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

## Rollout

1. **Phase 0** (no autonomy change): co-review status and required check in
   all four rulesets; move workflow-skills' release bypass; `merge-tier` and
   `land-pr.sh --dry-run` posting "would land: yes/no — reasons" on every
   delivered PR; the structural check and weekly audit from component 1; the
   tier forecast in `/promote-tasks`.
2. **Shadow, until the count is met.** Graduation per repo: at least 30
   would-land PRs, none in which the owner made a substantive change, and no
   follow-up fix a gate could not have caught. The count is the gate and the
   duration only an estimate: at the history's rates, about 5 weeks for
   workflow-skills under open question 1's option B, and about 5 months under
   option A.
3. **Phase 1.** Enable landing per repo, workflow-skills and dotfiles first.
4. **Phase 2.** One gate at a time; widen the matching `auto` globs only after
   its own shadow window.

## Risks and what would reopen this

- **Silent-read bias.** The history may undercount the owner's role; shadow
  mode is the correction. If would-land PRs draw substantive owner changes,
  narrow the slice.
- **Gaming by the author.** An agent could split a change to stay under the
  size cap or pick a title to fit a tier. The tier reads paths, not intent,
  so a split still hits the same always-human globs; the title lint covers the
  release lever.
- **A self-attested status.** The `co-review` status proves only that some
  writer posted a status with that name, and every agent holds the same
  `bestdan` token, so the authoring agent could post it without a completed
  co-review. Pinning the check to an App's `integration_id` would not help
  while co-review runs in the session that holds the App's credential too.
  This is accepted residual risk: the weekly audit in component 1 catches a
  missing status, not a forged one, and the PR stamp is posted with the same
  token, so it adds no independence.
- **A merge the guard cannot read.** The guard hook in component 4 reads only
  the Bash command text, so a merge inside a script or interpreter the agent
  wrote is invisible to it. Author ≠ lander holds against direct calls, not
  against a determined agent. Such a merge still needs the required checks,
  and component 1's audit catches one without a `co-review` `success`; one
  that skipped only `merge-tier` leaves no trailer and nothing flags it.
- **Consult correlation.** If resolved technical calls are later overturned,
  raise the bar or drop component 5.
- **Install base.** If many users other than the owner pull workflow-skills
  releases, a bad landing reaches them before the revert; that argues for
  keeping release-cutting titles out of Phase 1 (open question 1).

## Open questions for the owner

1. **Release-cutting titles in workflow-skills' first slice.** Option A: only
   non-releasing types, about 12 PRs in 60 days (~5%), too few for useful
   shadow data. Option B: `feat`/`fix` too when the path tier allows, about 52
   (~22%). Recommended: A, because a bad release reaches every user before
   the revert. A blind consult picked B at 70%, with the crux "how many
   consumers besides the owner pull releases."
2. **The workflow-skills release bypass.** Moving it off the admin role touches
   `release.yml`'s token. Acceptable?
3. **Starting thresholds**: size cap 300 lines, human-tier WIP limit 5, consult
   bar 85%.

## Decisions

Each checked with a blind `/consult`, with its confidence and crux:

- Enforce review-complete as a required status, not in script or prose (85%):
  "whether the merges that outran review were human UI merges" — they were.
- Lander as plugin scripts run by the session (75%): "if the D1 status could
  not be read from the session … B or C wins".
- No `dev_docs` in the first slice (75%).
- A consult-decided technical call keeps the PR `auto` only at ≥85%; below
  that it still applies, but the PR demotes to human (60% — weak agreement,
  from a consult on an earlier framing in which consult resolved calls only at
  85%): "whether 'calls for you' items are genuinely reversible and confined to
  the technical class".
