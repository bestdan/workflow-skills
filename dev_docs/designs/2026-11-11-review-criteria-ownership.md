---
created: 2026-11-11
status: proposed
tracks: https://github.com/bestdan/workflow-skills/milestone/20
---

# Give agent-guidance ownership of review criteria

Single-agent review and co-review should apply the same standards. Today,
agent-guidance defines those standards, while workflow-skills repeats and
overrides them in its reviewer rubric and reconciler instructions. Make
agent-guidance the canonical home for review criteria and finding-validation
principles. Keep reviewer dispatch, independent reconciliation, and disposition
in workflow-skills. This is an ownership migration, not a new review engine.

## What is true today

- Agent-guidance's [reviewing.md](https://github.com/bestdan/agent-guidance/blob/main/reviewing.md)
  defines scope, evidence, uncertainty, severity, and finding presentation.
  Its [reviewing skill](https://github.com/bestdan/agent-guidance/blob/main/skills/reviewing/SKILL.md)
  also loads writing conventions and, when tests change, testing guidance.
- [review_prompt.md](../../skills/co-review/review_prompt.md) repeats scope and
  finding conventions and explicitly overrides the attached agent-guidance
  text. It also carries legitimate worker constraints: supplied-context-only
  review, read-only operation, findings-only output, and a completion sentinel.
- [Co-review steps 6–8](../../skills/co-review/SKILL.md) separately define scope
  assessment, the main review, and reconciliation. The main review does not
  load the external reviewer rubric. Reviewers therefore share broad criteria
  without sharing one authoritative contract.
- [The reconciler](../../agents/co-review-reconciler.md) and step 8 deliberately
  duplicate judging rules because the fallback subagent and some distributions
  do not load the bundled agent. Rules about checking premises, reachability,
  and proposed fixes are useful in ordinary reviews too. Their
  [worked examples](../../skills/co-review/references/judging-fixes.md) are
  currently owned by workflow-skills.
- [coreview-conventions.sh](../../scripts/coreview-conventions.sh) resolves two
  optional convention files. Missing files are tolerated. External reviewers
  cannot follow links to testing guidance or other rules outside their input.
- [The zip builder](../../scripts/build-claude-ai-zips.sh) packages co-review
  through its default skill-directory copy, without the plugin's reconciler
  agent or the separately installed agent-guidance conventions.

## Proposal

### Ownership

| Concern                                                                                | Canonical owner |
| -------------------------------------------------------------------------------------- | --------------- |
| Review scope, including prose contradictions, relevant tests, and supplied PR metadata | agent-guidance  |
| Evidence, consequences, reachability, uncertainty, and judging a proposed fix          | agent-guidance  |
| Finding labels, anchors, and severity independent of confidence                        | agent-guidance  |
| Review-specific examples and rationale for those principles                            | agent-guidance  |
| Reviewer selection, models, probes, timeouts, locks, and permission rules              | workflow-skills |
| Input collection, context limits, output shape, and completion markers                 | workflow-skills |
| Blind source labels, deduplication, independent judging, and reconciler JSON           | workflow-skills |
| Confidence-to-action mapping, fixes, consultation, posting, ledgers, and status        | workflow-skills |

Agent-guidance's reviewing entry point must explicitly load the shared criteria
before producing findings. Put the generally applicable finding-validation
rules in `reviewing.md`; move substantial examples into a review reference
there. A linked example is optional reading; a required rule must be in the
loaded criteria, not reachable only through a link.

Workflow-skills' reviewer rubric becomes an assignment wrapper. It identifies
the supplied material, available capabilities, required output, and prohibition
on edits. It tells the worker to apply the attached criteria; it neither
repeats the checklist nor asserts blanket precedence over it. Findings-only
workers may omit praise while the human-facing summary includes it. That is
an output projection, not a different standard of correctness.

The main reviewer, every external reviewer, and the reconciler receive the
same resolved core criteria for a run. Include the testing guidance whenever
tests change; the caller makes that semantic assessment, including test hunks
inside files that do not have a conventional test filename. Pass relevant PR
title/body metadata when available, and say explicitly when it was not
supplied. Repository-specific conventions remain applicable, with supplied
context determining which claims a reviewer can verify.

### Loading is an interface

Extend the existing conventions resolver rather than introduce a framework.
It must select a complete criteria set from one agent-guidance source, expose
which source it selected, and preserve deterministic attachment order. It must
not combine an installed file with an older packaged file to fill gaps.
Keep the current transport distinction: stdin, a named input file, or
`--prompt-file`. Changing content must not accidentally change the pinned CLI
invocations or their permission rules.

Normal plugin installs resolve agent-guidance using the existing discovery
mechanism. A standalone distribution includes a generated copy of the required
agent-guidance files, accompanied by the source revision and original paths.
Generate it at package build time from one explicit revision; never maintain a
second handwritten rubric. A package can use that bundled source without
runtime network access. Packaging must verify the source's redistribution
terms and include the required license/attribution files.

The proposed availability policy is that a complete shared criteria set is
required. Missing, incomplete, or incompatible criteria stop the review before
reviewer dispatch or review side effects and report how to install/update the
source. Non-interactive mode reports a bounded failure rather than prompting.
An invalid explicit override remains an error, not a reason to silently use a
different source. This intentionally replaces today's reduced-coverage
behavior; document it as a user-visible prerequisite change.

Do not gate this on a guessed minimum version or invent a new version protocol.
The implementation should check the concrete files/contract established by
the upstream change and test an older install. Publish that upstream contract
before switching consumers. At minimum, provenance and required content must
distinguish a complete new contract from an old file with the same name.

### Review and reconciliation

A single-agent review uses agent-guidance's reviewing skill and returns its
findings. It does not acquire co-review's ledger, GitHub status, fix loop, or
independent-verification claim. No new standalone workflow-skills review skill
or single-review flag is needed for this migration.

Co-review runs its current reviewer pool, then independently assesses the
findings under the same standards. The reconciler still reads repository
context when available, checks both observations and fixes, merges duplicates,
and emits the current verdict schema. Workflow-skills retains the operational
rule that a reconciler-authored replacement fix cannot be auto-applied as
high confidence; that is action policy, not a definition of a good finding.

Pass the criteria text explicitly in the reconciler brief, both for the named
agent and the general-purpose fallback. The named agent and fallback may each
state their role, tool restrictions, and response schema, but neither owns a
second copy of the judging principles. No reviewer or reconciler may retrieve
historical reviewer scores to inform its judgment.

Equal criteria do not imply equal access or identical findings. External
workers remain limited to the supplied input. The main reviewer and reconciler
retain their existing inspection capabilities and execution/trust limits.
The shared principles require marking unsupported claims `UNVERIFIED`, never
inventing evidence or granting new tool permissions to obtain it. A single
review is not equivalent evidence to independent reconciliation, and cannot
satisfy co-review's multi-reviewer status requirement merely by sharing rules.

## Migration and validation

[Migration milestone](https://github.com/bestdan/workflow-skills/milestone/20):

- [Make agent-guidance the canonical review and finding-validation contract](https://github.com/bestdan/workflow-skills/issues/1479)
- [Deliver complete agent-guidance criteria to installed and packaged co-review](https://github.com/bestdan/workflow-skills/issues/1480)
- [Use shared review criteria for co-review's main and external reviewers](https://github.com/bestdan/workflow-skills/issues/1481)
- [Use shared finding-validation principles in both reconciler dispatch paths](https://github.com/bestdan/workflow-skills/issues/1482)
- [Verify review-path parity and graduate the review ownership design](https://github.com/bestdan/workflow-skills/issues/1483)

Track all work in workflow-skills, including the prerequisite whose code lands
in agent-guidance. The upstream issue must name that implementation repository
and link its PR before it can be considered complete. Use native GitHub
dependencies so downstream tasks cannot be selected ahead of it.

1. Establish and release the canonical review and finding-validation contract
   in agent-guidance, with the existing single-review entry point loading it.
2. Add complete criteria resolution and generated distribution content, with
   transport tests, before removing any consumer's existing rules.
3. Switch main and external reviewers to the shared criteria and enforce the
   new prerequisite at co-review entry. Remove duplicated reviewer criteria.
4. Switch the named and fallback reconciler to explicitly supplied judging
   criteria; remove the duplicated principles and migrate their examples.
5. Verify end-to-end loading and behavior, update user-facing documentation,
   graduate the design, and delete temporary planning material.

Each task is one PR in its implementation repository. If packaging exceeds the
task size budget, split it before implementation without removing the hard
dependency between available criteria and consumer cutover. GitHub owns
execution status; this document records the proposed sequence only.

Automated checks should exercise actual assembled input through stub reviewer
binaries for all supported transports, rather than merely assert that prose
mentions a filename. Cover a current install, an older incomplete install,
an invalid override, no install, and a built package used without the source
checkout. Verify mandatory criteria reach the main reviewer and both
reconciler dispatch paths, including test-specific guidance when needed.
Retain output sentinels, JSON schema, read-only restrictions, blind labeling,
and permission-rule checks. Run each repository's gate for its changes.

Use a small, fixed behavioral comparison in the final task: a prose
contradiction, an unreachable triggering case, a correct observation with a
harmful fix, an unverified external premise, and a weak test assertion.
Compare criterion coverage and evidence discipline across single review and
co-review, not identical wording or finding counts. Keep model-based checks
opt-in; deterministic gates verify input delivery and contracts.

## Decisions

- **One semantic owner.** Agent-guidance defines quality for both finding and
  validating issues. Workflow-skills orchestrates stages and acts on outcomes.
  Keeping separate rubrics would preserve the drift this migration addresses.
- **Explicit delivery over links.** Restricted workers cannot load linked
  guidance. Required criteria travel in their input; references hold optional
  examples. Generated package copies have provenance and are not new sources.
- **Require complete criteria.** A review without its standards must not look
  like a normal completed review. Packaging supplies an offline path rather
  than maintaining a reduced handwritten fallback.
- **Preserve operational independence.** Shared principles improve a lone
  review, but do not make self-checking independent verification. Reconciler
  dispatch and multi-reviewer status semantics remain workflow concerns.

## Not decided here

- Reviewer models, pool size, confidence thresholds, or merge policy changes.
- A generic review framework or a new public single-agent command.
- Identical context access for all agents, or identical findings across runs.
- The concrete compatibility marker and package provenance file layout; the
  upstream contract task selects these before consumers implement against it.

## Graduation

The final task writes the ownership/loading convention in
`dev_docs/review-criteria.md`, links it from the appropriate contributor map,
and records the ownership and availability decisions under `dev_docs/decisions/`.
Agent-guidance retains the runtime principles and their examples. Delete this
design after those homes exist, regenerate the affected indexes, and remove
any remaining local plan scaffolding. Keep progress in the linked issues.
