---
title: "research-spike: derived readiness and the status convergence report"
priority: high
size: 5
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
is_blocked_by: research_spike_task_5
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, reporting]
---

[[research_spike_plan]]

## Context

`status` answers the question the maintainer was really asking on day four:
**what still blocks building?** It is derived entirely by the script — the
agent never computes a status or a count.

Target output (design §"Convergence: the `status` report"):

```
foo — decisions: 1 decided, 1 ready, 3 blocked

  stop-semantics        READY    awaiting decision
  account-provisioning  BLOCKED  by 2 questions, 1 obligation
    Q: account/uid-domain-isolation   open
    O: account/keychain-invariant     open   → tracks/account/obligations/keychain.md
  account-tooling       PROPOSED awaiting promotion (filed in tracks/account)

  account:  Q  4 answered /  3 open / 1 retired    O  2 discharged /  8 open (2 stubs)
  watcher:  Q 10 answered /  3 open / 1 retired    O  7 discharged /  9 open (1 external)
  total:    Q 14 answered /  6 open / 2 retired    O  9 discharged / 17 open (3 blocking)
```

## Task

Implement `status <project>` in `scripts/research-spike.py`. Four rules keep
the report trustworthy rather than decorative — each is a behaviour, not a
formatting preference:

1. **Readiness is derived, never stored.** A decision is **ready** when every
   question blocking it is `answered` or `retired` **and** every obligation
   marked `blocking:` it is `discharged`. The header counts `decided`, `ready`
   and `blocked` **separately**: "ready" is not "done", and the project gate is
   all required decisions **decided**, not ready. A `proposed` decision prints
   as `PROPOSED awaiting promotion` with the track it was filed in.
2. **The divergence signal survives the roll-up.** Print the question pair and
   the obligation pair **per track and per project**. Totals never print
   without the per-track breakdown — big projects are exactly where one sick
   track hides inside healthy totals. This is a **snapshot**: the script has no
   history and must not print a trend, a delta, or an arrow.
3. **Blocking obligations stay scarce.** Warn — **warn only, fixed threshold,
   deliberately not configurable** — when more than a third of open obligations
   carry `blocking:`. If everything blocks, nothing converges and the flag has
   become emphasis.
4. **Retired questions count separately everywhere.** Never fold `retired` into
   `answered`: that would let a project converge by giving up. When a
   **retirement** is what removed a decision's last blocker, say so on that
   decision's line.

Also:

- A blocked decision lists its blockers with source paths, as in the sample —
  the path is what makes the report actionable.
- Stub and external (receipt) counts appear in the per-track obligation column,
  from task 3's tallies.
- `status` with no project argument prints every project; an unknown project
  exits `1` with the list of known projects.
- `status` is a **report**: it never writes, and it exits `0` even when
  decisions are blocked. Only `validate` gates.

## Acceptance Criteria

**Code-enforced:**

- New fixtures in `scripts/test-research-spike.sh`:
  - a decision whose blockers are all answered/discharged prints `READY`;
  - one open question makes it `BLOCKED`, and the blocker line names the
    question id and its file;
  - an open `blocking:` obligation alone makes it `BLOCKED`, with the
    destination path shown;
  - a `decided` decision prints in the `decided` count and not in `ready`;
  - a `proposed` decision prints `PROPOSED` with its filing track;
  - retiring the last open question flips the decision to `READY` **and** the
    line says the retirement did it;
  - retired questions never appear inside the answered count;
  - per-track lines print even for a single-track project, and totals never
    appear without them;
  - more than a third of open obligations carrying `blocking:` emits the
    scarcity **warning** and still exits `0`;
  - `status` never modifies the tree (assert file mtimes/contents unchanged);
  - unknown project exits `1` listing known projects.
- `bash scripts/check.sh` green.

**User-run:**

- Run `status` against a fixture with one healthy and one sick track and
  confirm the sick track is visible in the per-track lines even though the
  totals look acceptable.
