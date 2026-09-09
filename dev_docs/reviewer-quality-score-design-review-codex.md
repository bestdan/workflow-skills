# Architectural review: Reviewer quality score

The measurement concept is useful, but the draft is not implementation-ready.
Its blinding, control-group comparison, and ledger-integrity claims do not hold
under the proposed architecture.

## Findings

### P1: The firewall is not structurally enforced

`/co-review-stats` explicitly lets "the model present" aggregate scores, so an
agent learns the biasing information and can retain it for a later co-review in
the same conversation ([design](reviewer-quality-score-design.md#L201)). The main
agent also retains filesystem read access to the predictable ledger path.

A human-only output surface or mandatory fresh, isolated context is required.
Workflow prose and the absence of an explicit read step are policy controls, not
a capability boundary.

### P1: GitHub reviewers are not currently blinded

The design assumes every source is relabeled neutrally
([design](reviewer-quality-score-design.md#L21)), but the current reconciler
contract passes GitHub comments with their authors while anonymizing only Claude
and local agents ([co-review step 8](../skills/co-review/SKILL.md#L242)). Bot
scores would therefore be produced by a non-blind grader.

Normalize every source, including GitHub bots and human comments, before
reconciliation. Preserve the identity mapping outside the reconciler.

### P1: The Claude baseline is aggregated incorrectly

Reviewers skip, fail, and time out, but the proposed table compares each
reviewer's marginal rate against Claude's rate across all runs
([design](reviewer-quality-score-design.md#L210)). That confounds reviewer
quality with workload selection: a reviewer that runs only on a harder subset of
changes is compared with Claude's performance on the entire population.

Report paired deltas against Claude only on runs where both reviewers ran.
Prefer run-level confidence intervals and stratification by repository and mode
so a few finding-heavy runs do not dominate the comparison.

### P1: Reviewer identity lacks provenance

A config name such as `devin` can represent different models, prompts, CLI
versions, and context, while reconciler changes alter every grade. Aggregating
them under one name destroys the ability to detect the model or prompt
regressions the design promises ([design](reviewer-quality-score-design.md#L159)).

Record at least:

- Ledger schema version.
- Reviewer provider, model, prompt hash, command fingerprint, and CLI version.
- Reconciler model and prompt hash.
- Co-review skill or plugin version.
- Diff identity, such as base SHA and reviewed head SHA.

### P1: The global JSONL write has no consistency model

Concurrent reviews can interleave, a failure can append the run but only some
findings, and retrying can duplicate the batch. Recording only at the end also
cannot satisfy "one run record per invocation" when earlier steps abort
([design](reviewer-quality-score-design.md#L177)). These failure modes directly
corrupt both denominators and percentages.

Define the eligible cohort, add idempotency and locking with an atomic batch, or
use SQLite with a unique `run_id` and a transaction. The design should also say
whether an invocation that fails before reconciliation is intentionally absent
or represented by an incomplete run state.

### P2: Dropping human sources corrupts `unique-high`

If Devin and a human report the same issue, removing the human from `sources`
makes Devin appear to be the sole discoverer
([design](reviewer-quality-score-design.md#L155)). The same metric is also
conditional on which automated reviewers happened to run.

Preserve an anonymous `human_corroborated` flag and define the metric as
"exclusive among eligible reviewers that ran," not "only reviewer caught."

## Recommendation

Resolve the firewall and source-blinding problems before implementation because
they determine whether the collected grades are valid at all. Then revise the
schema around paired comparisons, provenance, and transactional recording before
collecting production data; otherwise the initial ledger will require either a
migration with unreliable historical rows or a clean restart.
