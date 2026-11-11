# Complexity report — step 6's score

Step 6 scores the diff before it judges scope, so every run of every reviewer
sees the same number for the same change. The score is an **input** to the
split judgment, never a replacement for it.

## Run it

Feed it the diff the mode already reviews, in one Bash call. `<config>` is the
absolute path of `$CO_REVIEW_DIR/.co-review.yml`, resolved against the main
working tree as **Local reviewers → Config file** shows; a missing file is not
an error.

```bash
gh pr diff <n> --repo <owner>/<name> | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pr-complexity.py" --config "<config>" --root "<checkout>"
```

`<checkout>` is the absolute path of the tree step 3 established, so the
script can read its `.gitattributes` for `linguist-generated` paths.

**Type the path as SKILL.md renders `${CLAUDE_PLUGIN_ROOT}`**, the absolute
plugin path step 6 shows. The variable is substituted in SKILL.md only, not in
this file and not in the Bash environment, so the line above copied literally
runs `/scripts/pr-complexity.py`.

- `--local`: first run `git merge-base <base> HEAD` as a call of its own,
  then swap `gh pr diff …` for
  `git diff --src-prefix=a/ --dst-prefix=b/ <merge-base>`. Diffing against
  `<base>` itself scores work `<base>` gained after this branch left it as
  if it were this change, which inflates the score. The prefixes override a
  user's `diff.noprefix` or `diff.mnemonicPrefix`, which the parser cannot
  read past. Pass each untracked file step 3 found as `--untracked <path>`.
- `--post`: drop `--root`. The checkout's `.gitattributes` is not the PR's, so
  only the path and header rules apply. The score describes the diff, whoever
  wrote it.

It prints three lines. The first two are the report:

```
### Complexity: 72 / 100 (large)
Reviewable: +830 / -320 across 18 files · 5 packages · 34 hunks · code+test
COMPLEXITY: score=72 size=large by=score cuts=30/60/80 lines_at=3000 r=1150 f=18 p=5 h=34 e=2 excluded=3
```

The `COMPLEXITY:` line is for you: `size=` is the band, `cuts=` the lowest
score of `medium`, `large` and `very-large`, and `by=` what set the band.
`by=lines` means the diff reached `lines_at` reviewable lines, which makes it
`very-large` whatever its score; the first line then reads
`(very-large: <n> reviewable lines)`. The trigger exists because the size
terms cap, so a large diff in a single top-level directory cannot score its way
to `very-large`. The formula, the default exclusions and the path rules behind
each category are in the script's header, which is their one home.

## Where the report goes

- **Summary:** the two report lines open the output template, above
  **Overview**, in every mode.
- **`--post`:** the posted review's `body` opens with the same two lines.
- **Under `--non-interactive`:** the same, and the `COMPLEXITY:` line goes on
  section 1's reviewer line so the caller can read the score without parsing
  markdown.

A pipe reports only the script's exit code, so a failed `gh pr diff` reaches
the script as an empty diff and scores `0 / 100 (small)` with `f=0`. Treat
`f=0` with `excluded=0` on a diff you know is non-empty as a failed run; a
diff of only excluded files, such as a lockfile bump, legitimately prints
`f=0` with `excluded` above zero. If the script exits
non-zero, prints nothing, or fails that way, open the summary with
`### Complexity: unavailable — <the stderr line>` and judge scope on the
qualitative signals alone. Never estimate a score yourself; a guessed number
is exactly what the script exists to remove. Not fatal.

## How the size bears on the split call

The size is weighed against step 6's split signals: unrelated concerns in one
diff, and units that could land independently. The bigger the band, the less
evidence those signals need before a split is called. Size alone decides only
at `very-large`.

| `size=`      | Scope line                                                                                                                                                                                                                                                   |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `small`      | 🟡 **split recommended** only when a signal holds with high confidence; otherwise ✅ **one unit**.                                                                                                                                                           |
| `medium`     | The same as `small`.                                                                                                                                                                                                                                         |
| `large`      | 🟡 **split recommended** when any signal holds, even a modest one. When none does, ✅ **one unit**, saying on the Scope line why the change holds together despite its size.                                                                                 |
| `very-large` | 🟡 **too big**, with the score and the measured lines and files, unless the change is one mechanical unit (a rename, a codemod, a vendored update). The pieces and the **split prompt** below follow it. A mechanical unit keeps ✅ **one unit**, saying so. |

**Too big is a verdict, not a hint.** Don't soften it to "consider
splitting". A reader should be able to quote the Scope line back to the author
as the reason to split.

