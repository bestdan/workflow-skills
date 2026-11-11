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

- `--local`: swap `gh pr diff …` for `git diff <base>` (the same merge-base
  caveat applies), and pass each untracked file step 3 found as
  `--untracked <path>`.
- `--post`: unchanged. The score describes the diff, whoever wrote it.

It prints three lines. The first two are the report:

```
### Complexity: 72 / 100 (High)
Reviewable: +240 / -110 across 18 files · 5 packages · 34 hunks · code+test
COMPLEXITY: score=72 band=High threshold=60 split=required r=350 f=18 p=5 h=34 e=2 excluded=3
```

The `COMPLEXITY:` line is for you: `split=required` means the score met the
threshold. The formula, the default exclusions and the path rules behind each
category are in the script's header, which is their one home.

## Where the report goes

- **Summary:** the two report lines open the output template, above
  **Overview**, in every mode.
- **`--post`:** the posted review's `body` opens with the same two lines.
- **Under `--non-interactive`:** the same, and the `COMPLEXITY:` line goes on
  section 1's reviewer line so the caller can read the score without parsing
  markdown.

A pipe reports only the script's exit code, so a failed `gh pr diff` reaches
the script as an empty diff and scores `0 / 100 (Low)` with `f=0`. Treat
`f=0` on a diff you know is non-empty as a failed run. If the script exits
non-zero, prints nothing, or fails that way, open the summary with
`### Complexity: unavailable — <the stderr line>` and judge scope on the
qualitative signals alone. Never estimate a score yourself; a guessed number
is exactly what the script exists to remove. Not fatal.

## How the score bears on the split call

- **Below the threshold:** step 6 is unchanged — split only on the qualitative
  signals, with high confidence.
- **At or above it (`split=required`):** the **Scope** line must be
  🟡 **split recommended**, with concrete pieces (files or hunks, one line
  each) beneath it, cut along the seams the signals point to: top-level
  packages, code versus tests versus config, independently mergeable layers.
  The one exception is a change that is a single mechanical unit — a rename, a
  codemod, a vendored update — which scores high on size alone. Then keep
  ✅ **one unit** and say on the Scope line that the score was overridden and
  why.

## Configuration

Both knobs sit in the `complexity:` block of `.co-review.yml`:

```yaml
complexity:
  split_threshold: 60 # the default
  exclude: # Python regexes against the repo-relative path; added to the defaults
    - '\.pb\.go$'
```

Use single quotes around a regex, so YAML leaves its backslashes alone. Flags
outrank the file: `--threshold <n>`, `--exclude <regex>` (repeatable), and
`--no-default-excludes`.
