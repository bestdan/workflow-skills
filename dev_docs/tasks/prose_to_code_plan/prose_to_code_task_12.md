---
title: Script the fact-reviewer's reproducibility re-run and restore
priority: medium
size: 2
impact: 2
status: new
created: 2026-09-09
source_branch: bestdan/prose-to-code-index
related_files:
  - agents/fact-reviewer.md:47-55
  - skills/analysis-pipeline/SKILL.md
  - skills/analysis-pipeline/example/README.md
parent: prose_to_code
expires: 2026-10-09
tags:
  - extraction
  - analysis
---

Plan: [[prose_to_code_plan]] · Index row 12.

## Context

Check 3 of the fact-reviewer: confirm `git status --porcelain` is clean (else
n/a — "the re-run would clobber uncommitted edits"), re-run the pipeline, diff
`model_output.json` and `memo.filled.md` against the committed copies,
re-confirm a JSON difference with `jq -S`, treat `memo.final.md` as
nondeterministic, and always `git checkout --` the regenerated files. A fixed
sequence with a mandatory restore is the shape most likely to be skipped under
a subagent's time pressure.

## Task

1. Add `scripts/analysis-pipeline/check-reproducibility.sh <dir>`: the clean
   check, the re-run (the pipeline's own command from its README), normalized
   JSON diff via Python (`json.dumps(sort_keys=True)`, no `jq` dependency),
   diff of the filled memo, restore in a trap so it runs on failure too. Print
   `REPRO: verdict=pass|fail|n/a` plus the diff on fail.
2. Test by copying `skills/analysis-pipeline/example/` into a temp `git init`
   fixture (`GIT_CONFIG_GLOBAL=/dev/null`): a clean commit → `pass`; a
   **committed** perturbed `model_output.json` → `fail` with the diff, and a
   clean tree afterwards; an uncommitted edit → `n/a`. A perturbation left in
   the working tree can never reach `fail` because the clean check returns
   `n/a` first — commit it.
3. Rewrite `agents/fact-reviewer.md` check 3 to run the script and report its
   verdict.

## Acceptance Criteria

- Code-enforced: `just check` passes; the restore-on-failure case leaves the
  fixture tree clean.
- Code-enforced: check 3 in `agents/fact-reviewer.md` is one command and its
  output contract.
