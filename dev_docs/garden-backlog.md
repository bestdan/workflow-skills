# garden-backlog — design and decisions

`/garden-backlog` tends a tracker backlog that was filled haphazardly. For each
open ticket in scope it decides whether the ticket is still valid, whether it
is a known fix or a spike, whether it is described well enough to pick up, how
big it is, how important it is, and which status it belongs in. A dry run
writes a report and nothing else; `apply` writes only what the owner approves
from that report. Any verdict the judge is unsure of goes to a human queue
instead of the tracker.

This doc records why the command is shaped the way it is. How it runs — the
arguments, the record schema, the report sections, the apply steps — is
[`commands/garden-backlog.md`](../commands/garden-backlog.md), and this doc
does not repeat it. It graduates the design from milestone
[#21](https://github.com/bestdan/workflow-skills/milestone/21).

The motivating case was a product team's Jira backlog: about 400 open tickets
on 2026-11-12, 283 with no Story Points and 308 at the default priority with no
recorded change. `/promote-tasks` could not fix that: on Jira
it backfills neither field, and it reads one unpaginated page of candidates.

## What it does not do

- **It never closes a ticket.** A ticket judged fixed, duplicate or obsolete is
  _marked_ for closure (`garden:close-candidate`), and a human closes it. The
  costliest error here is a false `fixed`, which closes the ticket and keeps
  the bug.
- **It never orders tickets.** Importance is objective, like severity, and says
  nothing about what to do first. The PM and EM trade it against feature work;
  a rubric that ranked one ticket against another would judge a ticket by its
  neighbours.
- **It ships only a Jira handler.** The core is written so Linear and gh-issue
  are one handler file each; every other handler stops as not yet implemented.
- **It adds no Severity field.** Importance goes to the native priority field
  for now.

## The per-ticket ladder

One pass per ticket, cheapest check first, so a ticket that is already well
filled costs one read:

1. **Early exit**, from the listing row alone: the ticket was gardened and is
   unchanged since, or a human set its size and importance, it passes the
   quality check, and it is ready or started.
2. **Validity** — a `fixed`, `duplicate` or `obsolete` verdict at high
   confidence proposes the closure mark and ends the ladder: a ticket about to
   close needs no better description.
3. **Kind**, **quality**, **size**, **importance**, **status**, **outcomes**, in
   that order.

The ladder's rules and each rung's verdicts are
[`commands/garden-backlog.md` §4](../commands/garden-backlog.md#4-judge-each-ticket-read-only).

## Core and handler

The command file owns the judgment, the per-ticket record and the report, and
speaks only symbolic values (`ready`, `major`, a Fibonacci size). A handler
file, `commands/handlers/<handler>-garden.md`, owns the tracker I/O: four
operations — List, Read, Write, Comment — defined in
[§2 of the command](../commands/garden-backlog.md#2-the-handler-contract).
Field encodings live with the tracker's other encodings, not in the garden
handler: Jira's `estimate_field` and `priority_map` are in
[`commands/handlers/jira-config.md`](../commands/handlers/jira-config.md). The
split keeps the judgment identical across trackers and lets a second handler
reuse the core unchanged.

The deterministic parts are scripts with tests, not prose:
`commands/handlers/assets/garden-checks.py` (the acceptance-criteria check, the
status derivation, the key search), `garden-overlay.py` (overlay paths), and
`jira-garden.py` (JQL, paging, rows, write payloads, the provenance comment).
Each docstring says why its rules are shaped as they are.

## Where the rubrics live

Each rung cites one definition rather than restating it, so the command is a
consumer of the same rules `/promote-tasks` and `/add-task` use:

| Rung       | Defined in                                                                                                                   |
| ---------- | ---------------------------------------------------------------------------------------------------------------------------- |
| validity   | [`skills/task/references/validity.md`](../skills/task/references/validity.md)                                                |
| kind       | [`skills/assess-task/SKILL.md`](../skills/assess-task/SKILL.md)                                                              |
| quality    | [`skills/task/SKILL.md`](../skills/task/SKILL.md) → **Confidence check**                                                     |
| size       | `skills/task/SKILL.md` → **Task size**; [`commands/handlers/task-fill.md`](../commands/handlers/task-fill.md) → **Estimate** |
| importance | [`skills/task/references/importance.md`](../skills/task/references/importance.md), then the repo overlay                     |

## Decisions

- **Dry run is the default, and `apply` writes only what an approved report
  lists.** The judge is a model reading free text, and a wrong write on a
  shared tracker is seen by a whole team. The report is where a wrong verdict
  is caught, so apply re-judges nothing: it re-reads each ticket, skips one
  that changed since the dry run, asks the owner to approve each write group,
  and writes in groups of at most 5 (the bound from the batch-writes incident
  in `jira-promote.md` step 5).
- **An epic is the default scope, and `--wide` widens it.** The calibration
  run on a 30-ticket epic on a real Jira site
  ([#1522](https://github.com/bestdan/workflow-skills/issues/1522), record in
  PR [#1560](https://github.com/bestdan/workflow-skills/pull/1560)) cost about
  440k judge tokens, so the whole backlog of about 400 tickets would cost about
  6M. A wider scope has to be asked for, and the handler refuses it before its
  first tracker read.
- **The importance rubric is generic, and a repo-local overlay carries the
  domain harms.** This plugin is public. The rubric names four harm classes and
  five levels; what those harms mean for one company's product is that
  company's content, so it lives in an overlay file the
  `garden.importance_overlay` key points at, outside this repo. The overlay may
  add harms and examples but never change the levels or their tests.
- **Importance is never written until the PM / EM sign-off.** Importance lands
  in the priority field, which the PM and EM use to trade work off. A rubric
  they have not agreed to would overwrite their field with someone else's
  judgment. Until the sign-off table in `importance.md` is filled, the report
  shows the level and proposes no write.
- **Kind is written only at high confidence, through `apply`, and never over a
  human-set kind.** On Jira a kind write changes the issue type, which changes
  how the ticket is worked and can be refused across workflows. A human who set
  the type knew something the text may not say, so a disagreement goes to the
  human queue.
- **Status respects blockers.** A quality-passing ticket with an unresolved
  blocker stays where it is, and so does one whose blocker links were not
  read: moving it to the ready status would tell a reader, and the task loop,
  that it can be picked up while it cannot. Neither gets the "gardened" mark,
  so the next run judges it again. Started work is never moved.
- **Status is a verdict, not a default.** The command targets the refinement
  status only when the quality check failed, and the report names the
  condition. A ticket held by a blocker, or whose quality verdict is unknown,
  keeps its current status, which may already be the refinement status. The
  statuses reuse `/promote-tasks`' `ready_status` and `refinement_status` keys.
- **Importance has its own five-level scale** (`blocker` / `critical` / `major`
  / `minor` / `trivial`), mapped one-to-one onto a site's priority names.
  Task-fill's priority vocabulary has four non-empty levels, which cannot
  separate "happening now" from "conditional".
- **A human-set value is never overwritten.** On Jira, priority is never empty,
  so "human-set" means a non-default value or a recorded `priority CHANGED`. A
  ticket deliberately left at the default looks untouched and is re-judged;
  the dry run catches a wrong proposal.
- **Closure and human review are labels**, `garden:close-candidate` and
  `garden:needs-human`, because a label carries across trackers and needs no
  workflow change.
- **Calibration records hold only ticket keys and verdicts, never ticket
  text.** This repo is public and the tickets are a work tracker's. Titles,
  descriptions, reasons and work-repo code pointers stay in the full report on
  the owner's machine; the committed record is the key, each verdict and its
  confidence.

## Adding a handler

1. Write `commands/handlers/<handler>-garden.md` implementing the four
   operations in [§2 of the command](../commands/garden-backlog.md#2-the-handler-contract).
   Define **human-set** for the tracker, map its statuses onto `initial` /
   `ready` / `refinement` / `started` / `other`, and refuse a scope wider than
   one epic (or the tracker's equivalent) unless `wide` is set.
   `jira-garden.md` is the template.
2. Add the tracker's field encodings to its existing config doc and the
   `task-fill.md` **Adapters** table, not to the garden handler.
3. Point validity rung 1 at the tracker's own-PR rule in `validity.md`.
4. Route the handler in the command's §1, and update the `garden` row of the
   capability matrix in `commands/task-config.md`.
5. Put deterministic logic in a tested script under
   `commands/handlers/assets/`, as `jira-garden.py` does.
