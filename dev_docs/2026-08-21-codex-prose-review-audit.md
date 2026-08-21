# Why codex passed prose-only diffs — audit and experiment (2026-08-21)

A snapshot. The measurements below are tied to specific commits and to the
reviewer pool as it stood on 2026-08-21; nothing here is maintained.

The durable conclusion lives in
[`skills/co-review/reviewers/codex.md`](../skills/co-review/reviewers/codex.md).
The fix it produced is the contradiction bullet and the generalized UNVERIFIED
clause in [`skills/co-review/review_prompt.md`](../skills/co-review/review_prompt.md).

## The question

`codex` returned `REVIEW_COMPLETE: PASS` on two consecutive prose-only PRs
([#402](https://github.com/bestdan/workflow-skills/pull/402),
[#405](https://github.com/bestdan/workflow-skills/pull/405)) where other
reviewers found real bugs. The hypothesis on the table: a diff-only reviewer is
structurally blind to this repo's dominant defect class — a new claim that
contradicts unchanged text elsewhere in the tree — because `codex exec
--sandbox read-only` gets the rubric and the diff and nothing else.

## Premise error in the hypothesis

The hypothesis contrasted codex against reviewers that can read past the diff,
and named `agy` as one. That is not what `--add-dir` does: it points at
`<INPUT-DIR>`, the dedicated directory holding the input file, to defeat a
headless `read_file` permission gate so agy can read _its own input_.
[`reviewers/agy.md`](../skills/co-review/reviewers/agy.md) forbids the repo root
by name, and agy's pointer says "Do NOT explore any other file."

**So the pool had two diff-only reviewers, not one.** Repo context came only
from Copilot, the main agent, and the reconciler. Caveat: the documented gate
was verified, not agy's runtime behavior — `agy.md` warns it explores despite
instructions.

## Diff-visibility measurement

13 findings (Copilot's, pulled verbatim from the API), classified against the
**review-time** diffs rather than merged state. This matters: #402's second
commit `49285ebf3` is the fix for its own sharpest finding, so testing against
`gh pr diff 402` after the merge would have been circular. Review-time commits
were recovered from each comment's anchor: `6863ec384` (#402) and `58950eee5`
(#405).

| PR   | Finding                               | Evidence needed                  | Verdict       |
| ---- | ------------------------------------- | -------------------------------- | ------------- |
| #402 | `do-tasks.md:311` `assigneeId`        | `linear-claim.md:73`             | **invisible** |
| #402 | `jira-claim.md:93` blocked_statuses   | config's "claim flow reads" list | **invisible** |
| #402 | `SKILL.md:82` per-operator cap        | `repo-pr-execute.md:174-190`     | **invisible** |
| #405 | `attendedness.md:37`                  | `repo-pr-execute.md:171-173`     | visible       |
| #405 | `attendedness.md:47` remote dispatch  | `repo-pr-execute.md:171-173`     | visible       |
| #405 | `wip_gate_attendedness.md:46`         | self-contradictory added para    | visible       |
| #405 | `SKILL.md:91`                         | `repo-pr-execute.md:171-173`     | visible       |
| #405 | `repo-pr-execute.md:173` cron overrun | added para vs added rule         | visible       |
| #405 | `gh-issue-claim.md:68` step 3         | step 3, ~30 lines below hunk     | **invisible** |
| #405 | `jira-claim.md:72` step 3             | same                             | **invisible** |
| #405 | `do-tasks.md:312` steps 3/4/5         | gate steps below hunk            | **invisible** |
| #405 | `do-tasks.md:32` `--non-interactive`  | preflight at lines 18-20         | **invisible** |
| #405 | `SKILL.md:130` flag matrix            | `do-tasks.md:42-55`              | **invisible** |

**8 of 13 (62%) diff-invisible. 5 of 13 (38%) fully visible and missed by
codex.** All five visible ones are on #405, and all turn on an added paragraph
in `repo-pr-execute.md` contradicting added lines in `attendedness.md` — both
halves `+` lines in the same diff. `agy`, with identical access, caught one.

So access was not the binding constraint, and the 62% gap was shared with agy
rather than specific to codex.

## The datum that redirected the diagnosis

Three dispatch logs from `~/.claude/co-review-input/`, same transport, same
`gpt-5.5` pin, same rubric:

- `codex.log` — prose diff of `reviewers/codex.md` → `PASS`, 13,931 tokens.
- `codex512.log` — diff of `agents/settings.json` → **`FINDINGS`**, and correct:
  a stale unpinned `codex exec` allowlist entry left beside the new pinned one,
  leaving the default model reachable.
- `agy.log` — agy catching a diff-visible prose contradiction on that same
  prose PR that codex passed.

The sharpest detail: the prose diff codex passed on was _itself_ editing
`codex.md`'s fenced allow-rule JSON (the unpinned entry being deleted). The same
JSON in a `.json` file is what codex caught. That falsifies any
file-extension-based dispatch predicate — it would skip codex on exactly the
diff whose fenced config it ignored.

An initial reading attributed this to content type. That was wrong; see below.

## The confound, and the experiment

`review_prompt.md` was code-review shaped ("Correctness and obvious bugs",
"Skip nitpicks") and **held constant across all four dispatches**, so
withholding-as-out-of-scope could not be separated from a content-type effect.

Design (per a second-opinion review): two arms, to separate mechanism.

- **Arm A** — generalized anti-withholding clause + a contradiction-is-correctness
  bullet. No cue to _search_ for contradictions.
- **Arm B** — Arm A plus an explicit search directive.

Arm A alone succeeding means suppression. Only Arm B working would mean the
model needed the reasoning pointed at the target. Scoring is recall by substance
against the labeled findings, **not** the `REVIEW_COMPLETE:` line: `codex exec`
is unseeded, so one verdict is a coin observation, and `FINDINGS` full of
irrelevancies is not a pass.

The two diffs carry different targets, so they test different clauses:

| Diff               | Targets              | Tests                   | Runs |
| ------------------ | -------------------- | ----------------------- | ---- |
| `58950eee5` (#405) | 5 diff-**visible**   | contradiction detection | 3    |
| `6863ec384` (#402) | 3 diff-**invisible** | the generalized clause  | 2    |

## Results

Arm A only. Command byte-identical to the approved invocation (the rubric
travels in `<INPUT>`, not the command string). codex-cli 0.148.0.

**`58950eee5` — baseline `PASS`:**

| Run | Target hits                                                            | Also found               |
| --- | ---------------------------------------------------------------------- | ------------------------ |
| 1   | `attendedness.md:32` — rule vs repo-pr single-mode exemption           | `adapters.md:71` (novel) |
| 2   | `attendedness.md:38` + `SKILL.md:125` — the "any remote dispatch" pair | —                        |
| 3   | none                                                                   | `adapters.md:76` (novel) |

**3 of 5 targets recovered, hits in 2 of 3 runs, FINDINGS in 3 of 3.** Run 2
recovered the case that had most strained the original hypothesis — both halves
`+` lines, previously passed.

**`6863ec384` — baseline `PASS`:** `PASS`, `PASS`. **0 of 3.**

### Novel true positive

Both runs reporting it were right, and no other reviewer had caught it:
`adapters.md` calls `$AUTO_PILOT_UNTIL` "the first hard negative on that file's
pre-check list", while `attendedness.md` lists `--non-interactive` first and
`$AUTO_PILOT_UNTIL` second. Verified against the tree at `58950eee5`.

**This is still unfixed on `main`** — the audit only documented it.

### agy regression check

One dispatch, same input, Arm A rubric. Still returns findings, both true, and
it found the same `adapters.md` contradiction independently. Caveat: agy's
original findings text was never recovered, so this shows no regression to
silence or garbage — it is not a recall comparison.

## Conclusions

1. **Arm B was unnecessary.** Arm A carried no search cue, so its success means
   **suppression, not blindness**. codex could see those contradictions and was
   being told they were out of scope.
2. **The content-type reading was wrong.** Content type was a correlate: a
   code-shaped rubric passed code-shaped content through its scope filter and
   screened prose out. That is why the `.json` diff got a finding and three
   prose diffs did not.
3. **The fix is rubric text**, not a dispatch predicate and not dropping codex.
4. **The 62% gap is untouched by it.** `6863ec384` stayed at `PASS` twice even
   with the generalized clause, so it does not make a diff-only reviewer report
   what it cannot see. Repo-context findings stay with Copilot, the main agent,
   and the reconciler.
5. **Keep the `gpt-5.5` pin.** The same pin produced the correct `codex512`
   finding, so the passes were never a pin problem.

## Limits

`n` is small: 5 codex dispatches + 1 agy, two labeled diffs, one repo, one
author, one model pin. Nothing generalizes to codex on code-bearing diffs or as
a coder. `devin` and `copilot`-as-CLI were never dispatched — no conclusions
about them. The rubric change is reviewer-neutral text, but only agy was
validated against it.
