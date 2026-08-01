---
title: "research-spike: suggest — the advisory scan that cannot fail a run"
priority: medium
size: 2
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
is_blocked_by: research_spike_task_1
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, advisory]
---

[[research_spike_plan]]

## Context

The lexical scan is the tempting half — grep for "deferred to", "gated on",
"belongs to" and flag anything unregistered. It survives only as an advisory
mode that **cannot fail a run**, and the reason is measured, not fastidious:
run against the reference tree, `--suggest` returned **29 hits**, most of them
prose describing behaviour rather than deferring work (e.g. "the stop is never
gated on the lock"). As a gate step in a repo with no baseline file, no
allowlist, and no skip flag, it would have been noise with no permitted place
to record exceptions.

The rule to carry across is not "keep it out of CI" but: **a check whose false
positives have nowhere to go must not be able to fail the build.** In a repo
that _does_ have an advisory tier, `suggest` may run in CI as a non-failing
report.

## Task

Implement `suggest` in `scripts/research-spike.py`:

- Scan `*.md` under the tree for a phrase list (`deferred to`, `gated on`,
  `belongs to`, `handled by`, `left to`, `once … lands`, …) — the list is a
  **starting point**, not a contract; keep it in one clearly-labelled constant
  so a repo can edit it without hunting.
- Report `path:line: <matched phrase>` with the surrounding line, so a human
  can judge in one glance.
- **Stay quiet next to a registered record**: a hit inside — or immediately
  adjacent to — a question section or contracts file that already carries an
  `obligation` block (or a `none:` declaration) is suppressed. The suppression
  rule matters more than the phrase list; without it the advisory output is
  dominated by exactly the deferrals that were done correctly.
- **The scan always exits `0`.** This is a structural property, not a policy:
  no code path in the **scan** returns non-zero, including on a parse error in
  a scanned file (report it and continue). Argparse **usage** errors still exit
  `2` before dispatch — that is task 1's dispatcher contract, and a malformed
  flag is not the scan reporting a finding. Scoping the absolute to the scan is
  what keeps "no findings" distinguishable from "you invoked it wrongly"; it
  does **not** reopen the settled decision that lexical findings never fail a
  run (design §"What is deliberately _not_ in the gate", measured at 29 hits).
- Document in `--help` that this mode is advisory and why, in one line.

## Acceptance Criteria

**Code-enforced:**

- New fixtures in `scripts/test-research-spike.sh`:
  - `suggest` reports unregistered deferral prose with path and line;
  - `suggest` stays **quiet** next to a registered obligation;
  - `suggest` stays quiet next to an explicit `none:` declaration;
  - the **scan** never returns non-zero — assert exit `0` on a clean tree, on
    a tree full of hits, and on a tree containing a file that fails the
    block parser;
  - `suggest --bogus-flag` still exits `2` — the dispatcher's usage contract is
    not suppressed by the scan's exit-0 guarantee;
  - `suggest` writes nothing (tree unchanged).
- `bash scripts/check.sh` green.

**User-run:**

- Run `suggest` over a real doc tree and eyeball the signal-to-noise ratio; if
  suppression is working, the surviving hits should mostly be things you'd
  actually want to register.
