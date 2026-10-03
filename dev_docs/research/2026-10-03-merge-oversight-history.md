---
created: 2026-10-03
---

# Which merges needed the owner? Sixty days across four repos

_Research record, 2026-10-03. Question: of the PRs merged in workflow-skills,
finplan, agent-guidance and dotfiles between 2026-08-04 and 2026-10-03, how
many could an agent have merged safely without the owner, and what marks the
ones that needed him? The field survey is
[`2026-10-03-agent-merge-autonomy-field.md`](2026-10-03-agent-merge-autonomy-field.md);
the proposal built on both is
[`../designs/2026-10-03-graduated-merge-autonomy.md`](../designs/2026-10-03-graduated-merge-autonomy.md)._

## Bottom line

Across **667 human/agent-authored merged PRs**, about **38% could have merged
with no oversight as they were**, a further **44% would have been safe behind
one named automated gate**, and about **17% needed the owner's judgement**.
Read the first two as upper bounds: most PRs have no transcript we can read,
and silence is weak evidence.

Two findings matter more than the split:

1. **The owner's merge caught no defect that shipped.** Every fix-within-7-days
   (about 40 across the four repos) got past him too. They were found by
   co-review finishing late, by Copilot, by real use, or by an agent
   re-checking. **Twelve PRs merged while co-review was still running or
   before its fixes were pushed, and ten of them needed a follow-up fix.**
2. **What he actually supplied was direction, not defect-catching** — scope
   corrections, premise errors, model semantics, conventions, policy about his
   own workflow, and checks on physical machines. Most of that arrived _during
   the session, before the PR existed_, not at merge. At merge, his typical
   turn was a one-line "merged, tidy up" minutes after co-review reported.

So the merge click is mostly a ritual today, while the real gate (finish the
review, then merge) is enforced by nobody. The cheapest safety gain is
mechanical, and the human's judgement belongs earlier in the loop.

## Results

| Repo                     |  Merged | A: safe as is | B: safe with a gate | C: needed owner | Notes                                                             |
| ------------------------ | ------: | ------------: | ------------------: | --------------: | ----------------------------------------------------------------- |
| workflow-skills (public) |     240 |      63 (26%) |           137 (57%) |        40 (17%) | C = 20 verified owner intervention + 20 high-risk by rule         |
| finplan (private)        |     113 |      47 (42%) |            48 (42%) |        18 (16%) | Plus 58 nightly dependency bumps that already merge themselves    |
| agent-guidance (public)  |      45 |      12 (27%) |            18 (40%) |        15 (33%) | A includes 4 design docs that set direction                       |
| dotfiles (private)       |     269 |     134 (50%) |            92 (34%) |        43 (16%) | A includes 27 plan/design docs; ~150 PRs classified by path rules |
| **All four**             | **667** | **256 (38%)** |       **295 (44%)** |   **116 (17%)** |                                                                   |

Release-bot commits (161 in workflow-skills, pushed to `main` directly),
dependabot, probe, closed and open PRs are excluded.

**Where the transcript exists, the picture is harsher.** In workflow-skills the
33 merged PRs with a local transcript are 48% A+B, not 83%, and 36% verified C
— though that subsample is dominated by the Jev research workstream, whose
research and decision PRs drew 9 of 15 corrections while its code and skill PRs
drew 3 of 18. A reasonable central estimate for workflow-skills is **65–75%**
mergeable without him.

## What needed the owner, by repo

The C class is different in each repo, which is the main input to the design.

