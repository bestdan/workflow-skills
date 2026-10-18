---
description: Print the per-reviewer quality table from the /co-review findings ledger, verbatim. The human-run end of the review feedback loop; no agent reads the ledger otherwise.
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-stats.py":*)
argument-hint: "[--since YYYY-MM-DD] [--repo <owner/name>]"
---

# Co-review Stats

Run the script, passing through only `--since` and `--repo` from the
arguments, and show its stdout **verbatim** in a code block:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-stats.py" [--since <date>] [--repo <owner/name>]
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
