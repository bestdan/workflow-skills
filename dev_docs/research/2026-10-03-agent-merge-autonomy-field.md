---
created: 2026-10-03
---

# When can an agent merge its own PR? — the field in October 2026

_Research record, 2026-10-03. Question: what do teams that let AI agents
approve or merge pull requests actually gate on, and what carries the safety
when they do? Input to
[`../designs/2026-10-03-graduated-merge-autonomy.md`](../designs/2026-10-03-graduated-merge-autonomy.md);
the per-repo evidence from our own history is
[`2026-10-03-merge-oversight-history.md`](2026-10-03-merge-oversight-history.md)._

## Bottom line

Nobody serious lets the **authoring** agent merge its own work. Machine
approval exists, but only in three shapes: (a) a **separate** reviewer process
approving a **deterministically pre-cleared slice** of small, low-risk changes
(Intercom ~19%, Meta RADAR, Copilot's path-scoped approvals since 2026-09-01);
(b) a verdict **bound to the exact head commit** that merges; (c) a strong
**post-merge net** — someone watches it go live and rollback is cheap. Model
judgement operates _inside_ a slice that rules already cleared; it never widens
the slice. Config, permission and agent-instruction files are human-only in
every policy that names them. The frontier vendors (Anthropic, OpenAI, GitHub's
coding agent, Cognition) all still keep the merge with a human.

For a solo developer the transferable moves are: a fail-closed path/size tier
script, agent review that can **demote but never promote**, head-SHA-bound
verdicts, a lander distinct from the author, a shadow-mode ramp, and batching
the human's remaining decisions instead of removing them.

## Method and confidence

A subagent read 35 sources through WebFetch, which returns a model-written
summary of each page, not raw text. I re-fetched the two load-bearing ones
(Intercom, pstack ch. 6) and confirmed the quotes below verbatim; other quotes
are near-verbatim from the summarizer and should be re-checked before being
cited anywhere load-bearing. "Inference" marks reasoning that is ours, not the
source's. Fetch failures are listed at the end.

## Sources

| #  | Source                                                                                                                                                        | Date       | Takeaway                                                                                                                                          |
| -- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | [Intercom — AI is approving our pull requests](https://www.intercom.com/blog/ai-is-approving-our-pull-requests-heres-how-we-made-it-safe/) (Mykhailov, Young) | 2026-04-21 | Decomposed AI reviewer auto-approves "over 19%"; refuses large PRs; shipper owns monitoring and rollback                                          |
| 2  | [pstack guide ch. 6 "Verify and ship"](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/06-verify-and-ship.md) (Lauren Tan / poteto, Cursor)     | 2026       | "Green is not the same as safe"; verifier is never the author; the PR babysitter never merges                                                     |
| 3  | [pstack ch. 7 "Overnight"](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/07-overnight.md)                                                     | 2026       | Unattended contract: goal, finish condition, permissions, escape hatch, TSV decision log; merge only on a clean verdict for the patch that merges |
| 4  | [pstack `autopilot-stack` playbook](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/autopilot-stack.md)                       | 2026       | Default: agents build a verified stack, the operator lands it; `autopilot-full` only with independent PRs and granted landing authority           |
| 5  | [pstack skills: never-block-on-the-human, blast-radius](https://github.com/cursor/plugins/tree/main/pstack/skills)                                            | 2026       | Proceed on reversible work, confirm irreversible; prove the one fact safety depends on by running code                                            |
| 6  | [Tan — How I Use Cursor](https://x.com/poteto/article/2058975157503570132)                                                                                    | 2026-05-25 | "The key for building your own software factory is trust"; verification is the bottleneck; go deep before wide                                    |
| 7  | [StrongDM Software Factory](https://factory.strongdm.ai/)                                                                                                     | 2026-02    | "Code must not be reviewed by humans" — replaced by holdout scenarios and a digital twin of third-party services                                  |
| 8  | [Willison on StrongDM](https://simonwillison.net/2026/Feb/7/software-factory/)                                                                                | 2026-02-07 | Proof is the open question when code and tests are both agent-written; skeptical of the cost                                                      |
| 9  | [Shapiro — The Five Levels](https://danshapiro.spicytakes.org/post/2026-01-23-the-five-levels-from-spicy-autocomplete-to-the-software-factory)                | 2026-01-23 | Vocabulary: L3 human reviews agent code … L5 "dark factory"                                                                                       |
| 10 | [Meta — RADAR, automating low-risk code review](https://arxiv.org/abs/2605.30208)                                                                             | 2026-05-28 | Eligibility → static rules → risk-score percentile → LLM (≥8/10, all-safe categories) → land; 331K+ diffs landed                                  |
| 11 | [Meta — Diff Risk Score](https://engineering.fb.com/2025/08/06/developer-tools/diff-risk-score-drs-ai-risk-aware-software-development-meta/)                  | 2025-08-06 | Model-scored incident risk lets the lowest-risk diffs land during freezes                                                                         |
| 12 | [GitHub — Copilot code review can now approve PRs](https://github.blog/changelog/2026-09-01-copilot-code-review-can-now-approve-pull-requests/)               | 2026-09-01 | Off by default; admins pick the paths it may approve; new commits dismiss the approval                                                            |
| 13 | [GitHub — Copilot cloud agent risks and mitigations](https://docs.github.com/en/copilot/concepts/agents/cloud-agent/risks-and-mitigations)                    | current    | The coding agent cannot approve or merge; the requester cannot approve its PR                                                                     |
| 14 | [pwd9000 — should Copilot approvals count?](https://dev.to/pwd9000/copilot-can-now-approve-pull-requests-should-it-count-toward-your-branch-protection-2b78)  | 2026-09-13 | Assess-only first, then count approvals on path-scoped low-risk repos only                                                                        |
| 15 | [Anthropic — Code Review for Claude Code](https://claude.com/blog/code-review)                                                                                | 2026-03-09 | Findings on 84% of PRs >1,000 lines; "It won't approve PRs — that's still a human call"                                                           |
| 16 | [Anthropic — How we built auto mode](https://www.anthropic.com/engineering/claude-code-auto-mode)                                                             | 2026-03-25 | Classifier blocks push-to-main and infra changes; 17% FNR on real overeager actions                                                               |
| 17 | [OpenAI — Verifying code at scale](https://alignment.openai.com/scaling-code-verification/)                                                                   | 2025-12-01 | Codex reviewer tuned for precision; advisory, "over reliance is a serious risk"                                                                   |
| 18 | [Cognition — Devin's 2025 performance review](https://cognition.com/blog/devin-annual-performance-review-2025)                                                | 2025-11-14 | Best on clear, verifiable 4–8h tasks; humans still review because quality isn't straightforwardly verifiable                                      |
| 20 | [Morris — Humans and agents in SE loops](https://www.martinfowler.com/articles/exploring-gen-ai/humans-and-agents.html)                                       | 2026-03-04 | "On the loop": fix the harness, not the artefact; auto-apply recommendations above a score as confidence grows                                    |
| 21 | [Beck — Party of One for Code Review](https://newsletter.kentbeck.com/p/party-of-one-for-code-review)                                                         | 2026       | Solo, review becomes sanity check (asked vs got) plus structure upkeep                                                                            |
| 22 | [Willison — Deliver code you have proven to work](https://simonwillison.net/2025/Dec/18/code-proven-to-work/)                                                 | 2025-12-18 | Proof = you saw it work + a test that fails when the change is reverted                                                                           |
| 24 | [Zietsman — The specification as quality gate](https://arxiv.org/abs/2603.25773)                                                                              | 2026-03-26 | Same-family generator and reviewer share errors; specs → deterministic checks → AI review for the residual                                        |
| 25 | [Agarwal — Refute-or-Promote](https://arxiv.org/abs/2604.19049)                                                                                               | 2026-04-21 | Ten reviewers unanimously endorsed a non-existent bug; one empirical test killed it                                                               |
| 26 | [CodeRabbit — request-changes workflow](https://docs.coderabbit.ai/pr-reviews/request-changes-workflow)                                                       | current    | Approves only when latest commit reviewed, threads resolved, checks pass, HEAD unchanged                                                          |
| 27 | [Merge Steward (agentic-ops-hub #78)](https://github.com/frankxai/agentic-ops-hub/pull/78)                                                                    | 2026       | Deterministic path tiers, "never a model"; agent-instruction files and workflows always human; fail closed; kill switch                           |
| 28 | [Osmani — The Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/)                                                                        | 2026       | "Don't run more agents than you can meaningfully review. 3-5 is the sweet spot."                                                                  |
| 29 | [Greptile — Rise of the overnight agents](https://www.greptile.com/blog/rise-of-the-overnight-agents)                                                         | 2026-05-05 | Claude 1.80 vs human 2.72 reverts per 1,000 merged PRs (all human-approved); Claude over-indexes on authorization flaws                           |
| 30 | [Not All Agents Are Equal](https://arxiv.org/abs/2609.17598)                                                                                                  | 2026-09    | 37,623 agent PRs; revert comparisons disputed as collider bias                                                                                    |
| 31 | [paddo — The Rollback Is the Product](https://paddo.dev/blog/the-rollback-is-the-product)                                                                     | 2026-09-05 | Incidents increasingly come from config, flags and prompts; separate deploy from release                                                          |
| 34 | [Anthropic — Property-based testing with Claude](https://www.anthropic.com/research/property-based-testing)                                                   | 2026-01-14 | Agent-written Hypothesis properties found real bugs; weak on subtle semantics                                                                     |
| 35 | [Google — Practical mutation testing at scale](https://arxiv.org/abs/2102.11378)                                                                              | 2021       | Mutate changed lines only and surface survivors in code review                                                                                    |

The subagent's full per-source notes (including sources 19, 23, 32, 33, cut
here as redundant) were not kept; the table is the durable part.

## What the leading examples actually do

**Intercom** (verified quotes). "Over 19% are auto-approved with no human
reviewer in the loop." The reviewer is split into sub-reviewers for problem
statement quality, diff-vs-intent, safety, logic, and practices. "It won't
approve large PRs. If a change is too big, too complex, or too broad in scope,
it flags it." The net is human: "The engineer who ships a change is expected to
watch it go live, monitor its behaviour in production, and be ready to roll
back." Result: "497 PRs went fully autonomous… zero reverts of AI-approved
PRs" in four weeks, with their own caveat: "You can only go so far with PR
review as a safety mechanism, no matter how good the reviewer is, human or AI."
Inference: the approved slice is selected for being small and clean, so its
revert rate is partly selection.

**pstack** (Lauren Tan, Cursor; ch. 6 quotes verified). "Green is not the same
as safe." "The agent that judges a change is never the one that wrote it."
"Babysit stops at merge-ready. It never merges, even with everything green,
because merging is a different decision." Verification is matched to the change
type (a CLI change runs the real command, a migration replays a saved input),
and "inconclusive" is a legitimate verdict. The default overnight mode builds a
verified linear stack and the operator lands it in one pass; only
`autopilot-full` merges, and only on a clean verdict for the exact patch, from a
verifier that is not the author. Overnight runs keep a decision log that a
separate reviewer turns into a morning "Attention" list. No outcome data — it
is a guide, not a measurement.

**Meta RADAR.** A funnel, in order: eligibility exclusions (WIP, previously
rejected, sensitive code, denylisted runbooks) → static heuristics → Diff Risk
Score percentile cut (P50 for allowlisted bot runbooks, P20 other bots, P5
humans) → LLM review requiring ≥8/10 confidence _and_ every change in a safe
category (refactor, dead-code removal, formatting) → land. Deterministic
codemods are vetted once and blanket-accepted. 331K+ diffs landed; reverts at
1/3 and incidents at 1/50 of the non-RADAR rate — no randomization, so
selection inflates both. Note the shape: the more repetitive and trusted the
source, the bigger the slice it may land.

**GitHub Copilot.** The coding agent cannot mark ready, approve, or merge, and
the requester cannot approve its PR. Separately, since 2026-09-01 the Copilot
_reviewer_ may approve — off by default, with admin-chosen path globs, and the
approval is dismissed by any new commit. The invariant is author ≠ approver.

**StrongDM** is the outlier: "code must not be reviewed by humans." The gate is
"satisfaction" over end-to-end scenarios held out of the codebase, run against
behavioural clones of Okta, Slack, Jira and Google services. Willison finds the
holdouts and twins transferable but questions cost and proof.

**Anthropic, OpenAI, Cognition** ship review agents and coding agents and all
keep the merge human: "It won't approve PRs — that's still a human call"
(Anthropic); "over reliance is a serious risk" (OpenAI); "It should always be up
to the human what to do" (Cognition's CEO).

## Cross-cutting patterns

1. **Author never approves itself.** Every source that allows machine approval
   puts it in a separate identity or process (GitHub, pstack, Meta).
2. **Rules tier first, models judge inside the tier.** Meta's eligibility gates,
   Merge Steward's "never a model", Copilot's path globs, Intercom's size
   refusal. Model judgement never widens the slice.
3. **Small single-purpose diffs are the unit of auto-approval.** Intercom
   refuses large ones; RADAR's safe categories are refactor/dead-code/format.
4. **The verdict binds to the head commit.** CodeRabbit, Copilot and pstack all
   invalidate approval on a new push.
5. **Executed evidence beats reviewer agreement.** Willison's "test fails on
   revert"; Refute-or-Promote's unanimous-but-wrong panel; Zietsman's
   correlated same-family errors. Inference: diversity of _evidence type_
   (execution vs reading) matters more than diversity of models.
6. **The safety net is after the merge.** Intercom's shipper watches and rolls
   back; paddo: config and prompts are where incidents now come from.
7. **Config, instructions and permissions are the dangerous class.** Merge
   Steward always human-tiers agent-instruction files, workflows and scripts;
   auto mode blocks infra and permission changes. Greptile finds Claude
   over-indexes on authorization flaws.
8. **Throughput is capped by verification capacity.** Osmani's 3–5 agents;
   Morris's throughput mismatch; Tan's "go deep before wide".
9. **Trust is earned per slice with a measured ramp.** Meta relaxed P25→P50 on
   data; pwd9000's assess-then-count; Morris's score-gated auto-apply.

## Disagreements and open questions

- **Is human code review needed at all?** StrongDM no; the frontier vendors
  yes; Intercom and Meta "not for a selected low-risk slice with a strong net".
- **Is AI review a gate or a filter?** Intercom and Meta use it to approve;
  OpenAI and Anthropic call it advisory; Zietsman scopes it to the residual
  after deterministic checks.
- **Do revert-rate comparisons prove agents are safe?** All are confounded by
  what got approved or merged, and one is publicly disputed as collider bias.
  Read them as "not obviously worse", not as licence to drop a gate.
- **Model-scored or rule-scored tiers?** Meta can afford a calibrated model; a
  solo developer with little incident history cannot train one, so auditable
  rules fit better (inference).
- **"Never block on the human" vs "the human merges."** pstack reconciles them
  by batching: reversible work proceeds and the human gets one landing decision
  per stack, not one per PR.

## What this implies for our setup

Inference throughout; the design proposal carries the concrete version.

- `/co-review`'s reconciler and `/consult` are exactly the "AI review" the
  field treats as a filter: right to **demote** a PR to human, wrong to
  **promote** one. `/co-review` already reaches cross-family reviewers (codex,
  agy, devin, copilot, crush, grok) when they are configured; `/consult` is
  Claude-only, the same-family case where errors correlate.
- `deliver-task` already never merges, matching pstack's babysitter. What is
  missing is the separate **lander** with a deterministic tier and a
  head-bound verdict.
- Our repos map onto the field's dangerous class unevenly: workflow-skills
  skill and command bodies and all of agent-guidance are "agent instruction
  files"; dotfiles' `settings.json` and hooks are permissions; finplan's risk is
  numeric correctness, where the field's answer is property and mutation tests
  rather than better reviewers.
- workflow-skills auto-releases on merge, so its "rollback" is a revert plus a
  re-release that users' sessions pick up later — a weaker net than a feature
  flag, which argues for starting with titles that do not cut a release.

## Fetch failures and limits

- A Springer paper on LLM reviewer overcorrection hit an auth wall; unused.
- Tan's X articles were read through a GitHub markdown mirror.
- No primary Factory.ai or Graphite/Diamond merge-policy document was found.
- Beck's post carried no date; his remark about agents deleting tests came
  from search snippets only.
