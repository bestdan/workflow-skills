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
gh pr diff <n> --repo <owner>/<name> | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/pr-complexity.py" --config "<config>"
```

**Type the path as SKILL.md renders `${CLAUDE_PLUGIN_ROOT}`**, the absolute
plugin path step 6 shows. The variable is substituted in SKILL.md only, not in
this file and not in the Bash environment, so the line above copied literally
runs `/scripts/pr-complexity.py`.

- `--local`: swap `gh pr diff …` for `git diff <base>` (the same merge-base
  caveat applies), and pass each untracked file step 3 found as
  `--untracked <path>`.
- `--post`: unchanged. The score describes the diff, whoever wrote it.

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

| `size=`      | Scope line                                                                                                                                                                                                         |
| ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `small`      | 🟡 **split recommended** only when a signal holds with high confidence; otherwise ✅ **one unit**.                                                                                                                 |
| `medium`     | The same as `small`.                                                                                                                                                                                               |
| `large`      | 🟡 **split recommended** when any signal holds, even a modest one. When none does, ✅ **one unit**, saying on the Scope line why the change holds together despite its size.                                       |
| `very-large` | 🟡 **split recommended** unless the change is one mechanical unit (a rename, a codemod, a vendored update), and the **split prompt** below follows the pieces. A mechanical unit keeps ✅ **one unit**, saying so. |

Every split recommendation lists concrete pieces beneath the Scope line (files
or hunks, one line each), cut along the seams the signals point to: top-level
packages, code versus tests versus config, independently mergeable layers.

**The split prompt** is a fenced block the author can hand to an agent as-is.
It names the repo, the branch, and the PR number when there is one; it lists
the pieces as stacked PRs in landing order, each with its files and a one-line
purpose; and it tells the agent to build them as stacked branches off the
base. Under `--post` it goes in the review `body` after the report lines, so
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
