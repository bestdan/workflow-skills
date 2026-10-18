# How this walk is paced

Read this before Step 1. It governs every step of the walk — `SKILL.md` carries
the steps, this file carries the rules for how to deliver them.

**One step per turn. Every step ends by handing the terminal back.** The
learner is here to watch a mechanism work, and a mechanism they scrolled past
is one they did not see. Running three steps in one turn buries the failure in
Step 4 under the fix in Step 5, which is exactly the pair the walk exists to
separate.

So at the end of every step, before touching the next one:

1. **Recap in two or three lines** — what the command did, what changed on
   disk, and the one rule that step was there to teach. Not a summary of the
   step's prose; the specific thing that just happened in their terminal.
2. **Stop and ask.** A bare, one-keystroke prompt: _"Questions on that, or
   press on? (Enter to continue)"_. Use plain prose, not `AskUserQuestion` —
   the point is room for an arbitrary question, not a menu. (Step 1's setup
   choice and Step 8's hand-off are bounded menus, not open questions, so
   those two keep `AskUserQuestion` as written.)
3. **Wait.** Do not run the next step's commands in the same turn. There is no
   step so small it can be bundled with its neighbour.

When they do ask something, answer it from what is already on their screen —
the output of the commands run so far. If the honest answer is "Step 6 shows
you", say so and offer to skip ahead rather than previewing it in prose; a
spoiled surprise costs more here than a deferred one. If an answer runs long,
answer it and re-offer the same continue prompt rather than sliding into the
next step on momentum.

If the learner asks to stop early, at any step: go straight to Step 8 and
delete the tree. A half-finished walk still leaves a directory behind, and
abandoning it is the one outcome this skill promised wouldn't happen.

## Say what you are about to do, before you do it

Each step opens with a sentence of intent in plain language — _what_ this
command is about to do and _why the walk needs it now_ — before the command
runs. The learner should never watch a command execute without knowing what
it was for. Same on the way out: the recap says what actually happened, which
is not always what you intended (Step 5's stale-ledger failure is the case in
point).

## Terms of art: define at first use, every one of them

This instrument runs on a small private vocabulary, and every one of these
words also has a loose everyday meaning that will quietly mislead. Whenever
one appears for the first time, define it in one sentence, inline, before
using it as though it were shared. Each step's checkpoint in `SKILL.md` names
which terms land there.

| Term                   | Say something like                                                                                                             |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| **spike**              | A bounded stretch of investigation that exists to unblock a decision, not to ship a feature.                                   |
| **track**              | One thread of related questions inside a project — `auth` here — with its own ledger.                                          |
| **question record**    | A filed, addressable question with a status, not a note; the unit the ledger counts.                                           |
| **decision**           | The thing the spike exists to unblock; questions `blocks:` it, and it stays `pending` until they clear.                        |
| **obligation**         | Work an _answer_ created — a debt the answer incurred — as distinct from a question, which is something not yet known.         |
| **discharged**         | An obligation that was actually done, as opposed to closed, deferred, or routed somewhere.                                     |
| **stub card**          | A real file standing in for work not done yet, so a deferral points at something on disk instead of at prose.                  |
| **`superseded_when:`** | The stated condition under which a stub is allowed to be deleted — required so stubs can't accumulate silently forever.        |
| **destination**        | A filesystem path an obligation is routed to, which `validate` checks exists; naming one does not create one.                  |
| **the ledger**         | The counts stored _in_ the markdown, written by `write-ledger` — stored, not computed live, which is why it can go stale.      |
| **stale ledger**       | Stored counts that no longer match what the tree actually contains; an error, and the mechanism catching its own contract.     |
| **retired**            | A question withdrawn as no longer worth answering — closed without an answer, tracked separately so it can't pose as progress. |
| **convergence**        | Whether the spike is actually approaching a decision, measured by what's left blocking it — not by how many questions closed.  |

Two of these do most of the work and are worth a beat longer than one line:
**obligation** (Step 4, where the first one appears) and **discharged versus
open** (Step 7, where the divergence lands). If the learner already knows the
vocabulary, they'll say so — take them at their word and stop defining.

`obligation` needs one extra piece of care: the word has already gone by twice
before Step 4 — in the spine you open with, and in Step 2's validator warning
— so Step 2's checkpoint names it as a term of art and says its definition is
coming, rather than either defining it early (which spends the tease) or
letting it pass as ordinary English.
