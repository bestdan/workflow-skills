# Importance — how bad the problem is, judged on the ticket alone

This file owns the **importance rubric**: five levels that say how bad a
ticket's problem is for a user or the business while it stays open, the inputs
a judge may use to pick one, and when the verdict is too uncertain to write.
Importance is an objective property of the ticket, like severity. A bug that
silently corrupts users' saved data is important whatever else is in the
backlog; a slightly misaligned button is not. It is **draft pending PM / EM review**:
no command writes a verdict from it until the sign-off below is filled in.

| Sign-off | Name | Date |
| -------- | ---- | ---- |
| PM       |      |      |
| EM       |      |      |

## Levels

Pick the level from the **consequence** the ticket describes, not from its
wording, its reporter, or its neighbours. Read the table top-down and stop at
the first row whose test the ticket meets.

The top two levels are set by four **harm classes**:

- **Wrong financial outcome** — a user or the business is charged, paid,
  credited or told a figure that is wrong.
- **Irreversible user harm** — an action taken for or on a user that cannot
  be undone.
- **Data loss or exposure** — data destroyed, corrupted, or shown to someone
  who should not see it.
- **Legal or compliance breach** — the product breaks a law, regulation or
  contractual obligation.

A repo can name what these mean in its own domain with an overlay (see
**Repo overlay** below); the classes and the levels stay as they are here.

| Level      | Test                                                                                                                                                                      | Examples                                                                                                                                                                             |
| ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `blocker`  | A harm-class consequence — **happening now**, with evidence it is.                                                                                                        | Every user who retries checkout is charged twice. A nightly job has deleted records it should keep since its last deploy. A shared export includes other customers' email addresses. |
| `critical` | The same consequences, but **conditional or rare**; or a core user flow **broken with no workaround**.                                                                    | Invoice totals are wrong only for orders that mix two currencies. A race under concurrent edits could drop a saved change. Users cannot sign in through single sign-on at all.       |
| `major`    | A user flow **degraded**, a non-core flow **broken**, or a core flow **broken with a workaround**; or a material, recurring internal-ops cost. No harm-class consequence. | A dashboard fails to load until the page is refreshed. Editing a form loses unsaved inputs on back-navigation. Support re-keys every rejected import by hand each week.              |
| `minor`    | **User-observable but harmless**: cosmetic, copy, alignment; or internal hygiene with no user effect.                                                                     | A label is misaligned on narrow screens. A tooltip has a typo. A deprecated helper still has three callers.                                                                          |
| `trivial`  | **No observable effect** on users or operations; a nice-to-have.                                                                                                          | Rename a private variable. Add a convenience flag nobody has asked for.                                                                                                              |

The boundaries between adjacent levels, stated once:

- `blocker` / `critical` — **happening now vs conditional**. Both carry the
  worst consequences; only evidence of current harm makes it `blocker`.
- `critical` / `major` — **wrong outcome or no workaround vs a workaround
  exists**. Any harm-class consequence is at least `critical`, however
  rare: rarity never lowers a harm-class consequence confirmed to affect even
  one user.
- **Reported by one user is not confirmed to affect one user.** A single
  report nobody has reproduced leaves reach unknown, and may be the first sign
  of a wider problem. Judge it as if the problem is real and widespread, never
  lower for having one reporter; whether it is real at all is validity's call
  (`unsure`, `reproduce across users`, in `validity.md` rung 3).
- `major` / `minor` — **a task is harder or fails vs it only looks wrong**.
  Something a user or operator cannot do, or does slower, is `major`.
- `minor` / `trivial` — **observable, or a real cost to the people maintaining
  the code, vs neither**. If nobody sees the difference and it costs no one
  anything, it is `trivial`.

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
- **Business value is not importance.** Opportunity size, revenue and roadmap
  fit are judged outside the ticket; the gardener cannot know them and does
  not guess them.
- **Importance is how bad the problem is while it stays open.** It judges
  consequence and orders nothing. A rubric that ranked tickets against each
  other or against quarter goals would judge a ticket by its neighbours, which
  is what this one exists to avoid.

Priority's vocabulary has four non-empty levels; importance needs five to
separate "happening now" from "conditional", so it has its own scale. A
consumer writes it to the tracker's priority field (on Jira, one-to-one onto
the site's `P0`–`P4` names through task-fill's `priority_map`, which this file
does not define), but never over a human-set priority
(`commands/garden-backlog.md` → **What may be written**): a PM's priority call
stands, and the gardener only records its disagreement for a human.
How a Jira priority name reads back as a symbolic level is the Priority row of
the field mapping in `commands/handlers/jira.md` → **List**.

## Repo overlay

A repo whose harms are more specific than the four classes can name them in an
overlay: a markdown file, in the repo or outside it, at the path the optional
`garden.importance_overlay` key in `.task-config.yml` gives
(`commands/task-config.md`). `/garden-backlog` reads this file, then the
overlay. With the key unset, this file is the whole rubric.

The overlay holds two things and nothing else:

- **Critical harms** — what each harm class means in the repo's domain, and
  any domain harm that counts as one of them.
- **Examples** — per level, cases from the repo's own product.

It does not restate or change the levels, their tests and boundaries, the
evidence rule for `blocker`, the inputs, confidence or output. Where an overlay
appears to, this file wins. A harm the overlay names is a harm-class
consequence for every rule above.

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
- **Business value** — what fixing it is worth, which is priority's and
  `impact`'s question, not this one.

## Confidence

Every verdict carries `confidence: high | low`. It is `low` when any holds:

- the consequence depends on facts the ticket does not state (who is affected,
  whether a harm class is touched, whether a workaround exists), or
- two levels are equally defensible from what the ticket does state, or
- the consequence is in a harm class, but the ticket does not settle whether
  the error is big enough to do real harm: how large the wrong figure is, and
  whether it would lead a user to a materially harmful action. The level
  stands, at least `critical`; only the confidence drops. An estimate a little
  off in the direction that only makes a user more cautious is not the harm of
  one that leads them past a hard limit, and a ticket that does not say which
  goes to a human.

Otherwise it is `high`. A `low` verdict is not written to the tracker; the
ticket goes to a human with the verdict as a suggestion.

## Output

One record per ticket. The consumer owns the full schema.

```yaml
importance: critical
confidence: high
evidence: "Invoice total is wrong for mixed-currency orders; no report of it occurring."
```

`evidence` is one line naming the consequence and, for `blocker`, the sign of
current harm.
