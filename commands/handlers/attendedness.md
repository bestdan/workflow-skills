# Attendedness — is a human present to answer right now?

Read by the four pre-claim WIP gates (`repo-pr-execute.md`, `gh-issue-claim.md`,
`jira-claim.md`, and `commands/do-tasks.md` for `linear`) and by
`linear-common.md` "Resolve claim scope". It defines **one** notion of "a human is
watching this run", and the mechanism that keeps a wrong answer cheap.

A run is **attended** when a human is present to answer a question **right now** —
operationally, when an `AskUserQuestion` prompt can be answered. Presence hours ago,
when someone typed `/loop`, is not presence now.

## Do not classify the run. Ask, and let the answer decide.

This is the whole design, and it is worth stating before the checklist below,
because the checklist is the part that looks load-bearing and isn't.

The tempting shape is: decide whether the run is attended, then skip the gate if it
is. **Do not build it that way.** "A human typed this" is not observable — a
scheduled routine, a `claude -p` cron, and an SDK-driven harness all deliver an
ordinary user message with no marker distinguishing it from a person at a keyboard.
A model that guesses "attended" in one of those runs removes the only bound on an
autonomous loop, and nobody sees the result until morning.

So the gate never decides. It **asks**, and the environment answers:

- A human present answers in one keystroke, and the claim proceeds.
- With nobody there, no answer arrives — and the gate holds.

Guessing wrong now costs one unanswered prompt instead of an unbounded claim. This
is why the override is an `AskUserQuestion` and **not** a line of prose inviting the
user to say "go ahead": prose that goes unanswered does not block, so an unattended
run sails straight past it. The prompt is a **mechanism**, not a courtesy — it is the
detector. Do not "simplify" it into a printed question.

## The gate

Every claiming run computes its in-flight count. The count is never skipped.

1. **Under the limit** → claim. No prompt, no note, no ceremony. This is the common
   case and it must stay silent.
2. **At or over the limit, and the action is a batch** — `--all`, `-n N`, a
   `--claim-only` batch, or any dispatch of remote sessions → **decline or bound the
   batch exactly as that handler already specifies. Never prompt.** Reserving many
   cards, or dispatching N sessions, is a throughput decision either way: a human
   present at dispatch is not present for the N pull requests that land later. This
   is the one place attendedness is deliberately ignored.
3. **At or over the limit, single action** (a single execution, or a single
   `--claim-only`):
   - If any **hard negative** below holds, decline as the handler specifies. Do not
     prompt.
   - Otherwise ask **once**, via `AskUserQuestion`: header `WIP limit`, question
     `<count> of your issues are in flight (limit <wip_limit>). Claim anyway?`,
     options **Claim anyway** and **Stop**.
     - **Claim anyway** → proceed, and say the gate was overridden in the run report.
     - **Stop**, no answer, an unavailable tool, or a failed call → decline with the
       handler's normal message. **Never retry the prompt** and never infer an
       answer from silence other than "stop".

## Hard negatives — a cheap pre-check, not the guarantee

Skip straight to declining, without attempting the prompt, when any of these holds:

- The caller passed `--non-interactive`.
- `$AUTO_PILOT_UNTIL` is set in the environment (an `/auto-pilot` orchestrator run —
  `scripts/spawn-orchestrator.sh` exports it into the job).
- The run is executing as a **subagent**.
- This turn was fired by a `/loop` wakeup (including the literal
  `<<autonomous-loop-dynamic>>` sentinel) or by a scheduled routine, rather than
  typed by a person.

**This list is an optimization and a belt, not the safety property.** Two
consequences follow, and both matter:

- A **missing** entry is cheap. An unattended entry point nobody thought of attempts
  the prompt, gets no answer, and declines — the correct outcome by a slower route.
  The list exists to avoid a pointless prompt in a run already known to be
  unattended, not to be exhaustive. Adding to it is a tidy-up; forgetting to is not a
  vulnerability.
- The last entry is **not reliably observable**, and that is tolerated for exactly
  the reason above. A scheduled routine's prompt is indistinguishable from a typed
  one, so treat this entry as best-effort and let the unanswered prompt be the
  backstop. Do not add heuristics that try to sharpen it — a guess that a real
  session is a routine declines work a present human asked for, which is the failure
  the whole design is arranged to avoid.

Never widen this list into "and anything else that feels automated." An open-ended
entry hands the decision back to model judgment at precisely the point where the
mechanism above was chosen to take it away.
