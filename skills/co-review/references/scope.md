# Scope and coverage (step 6)

Step 6 answers two questions before any per-line review: **should this change be
split**, and **will every part of it actually get reviewed**. Read this file on
every run; the size measure below is not optional.

## 1. Measure it

Write the diff step 3 fetched to a fixed absolute path (not `$TMPDIR`), then
measure it:

```bash
gh pr diff <n> --repo <owner>/<name> > "$HOME/.claude/co-review-input/size.<run_id>.diff"
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-size.py" --diff "$HOME/.claude/co-review-input/size.<run_id>.diff"
```

Under `--local`, write `git diff <base>` there instead. Untracked files are not
in that diff, so add each untracked file's line count to `reviewable.lines`
yourself. Under `--post`, pass `--no-attrs`, because the checkout's
`.gitattributes` is not the PR's.

The script excludes generated files (lockfiles, stubs, bundles, vendored trees,
`linguist-generated` paths, and files whose first lines say `@generated` or
`DO NOT EDIT`) from every count. The list of what it excluded is in
`generated.paths`. It prints:

- `reviewable.lines` / `reviewable.files`, split into `code_lines`,
  `test_lines` and `prose_lines`;
- `too_big`, true above 800 reviewable lines or 30 reviewable files. Those
  defaults are a starting point, not a measured cut-off;
- `prose_split`, true when at least 300 lines of prose sit beside code or tests;
- `groups`: reviewable files grouped by kind and directory, largest first.
  These are the raw material for a breakdown.

A failed run is never fatal. Note `size: not measured — <reason>` on the Scope
line and judge without it.

## 2. Judge the split

Three tests, each checked on its own:

1. **Unrelated concerns.** Is a refactor, a feature and a config change sharing
   one diff?
2. **Independent units.** Could a part land alone without breaking anything,
   such as docs that no code depends on, or a helper with its own tests?
3. **Can a reviewer hold it?** This test is checkable, so check it: when
   `too_big` is true, the answer is **no** unless the excess is mechanical.
   A rename, a move, or a find-and-replace across many files is mechanical.
   Copied code and new prose are not, because each still has to be read.

**Prose is a different review, not a free ride.** Prose that teaches or
decides, such as decision records, `AGENTS.md`, or a skill body, needs to be
checked against the code and against itself. That is slow, and a code reviewer
skims it. When `prose_split` is true, treat the prose as a candidate split unit
by default. Keep it in only when it documents the code in the same diff and
can't stand without it, such as a README section for the new flag. Say which.

The mechanical-rename exemption covers changes that are **cheap to review**. A
change that is mostly copied or mostly prose is not cheap to review, so
"it's mostly copied, so it's fine" does not apply the exemption. It misreads it.

## 3. Say it plainly

When `too_big` is true and the excess is not mechanical, the Scope verdict is
🟡 **too big**, followed by the measured numbers. Don't soften it to "consider
splitting". A reader should be able to quote the line back to the author as
the reason to split.

Then propose the breakdown. For each piece, give its files or globs, its line
count from `groups`, and a one-line description. List the pieces in landing
order, with the piece nothing else depends on first. Prefer pieces a reviewer
can finish in one sitting, about 400 reviewable lines or fewer.

Then name the stack. GitHub has native stacked pull requests, driven by the
`github/gh-stack` CLI extension (in preview as of 2026-11;
[docs](https://docs.github.com/en/pull-requests/how-tos/stacked-pull-requests)).
Give the author the commands for the pieces you proposed:

```text
gh extension install github/gh-stack     # once
gh stack init <piece-1-branch>           # bottom of the stack, on the base branch
# commit piece 1, then for each further piece:
gh stack add <piece-n-branch>
gh stack submit                          # pushes every branch, opens and links the PRs
```

When the branches already exist as separate PRs, `gh stack link` joins them
into a stack without local tracking. Stacks need every branch in one
repository, so for a fork PR suggest the split without the stack commands.

## 4. Coverage

Whatever the verdict, record what was **actually reviewed**. A file with no
finding looks the same as a file nobody read, so a reader who sees three
reviewers marked ✅ assumes the whole diff was covered.

After step 7, sort the reviewable files into three tiers, using paths or globs:

- **full**: read line by line by at least one reviewer, you included;
- **skim**: looked at, with some claims checked, but not every line;
- **none**: not reviewed.

Base the tiers on what reviewers did, not on what they were sent. Every
reviewer gets the whole diff. A reviewer's output shows what it engaged with:
the files its findings cite, and the files it says it checked. Your own review
counts as `full` only for the files you read line by line.

The Overview's **Coverage** line is required whenever any reviewable file is
`skim` or `none`, and whenever `too_big` is true. Name the `skim` and `none`
paths. An unreviewed area must be stated, never implied. When everything got a
full review, the line says so. Under `--post`, put the same Scope and Coverage
lines in the review body (step 12), so the author and every later reader see
them too.

## Worked example

This happened on a ~3,150-line PR in a private repo (2026-11-11), reviewed by
claude, codex and copilot.

| Part                                   | ~Lines | Reviewed?            |
| -------------------------------------- | ------ | -------------------- |
| New scripts (model, renderer, save)    | 590    | Yes, by all three    |
| Tests + fixture                        | 660    | Skimmed              |
| HTML template + assets, copied from PR | 765    | Spot-checked         |
| 20 decision records (prose)            | 800    | Effectively no       |
| Field list + `AGENTS.md` (prose)       | 340    | A few claims checked |

The run returned ✅ **one unit**. That was defensible on test 1, since all of
it served one feature. It skipped test 3, though, and the run proved test 3
failed: no reviewer covered more than about a fifth of the diff. The decision
records also failed test 2, since they were docs-only and nothing depended on
them. Measured, this diff is `too_big` with `prose_split` true. The output
should have read:

```text
**Scope:** 🟡 **too big** — 3,150 reviewable lines across 41 files (gate: 800 / 30); 1,140 lines of prose beside code
- decision records: dev_docs/decisions/** (800) — docs-only, lands first, reviewed as prose
- model + renderer + save: scripts/*.py, tests/** (1,250) — the feature
- template + assets: templates/**, assets/** (765) — copied from an earlier PR; review the diff against the original
- field list + AGENTS.md (340) — lands with or after the feature it documents
Stack: gh stack init decisions-records → gh stack add feature → gh stack add template → gh stack add field-docs → gh stack submit
**Coverage:** full — scripts/*.py · skim — tests/**, templates/**, AGENTS.md, field list · none — dev_docs/decisions/**
```
