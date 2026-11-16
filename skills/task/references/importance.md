# Importance — how bad the problem is, judged on the ticket alone

This file owns the **importance rubric**: five levels that say how bad a
ticket's problem is for a user or the business while it stays open, the inputs
a judge may use to pick one, and when the verdict is too uncertain to write.
Importance is an objective property of the ticket, like severity. A bug that
gives people bad financial advice is important whatever else is in the backlog;
a slightly misaligned button is not. It is **draft pending PM / EM review**:
no command writes a verdict from it until the sign-off below is filled in.

| Sign-off | Name | Date |
| -------- | ---- | ---- |
| PM       |      |      |
| EM       |      |      |

## Levels

Pick the level from the **consequence** the ticket describes, not from its
wording, its reporter, or its neighbours. Read the table top-down and stop at
the first row whose test the ticket meets.

| Level      | Test                                                                                                                                       | Examples                                                                                                                                                                                |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `blocker`  | Wrong money movement, wrong advice or tax outcome, data loss or exposure, or a compliance breach — **happening now**, with evidence it is. | A transfer posts twice for every user who retries it. Projections shown today use last year's contribution limit. A support export includes other users' account numbers.               |
| `critical` | The same consequences, but **conditional or rare**; or a core user flow **broken with no workaround**.                                     | A tax estimate is wrong only for filers with two states of residence. A rounding edge case could misstate a balance above some threshold. Users cannot link an external account at all. |
| `major`    | A user flow **degraded, or broken with a workaround**; or a material, recurring internal-ops cost. No wrong money, advice or data.         | A goal chart fails to load until the page is refreshed. Editing a plan loses unsaved inputs on back-navigation. Support re-keys every rejected form by hand each week.                  |
| `minor`    | **User-observable but harmless**: cosmetic, copy, alignment; or internal hygiene with no user effect.                                      | A label is misaligned on narrow screens. A tooltip has a typo. A deprecated helper still has three callers.                                                                             |
| `trivial`  | **No observable effect** on users or operations; a nice-to-have.                                                                           | Rename a private variable. Add a convenience flag nobody has asked for.                                                                                                                 |

The boundaries between adjacent levels, stated once:

- `blocker` / `critical` — **happening now vs conditional**. Both carry the
  worst consequences; only evidence of current harm makes it `blocker`.
- `critical` / `major` — **wrong outcome or no workaround vs a workaround
  exists**. Any wrong money, advice, tax or data outcome is at least
  `critical`, however rare.
- `major` / `minor` — **a task is harder or fails vs it only looks wrong**.
  Something a user or operator cannot do, or does slower, is `major`.
- `minor` / `trivial` — **observable vs not**. If nobody outside the code can
  see the difference, it is `trivial`.

### Never `blocker` without evidence of current harm

The judge sets `blocker` only when the ticket, or the code path the validity
check found, shows the harm occurring now: affected users, a live reproduction,
an incident. Absent that evidence, a `blocker`-shaped consequence is
`critical`, at the confidence the rest of the ticket supports. This mirrors
"Never auto-set `urgent`" in `commands/handlers/task-fill.md` → **Priority — a
static default, and a symbolic value**: the top of the scale is an escalation,
and an escalation needs a signal the judge can point to.

## Importance is not priority, and not `impact`

- **Priority orders work.** It is relative: it trades one piece of work against
  another, and that trade belongs to the PM and EM, who weigh it against
  project feature work. Its rules — the static `medium` default, never
  auto-setting `urgent`, the `urgent` / `high` / `medium` / `low` / `none`
  vocabulary — are in `commands/handlers/task-fill.md` → **Priority — a static
  default, and a symbolic value**, and each tracker's encoding is that file's
  **Adapters** table. `skills/task/SKILL.md` → **Ranking** is how priority is
  used.
- **`impact` is how much finishing the work is worth.** It is the Fibonacci
  value estimate in the `impact` row of `skills/task/SKILL.md` → **Field
  reference**, the numerator of the ranking score, with no rubric behind it.
- **Importance is how bad the problem is while it stays open.** It judges
  consequence and orders nothing. A rubric that ranked tickets against each
  other or against quarter goals would judge a ticket by its neighbours, which
  is what this one exists to avoid.

Priority's vocabulary has four non-empty levels; importance needs five to
separate "happening now" from "conditional", so it has its own scale. On Jira,
a consumer writes it to the priority field one-to-one onto the site's `P0`–`P4`
names through task-fill's `priority_map`; this file does not define that map.
How a Jira priority name reads back as a symbolic level is the Priority row of
the field mapping in `commands/handlers/jira.md` → **List**.

## Inputs

The judge may use:

- **The ticket text** — title, description, comments, attachments: the
  consequence is stated there or nowhere.
- **The code path the validity check found** — it shows whether the described
  harm can occur and how many paths reach it.
- **The parent epic's stated purpose** — it says what the affected flow is for,
  which can settle whether it is core.

The judge may not use:

- **Other tickets' importance** — that makes the verdict relative, which is
  priority.
- **Sprint or quarter plans** — they say when work is wanted, not how bad the
  problem is.
- **How urgent the reporter sounds** — tone is not evidence of consequence.

## Confidence

Every verdict carries `confidence: high | low`. It is `low` when either holds:

- the consequence depends on facts the ticket does not state (who is affected,
  whether money or advice is touched, whether a workaround exists), or
- two levels are equally defensible from what the ticket does state.

Otherwise it is `high`. A `low` verdict is not written to the tracker; the
ticket goes to a human with the verdict as a suggestion.

## Output

One record per ticket. The consumer owns the full schema.

```yaml
importance: critical
confidence: high
evidence: "Tax estimate is wrong for filers with two states; no report of it occurring."
```

`evidence` is one line naming the consequence and, for `blocker`, the sign of
current harm.
