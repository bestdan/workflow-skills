---
description: Use when the user asks how useful, accurate or fast each co-review reviewer has been — e.g. "is agy worth running", "which reviewers are wrong most", "show reviewer stats", or /co-review-stats. Prints the per-reviewer quality table from the co-review findings ledger, verbatim. Never invoke it from inside a review, or to inform one; it is the human end of the feedback loop.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-ledger-sync.py" --pull), Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-stats.py":*)
argument-hint: "[--since YYYY-MM-DD] [--repo <owner/name>] [--by-model]"
---

# Co-review Stats

First refresh the other machines' rows. Run this **unsandboxed**: it needs the
network and writes `~/.claude/co-review/shared/`, which the Bash sandbox denies.

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-ledger-sync.py" --pull
```

It prints only `ledger sync: …` receipt lines and always exits 0. Show them
above the table; a `❌` line, or `ledger sync: off`, still goes on to the stats.
The plugin's `SessionEnd` hook also pushes this machine's new runs when a
session ends, so the pull here mostly fetches the other machines'. The design is
[`dev_docs/designs/2026-10-18-co-review-ledger-sync.md`](../dev_docs/designs/2026-10-18-co-review-ledger-sync.md).

Then run the stats, passing through only `--since`, `--repo` and `--by-model`
from the arguments, and show its stdout **verbatim** in a code block:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-stats.py" [--since <date>] [--repo <owner/name>] [--by-model]
```

Add no numbers of your own: no summary that restates, computes, or rounds any
figure. If the output is a "No ... ledger data" or "No reconciled runs match"
line, show it and stop.

## How to read it

- `Δ claude` is the column that supports a keep/drop decision: it is paired on
  runs both reviewers shared, so it is not confounded by which diffs each saw.
  `wrong %` alone is the tempting read.
- Read `wrong %` with `unique-high`. Precision alone rewards a reviewer that
  reports nothing; a noisy sole catcher of real bugs may still be worth keeping.
- Read speed against quality. `p90 s` is the wait for the whole run. Slow, high
  `fail %`, low `unique-high` is the clearest drop; slow but a sole catcher may
  be worth it.
- After a reviewer's model pin changes, `--by-model` gives one
  `name@model` row per model; `--since` cannot separate them when old and new
  ran on the same day. `Δ claude` still pairs against claude across all its
  models.
- If every reviewer, `claude` included, looks bad, suspect the reconciler
  prompt, not the reviewers.
- Verdict to action: `wrong` means drop or retune the model; `not-applicable`
  means fix the repo context passed in; `out-of-scope` means fix the rubric.

Acting on the table is a human decision: edit `.co-review.yml` or a reviewer
prompt yourself. This command never edits config.

## Firewall

No agent in the review path may read the ledger, or it starts grading
reviewers instead of findings. So after viewing stats, **do not run
`/co-review` in this session**. If one is run anyway, its run summary must say
reviewer scores were in context, so its rows can be discounted. This is a
policy control, not a capability boundary.

Optional hardening: add `"Read(~/.claude/co-review/**)"` to `permissions.deny`.
It blocks the Read tool only; a Bash `cat` still reads the file (which is why
this script keeps working). The Bash-side boundary is
`sandbox.filesystem.denyRead`, for sandboxed commands only.