- **workflow-skills.** Not the code. The PRs that drew him were (a) records of
  a judgement — research verdicts, methodology, adopt/don't-adopt calls; (b)
  policy about his own workflow, e.g. #785, where he overruled a gate as too
  onerous for attended sessions and kept it only for unattended runs; (c)
  live-host checks only he could run (#497, #800); (d) the agent drifting off
  the task or using terms loosely (#787, #802, #847). Skill and command prose
  was redirected on only 4% of PRs. **`docs:` PRs drew the most corrections
  (21%)**, because here `docs:` marks decision content, not low risk.
- **finplan.** **Model semantics**, not size or path: real-vs-nominal price
  levels, default assumptions, how success and the contribution solver are
  defined, percentile methodology. About 15 of 27 C cases are of that kind.
  Statute and tax-table transcription is mostly **B**, because it has an
  external ground truth that a fact-reviewer or golden tests can check —
  and Copilot or co-review already caught statute misreads before merge on
  several PRs. The post-merge numeric defects in the window were of two kinds:
  transcription errors (a fact-reviewer catches them) and a nominal/real frame
  mismatch (typed money catches it; already proposed in finplan).
- **agent-guidance.** Two thirds of C are **rules every agent loads** (#22,
  #23, #32, #33, #64, #76, #77, #83, #87, #88). Three were owner redirections of
  a taxonomy or approach (#31, #47, #60). One closed PR (#4) carried private
  names into the public repo — the clearest judgement that no gate in hand
  would have made.
- **dotfiles.** **Permissions and always-loaded rules**: allow-rule and sandbox
  widening, global guidance changes, irreversible or network-exposing
  automation, and guard loosening — two guard changes opened bypasses found
  later. The owner left **no review comment on any of the 41 PRs touching
  `settings.json`**; those stay C by rule, not because he demonstrably caught
  something.

## How the owner's attention was actually spent

From the interactive transcripts on this machine (about 70 sessions, well over 350
human turns; most PRs were authored in cloud sessions or on the Macs and are not
visible here):

- **A quarter to a third of turns are bare approvals** of a recommendation the agent
  had already made, often one an independent reviewer had already endorsed
  ("one call is still yours… an independent reviewer recommends skipping it" →
  "skip", #914). Exactly the class `/consult` exists to absorb.
- **The merge ritual** — "merged, hop back on main, tidy up" — appears in dozens
  of sessions, typically minutes after co-review reports "nothing needs you".
  Median merge latency was 45 min (agent-guidance) and 68 min (dotfiles); in
  finplan it fell from ~110 h in August to under 1 h in the last week. That
  bounds how deep a silent read can have been.
- **Merging outran review** in all four repos: workflow-skills #388, #492, #550,
  #556; agent-guidance #41; five dotfiles PRs; two finplan PRs, one with the
  owner's own "oops, I merged it".
- **Real interventions** were direction and precision: the agent sidetracked
  onto unrelated tracker work (#787); a design that was too onerous for
  attended use (#785); terminology confused between two 1Password features
  (#802); a UX the owner said he would never type (#802); a verdict that ignored
  a 10× latency advantage (#866); asking whether the method was biased (#869);
  the wrong repo's backlog; a premise built around a PR number when the action
  was generic.
- **One complaint recurs across every repo: "historical narrative"** — prose
  written as a history of the change rather than for the reader (#560, #755,
  #830, #871 here; about 20 comments in dotfiles). It is the single most
  repeated owner correction, and it is mechanical enough to detect.
- **The owner as relay.** He carried messages between agents on different
  machines ("ask the other agent", "the mac mini put comments in the issue").
  That is task-tracking work, not review.
- **Unattended output piled up.** finplan's weekly cloud review routine opened
  PRs that sat unmerged until he consolidated them by hand.

## Gates that would move B to A

Named by the per-repo reviews, with the PRs that motivate them:

| Gate                                                                                           | Repos    | Motivating evidence                                                    |
| ---------------------------------------------------------------------------------------------- | -------- | ---------------------------------------------------------------------- |
| **No merge until co-review reports done at the head SHA and its fixes are pushed**             | all      | 12 PRs merged mid-review, 10 needing a fix                             |
| Dangling-reference check                                                                       | ws, ag   | #353→#354, #741→#751; since added (#823)                               |
| Conventional-type lint: a `skills/`/`commands/` change must use a releasing type               | ws       | #593 merged as `refactor:` and cut no release                          |
| Skill-trigger and behavioural evals, made reliable                                             | ws, ag   | the bulk of B; evals exist but are opt-in and flaky (#834, #838, #844) |
| "Check sibling paths for the same bug" in the co-review rubric                                 | ws       | #805→#898, #437→#438, #442→#443                                        |
| Historical-narrative prose detector                                                            | all      | ~25 owner comments                                                     |
| Fact-reviewer against cited statute/regulation tables; golden worked examples                  | finplan  | transcription defects fixed after merge                                |
| Typed nominal/real money                                                                       | finplan  | the frame-mismatch defect and its follow-ups                           |
| Invariant/property tests (categories sum to total, real values invariant under inflation)      | finplan  | —                                                                      |
| Deploy smoke plus prod freshness, not just `/health`                                           | finplan  | a tooling pin silently broke deploys for days                          |
| Permission-diff classifier: narrowing, repins and `$HOME`-equivalents pass; any widening holds | dotfiles | 13 narrowing PRs were B; 12 widening PRs were C                        |
| Shell run under macOS `/bin/bash` 3.2; guard suites with bypass cases                          | dotfiles | follow-ups from first-run-on-3.2 and two guard bypasses                |

## Method

Three subagents, one per repo group, each:

- pulled every PR created in the window with reviews, comments, review threads,
  commits and files (`gh pr view` / `gh api`), 264 + 207 + 47 + 294 PRs;
- separated the owner's comments from agents' — agents post as `bestdan`, so
  the login means nothing; comments were read and classed by voice and agent
  markers (owner voice found on 10 workflow-skills PRs, 3 finplan, 5
  agent-guidance, 10 dotfiles);
- extracted human turns from the JSONL transcripts under `~/.claude/projects/`
  for each repo and its worktrees, skipping tool results, hook output and
  headless `claude -p` runs;
- found follow-up defects from later PRs that cite an earlier one in a defect
  sense within 7 days, then read each candidate.

Classes: **A** — no visible owner input, no follow-up fix, low blast radius;
**B** — safe given one named gate; **C** — owner changed direction or caught
something (verified), or the change is high-risk by rule (permissions, rules
every agent loads, model semantics, irreversible). Each agent applied the same
brief with its own judgement, so the boundaries differ slightly: workflow-skills
reports C-by-rule separately; agent-guidance and dotfiles split design docs into
A\*, folded into A above.

## Caveats

- **Silence is weak evidence.** "Merged without comment" never made a PR A by
  itself; A also required a low-risk change type and no follow-up. The owner
  may still have read silently or steered in a session we cannot see — August
  in workflow-skills has zero verified C precisely because no record of his
  words exists for it.
- **Transcript coverage is thin.** 207 of 240 workflow-skills merges, most
  finplan merges before Sep 27, and nearly all of the Mac-authored dotfiles and
  agent-guidance work have no local transcript. That undercounts C.
- **Follow-up detection needs a citation**, so uncited regressions are missed
  and A is an upper bound. A numeric bug nobody has found yet is invisible.
- **C-by-rule is policy, not observation.** It encodes what should stay human,
  not what the owner was seen to catch.
- About 150 dotfiles classifications are path-rule defaults, spot-checked by
  title; the ±10-point uncertainty is mostly between A and B.
- Private-repo detail (finplan, dotfiles) is reported here as counts and
  categories only. The per-PR tables were working files and are not kept in
  this public repo.