**Mechanical means cheap to review.** A rename, a move or a find-and-replace
across many files qualifies. Copied code and new prose do not, because each
still has to be read line by line. "It's mostly copied" or "it's mostly docs"
is not a reason to call a change one unit.

**Prose beside code is its own unit by default.** When the report's categories
include `docs` and the prose is more than a short section, treat it as a
candidate split piece at every band. Prose that teaches or decides (decision
records, `AGENTS.md`, a skill body) needs checking against the code and against
itself, which a code reviewer skims. Keep it in only when it documents the code
in the same diff and cannot stand alone, such as a README section for the new
flag, and say which on the Scope line.

Every split recommendation lists concrete pieces beneath the Scope line, in
landing order, with the piece nothing else depends on first. Give each its files
or globs, its reviewable lines, and a one-line purpose. Cut them along the
seams the signals point to: top-level packages, code versus tests versus
config versus docs, independently mergeable layers. Take each piece's line
count from `git diff --numstat` summed over its files, never from an estimate.
Prefer pieces a reviewer can finish in one sitting, about 400 reviewable lines
or fewer.

**The split prompt** is a fenced block the author can hand to an agent as-is.
It names the repo, the branch, and the PR number when there is one; it lists
the pieces as stacked PRs in landing order, each with its files and a one-line
purpose; and it tells the agent to build them as a GitHub stacked PR with the
`gh stack` extension (`github/gh-stack`, in preview as of 2026-11;
[docs](https://docs.github.com/en/pull-requests/how-tos/stacked-pull-requests)):

```text
gh extension install github/gh-stack     # once
gh stack init <piece-1-branch>           # bottom of the stack, on the base
# commit piece 1, then for each further piece:
gh stack add <piece-n-branch>
gh stack submit                          # pushes every branch, opens and links the PRs
```

When the pieces already exist as open PRs, `gh stack link` joins them into a
stack. A stack needs every branch in one repository, so for a PR from a fork
the prompt asks for stacked branches without the `gh stack` commands. Under `--post` it goes in the review `body` after the report lines, so
it reaches the author rather than only you.

## Configuration

The knobs sit in the `complexity:` block of `.co-review.yml`:

```yaml
complexity:
  medium_at: 30 # the defaults: each is the lowest score in its band
  large_at: 60
  very_large_at: 80
  very_large_lines: 3000 # reviewable lines that make a diff very-large; 0 turns it off
  exclude: # Python regexes against the repo-relative path; added to the defaults
    - '\.pb\.go$'
```

The three cuts must rise strictly above 0, and `very_large_lines` must be 0 or
more, or the script exits 2. Use single quotes around a regex, so YAML leaves
its backslashes alone. Flags outrank the file: `--medium-at`, `--large-at`,
`--very-large-at` and `--very-large-lines` (each `<n>`), `--exclude <regex>`
(repeatable), and `--no-default-excludes`.

## Worked example

A 3,155-line PR, reviewed by claude, codex and copilot:

| Part                                   | ~Lines | Reviewed?            |
| -------------------------------------- | ------ | -------------------- |
| New scripts (model, renderer, save)    | 590    | Yes, by all three    |
| Tests + fixture                        | 660    | Skimmed              |
| HTML template + assets, copied from PR | 765    | Spot-checked         |
| 20 decision records (prose)            | 800    | Effectively no       |
| Field list + `AGENTS.md` (prose)       | 340    | A few claims checked |

It was called ✅ **one unit**. That was defensible on unrelated concerns,
since all of it served one feature. But the line trigger makes it `very-large`,
the copied template and the prose are not mechanical, and the decision records
could land alone because nothing depends on them. No reviewer covered more than
about a fifth of it, which the Coverage line ([`coverage.md`](coverage.md))
exists to say. The output should have read:

```text
### Complexity: <score> / 100 (very-large: 3155 reviewable lines)
**Scope:** 🟡 **too big** — 3,155 reviewable lines, over the 3,000-line trigger; 1,140 lines of prose beside code
- decision records: dev_docs/decisions/** (800) — docs-only, lands first, reviewed as prose
- model + renderer + save: scripts/*.py, tests/** (1,250) — the feature
- template + assets: templates/**, assets/** (765) — copied from an earlier PR; review it against the original
- field list + AGENTS.md (340) — lands with or after the feature it documents
**Coverage:** full — scripts/*.py · skim — tests/**, templates/**, AGENTS.md, field list · none — dev_docs/decisions/**
```
