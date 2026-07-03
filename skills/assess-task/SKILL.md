---
name: assess-task
description: Use when you need a structured read on what a coding task actually demands — before routing it to a coder, sizing it, or scoring it — e.g. "how hard/creative/mechanical is this", "profile this task", "what does this work need", or /assess-task. Scores a task description along stable dimensions (complexity, creativity, scope, autonomy, speed/cost sensitivity, verification criticality) and returns a compact `task_profile` block plus a routing label. Pure assessment — it never picks a model, sizes, or dispatches; select-coder, break-down-task, and promote-tasks consume its output.
user-invocable: true
---

# assess-task — profile what a coding task demands

Read a task description and emit a **structured profile** of what the work
needs — how hard, how creative, how big, how autonomous, how latency- or
cost-sensitive, and how much its value rides on verification. The profile is a
stable contract other skills consume:

- **select-coder** maps the profile → ranked `<backend>:<model>` candidates.
- **break-down-task** uses `complexity` + `scope` as a sizing signal.
- **promote-tasks** uses the profile as one input to its confidence check.

This skill **only assesses**. It never recommends a model, assigns a size, or
dispatches work — that belongs to the consumers above. Keep it that way so the
profile stays a single source of truth rather than three divergent rubrics.

## The `task_profile` contract

The output is a compact YAML block. Every field is a fixed enum — consumers
switch on the values, so don't invent new ones.

```yaml
task_profile:
  complexity: mechanical | standard | hard # reasoning / multi-file accuracy demand
  creativity: low | medium | high # design taste, frontend, API & naming ergonomics
  scope: single-file | pr-sized | multi-file | whole-codebase
  autonomy: bounded | long-horizon # steps completable without steering
  speed_sensitivity: low | high # is a tight, latency-critical loop the point?
  cost_sensitivity: low | high # is this cost-dominated / high-volume bulk?
  verification_criticality: low | high # is honestly running the checks the deliverable?
  label: <routing label> # derived summary — see the table below
  confidence: high | low # low ⇒ genuinely ambiguous between profiles
  notes: <one line> # assumptions and, on low confidence, the runner-up label
```

### Dimension rubric

Score each dimension from the description. Most tasks are obvious; reach for the
harder value only when the description warrants it.

| Dimension                    | Pick the higher value when…                                                                                                                                                                           |
| ---------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **complexity**               | `hard`: architecture, cross-cutting refactor, subtle bug, or unclear approach. `standard`: a normal PR-sized feature/fix. `mechanical`: rename, config churn, mechanical edits with an obvious shape. |
| **creativity**               | `high`: frontend/visual, API surface, naming, or anything where taste is the deliverable. `medium`: some design latitude. `low`: the shape is dictated.                                               |
| **scope**                    | Count the blast radius: `single-file`, `pr-sized` (a handful of files), `multi-file` (broad refactor), `whole-codebase` (needs to read most of the tree, e.g. 1M-token context).                      |
| **autonomy**                 | `long-horizon`: many dependent steps, overnight-scale, little chance to steer mid-run. `bounded`: finishes in one focused pass.                                                                       |
| **speed_sensitivity**        | `high`: a tight iteration loop where wall-clock per turn dominates value. Otherwise `low`.                                                                                                            |
| **cost_sensitivity**         | `high`: bulk/high-volume work, or the task is only worth doing cheaply. Otherwise `low`.                                                                                                              |
| **verification_criticality** | `high`: the deliverable _is_ passing/reporting checks honestly (the check is the point). `low`: an edit whose correctness is easy to eyeball or re-run downstream.                                    |

### Deriving `label`

The label is a one-word summary of the dominant demand, drawn from a fixed set.
When several fit, pick the one that captures why the task is _hard to route_ —
the standout dimension wins over the mild ones.

| Label                    | Fires when the profile is dominated by…                                          |
| ------------------------ | -------------------------------------------------------------------------------- |
| `architecture`           | `complexity: hard` with `multi-file`/refactor scope, or a genuinely hard bug.    |
| `standard-pr`            | The unremarkable middle — `standard` complexity, `pr-sized`, nothing extreme.    |
| `mechanical-bulk`        | `mechanical` complexity and/or `cost_sensitivity: high` high-volume simple work. |
| `frontend-creative`      | `creativity: high` — design, frontend/visual, API & naming ergonomics.           |
| `latency-loop`           | `speed_sensitivity: high` — a latency-critical tight loop.                       |
| `whole-codebase`         | `scope: whole-codebase` — needs to read most of the tree at once.                |
| `verification-sensitive` | `verification_criticality: high` — the check is the task.                        |
| `long-horizon`           | `autonomy: long-horizon` — overnight-scale autonomous work.                      |

If two labels are genuinely tied, set `confidence: low` and name the runner-up
in `notes` — consumers (e.g. select-coder) can then weigh both.

## Procedure

1. **Read the task.** A description, a task card (`skills/task/SKILL.md`
   shape), or a plan file under `dev_docs/tasks/<name>_plan/`. Use
   `related_files` / linked context if the raw description is thin.
2. **Score the seven dimensions** against the rubric. Prefer the common value;
   only escalate when the description earns it.
3. **Derive the label** and set `confidence`.
4. **Emit the `task_profile` block** — nothing else when called as a subagent;
   the block _is_ the return value. When a human invoked `/assess-task`, follow
   the block with one sentence of plain-English justification.

### Ambiguity

When genuinely torn between two profiles, ask **one** clarifying question rather
than guessing — **unless running non-interactively** (as a subagent, e.g. under
select-coder or orchestrate-coders, where no user is reachable): then don't
block. Pick the most likely profile, set `confidence: low`, and record the
runner-up label in `notes` so the caller can override. This mirrors how
select-coder handles ambiguity, so the two never diverge.

## Invocation

```
/assess-task <task description | --plan <name>>
```

- `<task description>` — the task to profile.
- `--plan <name>` — profile each task file in `dev_docs/tasks/<name>_plan/`
  and emit one `task_profile` block per file, keyed by filename.

## Rules

- Emit **only** the enum values in the contract — never freeform dimension
  labels. A consumer that gets `complexity: gnarly` can't route it.
- Assess; don't decide. No model names, no size numbers, no dispatch — those are
  the consumers' jobs. If asked to also pick a coder, hand off to
  `select-coder` rather than answering inline.
- One profile per task. For a batch (`--plan`), score each file independently;
  don't average them into a single blended profile.
