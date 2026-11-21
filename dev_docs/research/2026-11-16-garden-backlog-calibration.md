---
created: 2026-11-16
question: "Do /garden-backlog's dry-run verdicts on one real Jira epic match what the owner would decide, and where does the procedure break when run end to end?"
feeds:
---

# `/garden-backlog` calibration dry run on one Jira epic (run 2026-11-16)

## Question

Do `/garden-backlog`'s dry-run verdicts on one real Jira epic match what the
owner would decide, and where does the procedure break when run end to end?
This is the record for [#1522](https://github.com/bestdan/workflow-skills/issues/1522).
**The owner's marks are not in yet.** The agreement counts that #1522's
acceptance criteria need cannot be computed until they are, so this record
does not say whether calibration passed.

## Method

- **Scope.** Epic `IP-122` on the work Jira site: every open, non-epic child
  outside an active sprint, as `jira-garden.py jql --scope IP-122` builds the
  query. That gave **30 tickets** (27 `IP`, plus 3 children with other project
  keys). The issue asked for 15–25; the owner chose this epic.
- **Plugin.** `origin/main` at `b18d1353`, which includes `52fd2db4`,
  `e2c945b0` and `5e12b5eb`.
- **Mode.** Dry run only. `apply` was not run, and **nothing was written to
  Jira**: no edit, transition, comment, label, worklog or link. Only the
  handler's List operation ran. The Write and Comment operations did not.
- **Config.** A gitignored `.task-config.local.yml` override for this run:
  `handler: jira`, project `IP`, `ready_status: Ready for Development`,
  `refinement_status: Needs Refinement`,
  `blocked_statuses: [On Hold/Blocked]`, the story-points field as
  `estimate_field`, `priority_map` mapping the five importance levels onto
  `P0-Blocker`…`P4-Trivial`, and `garden.importance_overlay` pointed at the
  Betterment overlay from #1557's PR body. The overlay was written to this
  branch's work-tmp directory and is not committed. The project's default
  priority (`P3-Minor`) came from the MCP's create metadata, as
  `jira-garden.md` step 1.3 says.
- **List.** `jira-garden.py fetch` over twg (1 page per query), then `rows`.
  Validity rung 1 ran through `linear-pr-resolve.py` for all 30 rows.
- **Judging.** As §4's fan-out says: one parent group, split at 25 into two
  chunks of 15, each judged by one read-only subagent. Each subagent got
  §3–§4, the rubric paths, the overlay, and its rows with descriptions and
  comments. Code was read at `origin/HEAD` of three private work repos,
  fetched the same day.
- **What is left out.** This repo is public. Ticket titles, descriptions,
  per-verdict reasons, code paths and the work repos' commits are not here.
  They are in the full dry-run report, which the owner holds locally. Each
  row below has the verdict and confidence only, so the owner marks against
  the full report.

## Cost

| Measure                               | Value                                                                           |
| ------------------------------------- | ------------------------------------------------------------------------------- |
| Tickets judged                        | 30 (1 early exit)                                                               |
| Wall time, first fetch to last record | about 17 min (the two judges ran in parallel, 9–10 min each)                    |
| Judge subagent tokens                 | about 440k (218k + 223k), 105 tool calls                                        |
| Jira reads                            | 2 twg search pages, 1 epic read, 2 MCP metadata reads; 0 writes                 |
| Proposed writes (would need `apply`)  | 20 `human-queue`, 6 `status`, 3 `gardened`, 2 `size`, 1 `closure`, 0 importance |

## Findings

### Verdict counts

Verdicts are the judges'. Confidence is high / medium / low as each judge
gave it. For quality and status the command defines no confidence, so the
judges were asked for one anyway.

| Dimension  | Counts                                                                                                                       |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------- |
| Validity   | valid 18 (5 high, 13 medium); unsure 9; duplicate 1 (medium); fixed 1 (high); not judged 1 (early exit)                      |
| Kind       | fix 21; spike 6; unclear 3 (all low)                                                                                         |
| Quality    | pass 10; fail `has_acceptance_criteria` 18; fail `scope_fits` 2                                                              |
| Size       | 1: 2; 2: 6; 3: 14; 5: 6; split (over 5): 2                                                                                   |
| Importance | critical 9 (7 low, 2 high); major 5 (3 low, 2 high); minor 14 (8 high); trivial 2. High-confidence verdicts: 12              |
| Status     | refinement 20 (all already there, so no write); ready 9 (6 would move: `IP-831`–`IP-835`, `IP-1045`); unchanged 1 (`IP-875`) |

The one high-confidence `fixed`, `IP-875`, was re-checked by hand. The merged
commit that carries its key is reachable from the work repo's `origin/HEAD`.
The owner still has to confirm that the change matches the ticket. The shipped
link text differs from the text the ticket asks for.

### Per-ticket verdicts, for the owner to mark

| Key     | Validity                      | Kind                        | Quality                          | Size                              | Importance                           | Status                        | Human queue | Proposed writes                          | Owner verdict |
| ------- | ----------------------------- | --------------------------- | -------------------------------- | --------------------------------- | ------------------------------------ | ----------------------------- | ----------- | ---------------------------------------- | ------------- |
| CXI-393 | unsure (high)                 | spike (medium)              | has_acceptance_criteria (high)   | 2 (low)                           | critical (low)                       | refinement (high)             | 3           | human-queue                              |               |
| IP-39   | unsure (medium)               | fix (medium)                | has_acceptance_criteria (high)   | 3 (low)                           | critical (low)                       | refinement (high)             | 4           | human-queue                              |               |
| IP-43   | unsure (medium)               | fix (medium)                | has_acceptance_criteria (high)   | 3 (low)                           | major (low)                          | refinement (high)             | 3           | human-queue                              |               |
| IP-45   | unsure (high)                 | unclear (low)               | has_acceptance_criteria (high)   | 3 (low)                           | critical (low)                       | refinement (high)             | 4           | human-queue                              |               |
| IP-50   | unsure (medium)               | fix (high)                  | has_acceptance_criteria (high)   | 5 (medium)                        | critical (high)                      | refinement (high)             | 2           | human-queue                              |               |
| IP-51   | duplicate (medium)            | fix (medium)                | has_acceptance_criteria (medium) | 3 (low)                           | critical (low)                       | refinement (high)             | 3           | human-queue                              |               |
| IP-55   | valid (high)                  | spike (high)                | has_acceptance_criteria (high)   | 3 (medium)                        | minor (low)                          | refinement (high)             | 1           | human-queue                              |               |
| IP-57   | valid (medium)                | spike (medium)              | has_acceptance_criteria (high)   | 2 (medium)                        | critical (low)                       | refinement (high)             | 2           | human-queue                              |               |
| IP-63   | unsure (medium)               | fix (high)                  | has_acceptance_criteria (medium) | 3 (medium)                        | minor (low)                          | refinement (medium)           | 2           | size, human-queue                        |               |
| IP-68   | valid (medium)                | fix (medium)                | has_acceptance_criteria (high)   | 2 (medium)                        | minor (high)                         | refinement (high)             | 2           | human-queue                              |               |
| IP-85   | unsure (medium)               | fix (high)                  | has_acceptance_criteria (high)   | 3 (medium)                        | major (high)                         | refinement (high)             | 1           | human-queue                              |               |
| IP-92   | valid (medium)                | fix (medium)                | has_acceptance_criteria (high)   | 3 (low)                           | minor (low)                          | refinement (high)             | 2           | human-queue                              |               |
| IP-93   | unsure (high)                 | fix (medium)                | has_acceptance_criteria (high)   | 5 (low)                           | major (low)                          | refinement (high)             | 3           | human-queue                              |               |
| IP-103  | unsure (medium)               | spike (medium)              | has_acceptance_criteria (high)   | 3 (medium)                        | critical (low)                       | refinement (high)             | 3           | human-queue                              |               |
| IP-731  | valid (high)                  | fix (high)                  | has_acceptance_criteria (medium) | 5 (medium)                        | critical (high)                      | refinement (medium)           | 0           | gardened                                 |               |
| IP-733  | valid (medium)                | fix (high)                  | scope_fits (medium)              | split (>5) (medium)               | critical (low)                       | refinement (unchanged) (high) | 3           | human-queue                              |               |
| IP-736  | not judged (early exit) (low) | spike (not recorded) (high) | pass (high)                      | 3 human-set (not judged) (medium) | trivial human-set (not judged) (low) | ready (unchanged) (high)      | 0           | early exit: human-set and passes quality |               |
| IP-812  | valid (medium)                | spike (high)                | has_acceptance_criteria (medium) | 3 (medium)                        | major (low)                          | refinement (unchanged) (high) | 3           | human-queue                              |               |
| IP-813  | valid (high)                  | fix (high)                  | scope_fits (high)                | split (>5) (high)                 | minor (medium)                       | refinement (unchanged) (high) | 3           | human-queue                              |               |
| IP-829  | valid (medium)                | fix (high)                  | pass (high)                      | 3 (high)                          | minor (high)                         | ready (unchanged) (high)      | 0           | gardened                                 |               |
| IP-830  | valid (medium)                | fix (high)                  | pass (high)                      | 3 (high)                          | minor (high)                         | ready (unchanged) (high)      | 0           | gardened                                 |               |
| IP-831  | valid (medium)                | fix (high)                  | pass (medium)                    | 5 (medium)                        | minor (high)                         | ready (medium)                | 0           | status                                   |               |
| IP-832  | valid (medium)                | fix (high)                  | pass (high)                      | 5 (medium)                        | minor (high)                         | ready (medium)                | 0           | status                                   |               |
| IP-833  | valid (medium)                | fix (high)                  | pass (high)                      | 2 (high)                          | minor (high)                         | ready (medium)                | 0           | status                                   |               |
| IP-834  | valid (high)                  | fix (high)                  | pass (high)                      | 3 (medium)                        | minor (high)                         | ready (medium)                | 0           | status                                   |               |
| IP-835  | valid (medium)                | fix (high)                  | pass (high)                      | 1 (high)                          | trivial (low)                        | ready (medium)                | 1           | status, human-queue                      |               |
| IP-875  | fixed (high)                  | fix (not recorded) (high)   | pass (not recorded) (high)       | 1 (not recorded) (high)           | minor (not recorded) (medium)        | unchanged (high)              | 0           | closure                                  |               |
| IP-1045 | valid (high)                  | fix (high)                  | pass (medium)                    | 5 (medium)                        | major (high)                         | ready (medium)                | 0           | size, status                             |               |
| IX-916  | valid (medium)                | unclear (low)               | has_acceptance_criteria (high)   | 2 (low)                           | minor (low)                          | refinement (unchanged) (high) | 3           | human-queue                              |               |
| IX-918  | valid (medium)                | unclear (low)               | has_acceptance_criteria (high)   | 2 (low)                           | minor (high)                         | refinement (unchanged) (high) | 3           | human-queue                              |               |

### Where the procedure broke or behaved oddly

Each item says what was observed and where it surfaced. None was fixed in this
run.

1. **The two judges read `has_acceptance_criteria` differently, so quality and
   status depend on which chunk a ticket was in.** The chunk-1 judge required
   a literal Acceptance Criteria section, and 15 of its 15 tickets failed. A
   bug with Expected/Actual and repro steps (`IP-731`) failed too. The chunk-2
   judge used `jira-promote.md`'s "equivalent concrete outcome" reading, and
   10 of its 15 passed. Status follows quality, so this one disagreement
   decides the status target for about half the epic.
2. **Rung 1 is blind on tickets the plugin did not deliver.** The own-PR trail
   is built only from the plugin's `PR opened:` and claim comments, so all 30
   rows resolved `no_pr`. Yet `IP-875`'s fix had merged under a PR whose title
   carries the key, and only the rung-2 search found it.
3. **Rung 2's searches miss as written.** `gh search prs "IP-39"` matches the
   bare token `IP`, which returns unrelated PRs. `git log -E --grep` with `\b`
   silently matches nothing. A key search needed `git log -P --grep
   '<key>(?![0-9])'`.
4. **A sub-`high` `fixed` gets two labels.** `validity.md` §4 reports it as
   `unsure`, while `garden-backlog.md` §4 queues it as
   `validity fixed at <confidence>` (`IP-50`'s lead).
5. **The overlay pushes most of the epic's bugs to `critical`.** "A projection …
   shown to a user that is wrong" is a harm class, and the rubric's "any
   harm-class consequence is at least `critical`, however rare" then applies.
   The result is 9 `critical` verdicts, 7 of them low-confidence, often against
   a human-set `P2`/`P3`.
6. **The human queue fills with fields that cannot be written.** Low-confidence
   importance adds a `human_queue` reason, and so the `garden:needs-human`
   label, even while the empty sign-off table blocks every importance write.
   20 of 30 tickets would get the label.
7. **Status ignores blockers and owed decisions.** "Quality pass → ready" would
   move `IP-831`–`IP-835` to Ready while each is still blocked by an earlier
   slice. Jira has no counterpart of the file path's `is_blocked_by` hold.
   `IP-1045` would go to Ready even though its criteria include a product
   sign-off that has not happened.
8. **Unsure validity holds the status write but not the size write.** `IP-63`
   gets a size write with validity `unsure`. Separately, `task-fill.md`'s Jira
   adapter says Jira has no estimate field, which contradicts
   `jira-config.md`'s `estimate_field`.
9. **The refinement status is the initial status here.** `jira-garden.md`
   requires `refinement_status` and says to stop and ask when it is unset.
   `jira-config.md` says to leave it unset when the initial status is named
   `Needs Refinement`, as it is on this board. With it set, every failing
   ticket is "already in refinement", so the run proposed no refinement
   writes. The stop-and-ask cannot run unattended or in a subagent, so the
   value was set by hand.
10. **The duplicate rule does not fit the ladder.** The twg-jira
    `duplicates.md` rule forbids reading comments and links, while the ladder
    supplies them, and one ticket's comments name its likely duplicate. It
    scores 0.50–0.95, which nothing maps onto high / medium / low. It is
    symmetric, so it does not say which ticket of a pair stays open. The judge
    marked the higher key (`IP-51`) duplicate.
11. **§3 has no shape for a closure record's unjudged fields.** After a
    closure verdict the ladder stops, but §3 defines a key-only record only
    for early exits, so the judge recorded `null`s.
12. **Validity is written for bugs.** For new-build slices (`IP-829`–`IP-833`),
    `valid` can rest only on the new code being absent, while "never `valid`
    where you could not find the code" pulls toward `unsure`. The judge gave
    `valid` at medium confidence and cited the legacy code each slice
    replaces.
13. **The overlay key reaches outside the repo.** §1 says the key "must name a
    file in the repo", but a `../`-relative path to an uncommitted file outside
    the checkout resolves and passes the check. This run relied on that.

## Pending

- The owner marks the per-ticket table against the full report.
- Then the #1522 comparison: agreements per dimension, every false `fixed`,
  and which disagreements the low-confidence flag caught.
- Rubric changes go to `importance.md` (#1518) and `validity.md` (#1519).

## Feeds

#1522's comparison, and through it #1518 and #1519.
