# Tier forecast and Intent confirmation

`/promote-tasks` runs this step for every candidate that passed the rest of the
confidence check (`SKILL.md` → **Confidence check**). It answers one question
before the task is promoted: will this task's PR need the owner's review, and if
so, has someone other than its author checked what it rests on? Every handler
follows this file. Each handler file says only where its writes go.

The point is checks and balances, not owner sign-off. A task that will change
prompt text, docs or the landing gate is promoted only once an agent that
neither wrote the task nor runs the promotion has confirmed its premise. The
lander (`scripts/land-pr.py`) still re-tiers the real PR at merge, so the
forecast never decides whether anything lands. It only makes the premise check
happen before the work starts, not in review.

## 1. List the paths, then forecast

List the repo paths the task is expected to **change**, from its `## Task` and
`## Context` sections, plus `related_files` on the file path. List paths, not
directories, and not files the task only reads. When the body names no path
you can identify, pass an empty list: the forecast is then `human`. Never guess
paths to get an `auto`.

Write `{"paths": [...], "body": "<the task's markdown>"}` to a file and run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/tier-forecast.py" --task <file>
```

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/scripts/tier-forecast.py`.

It reads the policy from `origin/HEAD`, never the working tree. It prints one
JSON object, then `FORECAST: auto|human|skipped`. The fields used below are
`forecast`, `reasons`, `intent.state`, `intent.missing` and `consult_bar`. The
script's docstring defines each one.

| `forecast` | What it means                                                                 | Do this                                  |
| ---------- | ----------------------------------------------------------------------------- | ---------------------------------------- |
| `skipped`  | The repo has no merge policy, so no lander and nothing for an Intent to gate. | Promote as the rest of the check says.   |
| `auto`     | Every path is in the policy's `auto` globs.                                   | Promote as the rest of the check says.   |
| `human`    | A path is human-tier, or the forecast could not be made (see `reasons`).      | Confirm the Intent block (step 2) first. |

Record the forecast on the card whatever it is: `forecast`, plus the first
reason for `human`. That lets the owner see which tasks will need their review.

## 2. The Intent block

A `human` task needs an `## Intent` block of three labelled lines:

```markdown
## Intent

- **Change:** what the PR will change, in behaviour, in one sentence.
- **Premise:** the fact about the repo or the world it rests on, which could be wrong.
- **Finish:** how anyone can tell it is done.
```

When `intent.state` is `partial` or `absent`, draft the missing lines from the
task. The draft goes to the confirmer in step 3 like any other block. Do not
write a premise you have not read the repo for. Write the claim the task
actually depends on: "`gh-issue-promote.md` writes labels and comments, never
bodies" is a premise, "the handlers can be extended" is not.

The task's `## Acceptance Criteria` is its finish condition, and the existing
check already requires at least one bullet there. `Finish:` names the one
criterion that shows the work is done; it is not a second copy of the list.

## 3. Confirm with a different agent

The promoter never confirms the block, whether it drafted the block or the
task's author wrote it. Dispatch a fresh-context `agent-guidance:consultant`.
Pick its model the way the `agent-guidance:consult` skill's step 3 does: the
next tier above yours, or your own at the top tier, never lower. A fresh agent
did not write the task and is not running the promotion. Give it:

- the task's full body, and the Intent block as one decision: "D1: does this
  Intent block hold for this repo? Confirm or reject; you may reject all of it";
- the repo path, and the paths from step 1;
- three checks: that the **premise** holds in the repo as it is now (read
  the code, do not trust the task), that the **change** is what the task's
  `## Task` section asks for and no more, and that the **finish** condition can
  be checked by someone other than its author.

Do not give it your own view of the block, or say who wrote it.

| Verdict                                | Do this                                                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| confirm, confidence ≥ `consult_bar`    | Promote. Record the block, the confirmer, and its confidence on the card.                                     |
| confirm below `consult_bar`, or reject | `needs_refinement`, reason `intent not confirmed: <crux>`. Record the block (drafted or not) and the verdict. |

`consult_bar` comes from the policy (default 85), the same bar the lander uses
for consult evidence. A rejected or unconfirmed block reaches the owner through
the existing `needs_refinement` path, with the crux already written. Their move
back to `ready` is what clears it. The promoter never re-scores a card past
`new`.

A failed dispatch (the consultant can't be spawned, or returns nothing) is
not a confirmation. Treat it as below the bar and say so in the reason.

Under `dry-run`, forecast and draft but do not dispatch the confirmer. A
`human` card reports as `ready if confirmed`, with its drafted block, and
nothing is written.

## Where things are recorded

| Handler          | Forecast, Intent block and verdict go in                   |
| ---------------- | ---------------------------------------------------------- |
| `repo-pr`        | `commands/promote-tasks.md` steps 2–4                      |
| tracker handlers | each `*-promote.md`, in one comment per forecast candidate |

A tracker handler writes labels, states and comments, never the issue body, so
everything goes in the one comment the handler writes for a candidate, which
also carries any failed check and backfill note. Every candidate that reached
the forecast gets that comment, a HIGH `auto` one with nothing else to say
included:

- `tier: <forecast>`, and for `human` its first reason;
- for `human`, the Intent block as the confirmer saw it (drafted lines marked
  `(drafted)`), then `intent confirmed by <model> at <confidence>%` or
  `intent not confirmed (<confidence>%): <crux>`. For an unconfirmed card that
  verdict is the failed check, so it is not written twice.
