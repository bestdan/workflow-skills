---
created: 2026-11-13
status: accepted
convention: ../research_spike.md
---

# research-spike: an obligation's destination must be an existing file in the repo

## Context

Four days into `aiutopilot`, the repo the ledger was extracted from, the
maintainer asked whether the project was infeasible. Questions were
converging: 7 answered against 6 filed. Obligations were not: roughly 6
discharged against 23 open, and none of them were counted. Of six deferrals
made in those days, three stayed visible and three went dark. **A deferral
stayed visible exactly when its destination was a file that already existed.**
"Defer to the account track" routed work to a track that did not exist, and
the sentence read as though it had routed it somewhere.

The design (`2026-08-01-research-spike-skill.md`, deleted on graduation) made
that the instrument's central rule. It landed in
[#322](https://github.com/bestdan/workflow-skills/pull/322); the terminal-status
check was added in
[#1050](https://github.com/bestdan/workflow-skills/pull/1050).

## Decision

An `obligation` block's `destination:` must be a normalized repo-relative path
to an existing regular file inside the repository. A directory does not
qualify, `../` traversal and a symlink that escapes the repo fail, and a path
into another research project fails. Work with no home yet gets a stub card
under `tracks/<track>/obligations/`, created first and announced. The skill
never creates a destination to make a record resolve.

Two rules shipped that the design did not have:

- A destination inside a `*_plan/` directory is a **warning**. `/push-plan`
  deletes plan directories, so the pointer will rot, but pointing at an
  in-flight plan is legitimate routing.
- An open obligation whose destination's frontmatter says `status: done` or
  `status: abandoned` is an **error**. The existence check cannot see a task
  file flipped to done in place, so without this the obligation stays open
  after the work it waited on has shipped.

## Consequences

- Good, because "defer to a thing that does not exist" fails the gate instead
  of reading as handled.
- Good, because the stub count becomes a diagnostic: each stub is work with
  nowhere to go, and a backfill that needs several is a finding.
- Bad, because every deferral costs a file, even one a reviewer would accept
  as prose.
- Bad, because the terminal-status check trusts one frontmatter vocabulary
  (`done`, `abandoned`); a destination using another word is not checked.

## Revisit when

- Adopters routinely create stub cards that carry nothing but the
  `superseded_when:` line, which would mean the file costs more than the
  visibility it buys.
- The task loop's terminal statuses change, which leaves
  `DESTINATION_TERMINAL_STATUSES` stale.
- `/push-plan` stops deleting plan directories, which removes the reason for
  the plan-directory warning.

## Confirmation

`validate` enforces all of it. `scripts/test-research-spike.sh` has fixtures
for a missing destination, a directory, traversal, a symlink escape, a
cross-project path, a plan directory and a terminal-status destination.

## Alternatives

- **Allow a directory:** a directory can exist while saying nothing about the
  work.
- **Allow free-text destinations ("the watcher"):** the exact failure the rule
  exists to stop.
