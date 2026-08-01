---
type: epic
title: "research-spike — ship the obligation ledger as a skill"
status: active
owner: dp.egan
created: 2026-08-01
---

# research-spike

Implements [`dev_docs/designs/research_spike_skill.md`](../../designs/research_spike_skill.md).
Read that design first — it is settled (dual-reviewed, 2026-08-01) and this plan
does not re-litigate it. Every task below cites the design section it builds.

## Goal

Ship `research-spike`: a skill for managing long-running research spikes, whose
load-bearing instrument is an **obligation ledger**. A research spike answers
questions before building; answering a question mostly creates _obligations_,
not new questions, and obligations that are deferred into prose (rather than
into a file that exists) accrue invisibly. The reference implementation
(`aiutopilot` PR #25) measured **~6 discharged against 23 open** after four
days, while the question count looked flat-to-down and healthy.

The instrument makes deferred work addressable: a deferral must name a
`destination:` **path that already exists**, every question section must
declare its obligations or explicitly declare `none:` with a reason, and four
counts are stored (not computed on demand) so the divergence between
questions-converging and obligations-climbing is legible in one line.

## Scope / non-goals

**In scope**

- `scripts/research-spike.py` — stdlib-only, `--root`-testable, the only thing
  CI ever runs: `init`, `validate`, `ledger` / `write-ledger`, `status`,
  `suggest`.
- `skills/research-spike/SKILL.md` — the judgment half: `file`, `answer`,
  `defer`, `backfill`, `promote-decision`.
- The two bridges to the existing task loop, both routed through **receipt
  cards** so the destination-must-exist invariant is never bent.
- Hermetic fixture tests under `--root`, wired into `scripts/check.sh`.

**Non-goals (v1)**

- **No `sweep` verb.** Bridged work is not reconciled: completing a bridged
  task in Linear/Jira does not discharge the local obligation. Receipt cards
  are what make a later `sweep` possible; building it now is out of scope
  (design § "Bridges", § "Known limits").
- **No cross-project destinations.** A destination pointing into a foreign
  project tree is a validation error, so every project's inbound dependencies
  are visible in its own ledger.
- **No trend computation.** The ledgers store no history. The divergence signal
  is a snapshot read by a human across commits; the script must not pretend to
  report a trend.
- **No auto-repair.** `validate` never rewrites a stale ledger and never
  creates a missing destination — both are how this becomes theatre.
- **No lexical gate.** `suggest` stays advisory and can never exit non-zero
  (measured: 29 hits on the reference tree, mostly prose describing behaviour).
- **No `/work-track` dispatcher.** The structure makes tracks agent-sized
  context bundles; actually dispatching agents is deliberately later.

## Approach

**The hard boundary is script vs LLM, and it drives the task split.** If two
runs over the same tree could disagree, it belongs to the script: parsing,
every validation rule, decision-status computation, the `status` report, ledger
derivation and freshness, `suggest`. The LLM contributes prose, proposed ids,
`none:` reasons, the interactive dialogues, and the judgment of _whether_
something is a deferral at all — and never computes a status or a count.

Phase 1 (tasks 1–8) therefore builds the whole script before any SKILL.md
prose exists. That ordering is also the design's own hard-won sequencing
lesson: **land the instrument first, then let live work register its own
deferrals** — the mechanism was first built stacked behind the plan it was
auditing, which had it describing only work already done.

Choices made here that the design left environmental:

| Choice                                                                   | Why                                                                                                                                                        |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py"`, no `uv run` | The design mandates stdlib-only, so there is no dependency to lock — this matches `commands/handlers/assets/*.py` rather than `validate.py`/`task-scan.py` |
| Bash fixture harness `scripts/test-research-spike.sh`                    | Mirrors `test-validate.sh` / `test-task-scan.sh`; every fixture tree is a `mktemp -d` passed via `--root`, so no test ever reads the real repo             |
| Validation split across tasks 3/4/5 by **record type**                   | Each lands with its own fixtures and stays inside the size-5 budget; the alternative (one big validate task) is a ~700-line PR nobody can review           |
| Flat task files, no `phase_N/` nesting                                   | Three phases exist conceptually but the dependency chain already encodes them; nesting only adds path churn                                                |

Two implementation traps the design paid for and this plan must not re-pay:

1. **The generated ledger must survive `dprint`.** If the formatter rewrites
   the generated block, the freshness check and the formatter fight forever.
   Use a bullet list, not an aligned table, and verify it explicitly (task 7).
2. **`scripts/research-spike.py` is ruff-formatted by `dprint`** (the ruff
   plugin is registered in `dprint.json`), so `dprint check` gates the script's
   formatting as well as the markdown.

## Tasks

**Phase 1 — the script.** Tasks 2, 3, 4 and 8 are parallel once 1 lands.

1. [research_spike_task_1.md](research_spike_task_1.md) — script skeleton:
   CLI dispatch, `--root`, tree discovery, the fenced-block parser and record
   model, plus the test harness wired into `check.sh`.
2. [research_spike_task_2.md](research_spike_task_2.md) — `init <project>` and
   `--track`: scaffold the directory convention the skill owns.
3. [research_spike_task_3.md](research_spike_task_3.md) — obligation and card
   validation, including the tightened `destination` rules.
4. [research_spike_task_4.md](research_spike_task_4.md) — question records,
   the coverage rule, and `contracts/` coverage.
5. [research_spike_task_5.md](research_spike_task_5.md) — decisions and
   cross-record referential integrity.
6. [research_spike_task_6.md](research_spike_task_6.md) — derived readiness
   and the `status` convergence report.
7. [research_spike_task_7.md](research_spike_task_7.md) — the ledgers:
   derivation, storage, scoped freshness, and the `--strict` tier.
8. [research_spike_task_8.md](research_spike_task_8.md) — `suggest`, advisory
   and structurally unable to fail.

**Phase 2 — the skill.**

9. [research_spike_task_9.md](research_spike_task_9.md) — `SKILL.md` and the
   record-grammar reference: the five procedures and the script/LLM boundary.
10. [research_spike_task_10.md](research_spike_task_10.md) — the two bridges
    to the task loop, via receipt cards.
11. [research_spike_task_11.md](research_spike_task_11.md) — packaging:
    README count, trigger eval, claude.ai zip bundling.

**Phase 3 — adoption and cleanup.**

12. [research_spike_task_12.md](research_spike_task_12.md) — the adoption
    playbook and a real backfill, which is the moment of truth.
13. [research_spike_task_13.md](research_spike_task_13.md) — graduate the
    durable wisdom to `dev_docs/research_spike.md` and delete this folder.

## Decisions taken while planning

Resolved with the maintainer on 2026-08-01. Recorded here so a later reader
does not reopen them by accident.

1. **Command surface: skill only.** All five procedures live inside
   `SKILL.md`, invoked conversationally — matching `plan-with-docs` and
   `co-review` rather than the task loop's one-command-per-verb convention.
   The design calls them "SKILL.md procedures", and a command layer would be a
   second surface to keep in sync with no verb it could express better. No
   `commands/` file in v1; task 11 bumps only the **skills** count.
2. **No live research project in this plan.** The gate is the fixture harness
   (`scripts/test-research-spike.sh`) only; `validate` is **not** wired into
   `scripts/check.sh`, because this repo will have no `dev_docs/research/` tree
   when the plan lands and a gate over a nonexistent tree measures nothing. The
   first real spike — the auto-pilot E-lite substrate questions (PR #243) are
   the obvious candidate — is initialized by whoever runs it, following task
   12's playbook, which is also when the gate gets wired.
3. **The plan is committed, not pushed.** `dev_docs/tasks/*` is gitignored
   here, so these files carry the scoped negation the `.gitignore` comment
   documents; the plan is reviewable in its PR and survives the session. Task
   13 removes the folder **and** the negation at completion. `/push-plan` to
   Linear stays available later.

One consequence worth naming: decision 2 means the instrument ships **unused**.
That is the design's own sequencing lesson applied — land the instrument first,
then let live work register its own deferrals — but it does mean the first
backfill, the step where the finding actually lives, happens after this plan
closes.
