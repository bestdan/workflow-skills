# Design — the obligation ledger for research spikes

Status: proposed. Extracted from a working implementation in `aiutopilot`
(PR #25), which is where every measurement below comes from.
Date: 2026-08-01

## Problem

A research spike is a repo whose job, for a while, is answering load-bearing
questions before building the thing. `aiutopilot` is one: a control plane whose
design is gated on questions like "must the baseline stop contain a
process-group escapee" and "what does a trip do when it cannot get the registry
lock". Each is answered on measured evidence, written down, and closed.

**Four days into that repo the maintainer asked whether the project was
fundamentally infeasible.** The numbers said otherwise:

|                                      |                                  |
| ------------------------------------ | -------------------------------- |
| Questions inherited at repo creation | 6                                |
| Questions filed since                | 6                                |
| Questions answered since             | 7                                |
| Questions open at the time of asking | 4, of which **one** was blocking |

The question count was flat-to-down and converging. Nothing was circling. So the
feeling was not tracking the questions — it was tracking something nobody was
counting:

| Source                                             | Discharged | Open                  |
| -------------------------------------------------- | ---------- | --------------------- |
| One answer's named obligations                     | 1          | 6                     |
| One answer's deferred component                    | 3          | 11                    |
| One answer's deferred contract work                | 0          | 4                     |
| An audit's owed contracts                          | 2          | 2                     |
| A deployment precondition stated in a contract doc | —          | **on no list at all** |

**Roughly 6 discharged against 23 open.** Answering a question in a spike does
not mostly create new questions. It creates _obligations_ — and obligations were
invisible, so progress was measured with the one instrument that looked healthy.

### The mechanism, which is narrower than "we deferred too much"

Six deferrals were made in those four days. Three stayed visible and three went
dark, and the discriminator was not importance, volume, or author:

| Deferral                                         | Destination                        | Outcome     |
| ------------------------------------------------ | ---------------------------------- | ----------- |
| An answer's seven obligations                    | seven task cards that were created | **visible** |
| An answer's new component                        | a plan folder that was created     | **visible** |
| Four items deferred "to the watcher"             | prose inside the answer            | dark        |
| A test deferred "to the Stage-2 gate"            | a _stage_ — no file                | dark        |
| Two measurements deferred "to the account track" | **a track that did not exist**     | dark        |
| A precondition in a contract doc                 | a heading                          | dark        |

**A deferral stayed visible exactly when its destination was a file that already
existed.** "Defer to the watcher" was fine once the watcher had a plan folder.
"Defer to the account track" was invisible because naming a track does not create
one — the sentence reads as though it routed the work somewhere, and it routed it
to a string.

Note the second and third columns of that last row. Contract documents state
preconditions constantly ("this component must not be deployed on a shared
host"), and a contract document is not a backlog. That is a _second_ class of
hidden work, and it needs the same address discipline as the first.

### Why "defer less" is the wrong lesson

Every one of those deferrals was correct. Building the account before knowing
what it had to satisfy would have meant provisioning it, then discovering from
three separate answers that it needed an isolated uid domain, particular sudoers
entries, and a keychain invariant — and rebuilding it three times. The defect was
never the deferral. It was that deferred work had no address, so it accrued
silently and surfaced as a mood rather than as a number.

## What the instrument is

Three parts that must ship together, plus one that must deliberately stay out.

### 1. The record

A fenced block emitted **next to the prose that creates the deferral**, not in a
central registry — locality is what stops it drifting from the sentence it
describes:

````markdown
```obligation
id: ceiling-receipt
owes: the receipt, its durability contract, the promotion pass, and the trip_id payload
destination: dev_docs/tasks/deferred_obligations/deferred_ceiling_receipt.md
status: open
```
````

- `id` — kebab-case, unique across the whole tree.
- `owes` — one line, in the author's own words.
- `destination` — **repo-relative path that must exist.** This is the entire
  point. A deferral to a path that is not there fails the check.
- `status` — `open` | `discharged`.
- `discharged_by` — required iff `discharged`, forbidden while `open`.

### 2. The coverage rule — the half that matters more

Records alone only catch _malformed_ deferrals. They cannot catch the deferral
nobody registered, which is the actual failure mode. The naive fix is to grep for
deferral vocabulary ("deferred to", "gated on", "belongs to"). **That question is
unanswerable over English prose.**

The structural fix asks a different question. Every question section in the
answers file must declare — including declaring, with a reason, that it owes
nothing:

````markdown
```obligation
none: option (A) adds no observation and owes no tooling
```
````

"Did you use a deferral word?" is a heuristic. "Is the field present?" is
mechanical. **Forgetting stops being available; only deliberate silence
remains** — and deliberate silence is a thing a reviewer can see and challenge.

### 3. The ledger

Four numbers, derived from the records and **stored** at the top of the answers
file, with the gate verifying they are not stale:

```markdown
- **Questions:** 8 answered, 4 open
- **Obligations:** 3 discharged, 10 open
```

Stored rather than computed on demand, because a number you must remember to run
reproduces the original failure — invisible unless someone thinks to look. **The
divergence between the two pairs is the health signal.** Questions converging
while obligations climb is exactly the state that felt like futility, and it is
legible in one line.

### 4. What is deliberately _not_ in the gate

The lexical scan survives as an advisory `--suggest` mode that **cannot fail a
run**. This is not fastidiousness; in `aiutopilot` it was forced by that repo's
check contract, which has no baseline file, no allowlist, and no skip flag. A
check that matches English has false positives, and a false positive in such a
gate has nowhere legal to go.

**Measured, not assumed:** run against that tree, `--suggest` returned **29
hits**, most of them prose describing behaviour rather than deferring work — e.g.
"the stop is never gated on the lock". As a gate step it would have been noise
with no permitted place to record exceptions.

> **Generalization point.** If the adopting repo _does_ have an advisory tier,
> this constraint relaxes and `--suggest` can run in CI as a non-failing report.
> The rule to carry across is not "keep it out of CI" but **"a check whose false
> positives have nowhere to go must not be able to fail the build."**

## How it works mechanically

One script, no dependencies beyond the standard library.

| Invocation          | Behaviour                                                 |
| ------------------- | --------------------------------------------------------- |
| `ap-obligations.py` | validate; exit `0` clean, `1` violations, `2` usage error |
| `--ledger`          | print the derived ledger                                  |
| `--write-ledger`    | rewrite the stored ledger in place                        |
| `--suggest`         | advisory scan; always exits `0`                           |
| `--root <dir>`      | scan a different tree — what makes it testable            |

Validation, in one pass over every `*.md` under the tree:

1. Blocks parse as `key: value`; unknown keys are an error, not ignored (a
   `desination:` typo must not silently drop the constraint).
2. `id` is kebab-case and globally unique.
3. `status` is in the enum; `discharged_by` present iff discharged.
4. `destination` is repo-relative and **resolves to an existing path**.
5. Every question section in the coverage file declares something.
6. The stored ledger matches the derived one.

Two implementation notes that cost time to find:

- **The generated ledger must survive the repo's formatter.** If `dprint`
  (or prettier) rewrites the generated block, the freshness check and the
  formatter fight forever. Use constructs the formatter leaves alone — a bullet
  list rather than an aligned table — and verify it explicitly.
- **The check will catch its own staleness during development.** That is correct
  behaviour and should be left in place; it is the thing that keeps the number
  honest when someone adds a record and forgets to regenerate.

## Setting it up in a repo

1. **Drop in the script.** Set the coverage file to wherever answers live.
2. **Add the ledger markers** to that file and run `--write-ledger`.
3. **Backfill every deferral already made.** This is the payload; the mechanism
   is worthless empty. **Expect the backfill to be the moment of truth** — in
   `aiutopilot` it produced 13 records, and **4 of them had no destination at
   all**, requiring stub cards to be created before they could be registered.
   Needing stubs to complete a backfill is the finding, not an inconvenience.
4. **Create a stub folder** (`dev_docs/tasks/deferred_obligations/`) for
   obligations with no owner yet. A stub is a real card carrying the context, and
   it should say plainly when it expects to be **superseded** — one stub in the
   reference implementation exists only until two real cards appear, and says so.
5. **Wire it into the check** and document it in the check contract, including
   the reasoning for what was left out.

### Sequencing, learned the hard way

The mechanism was first built stacked behind the very plan it was auditing —
which had it describing only work already done, the exact posture it exists to
criticise. **Land the instrument first, then have live work register its own
deferrals.** A plan should register its own obligations rather than be annotated
retroactively by someone else's PR.

The restructure was cheap and self-verifying: only 2 of 13 records depended on
the other branch, and the check named both immediately on rebase.

## Test surface

19 hermetic tests in the reference implementation, all against fixture trees
under the test's own `mktemp -d` and passed via `--root`, so nothing reads the
real repository. That matters more than usual here: the central assertion is
"this path exists", and a test scanning the real tree would pass or fail on
whatever happens to be checked in.

Classes worth keeping in any port:

- a nonexistent destination fails, **and the message says why it matters**;
- a section declaring nothing fails the coverage rule;
- an explicit `none` satisfies coverage; a `none` without a reason fails; a
  `none` carrying other fields fails;
- duplicate ids fail **across files**;
- `discharged` without `discharged_by` fails, and `discharged_by` while `open`
  fails;
- an absolute destination fails;
- an unknown field fails rather than being ignored;
- a stale ledger fails and `--write-ledger` repairs it;
- `--suggest` reports unregistered prose, stays quiet next to a record, and
  **never returns non-zero**.

## What is repo-specific, and must be parameterized to become a skill

The reference implementation hard-codes decisions that were right for one repo:

| Hard-coded                                           | Why it was right there                | What a skill needs                                              |
| ---------------------------------------------------- | ------------------------------------- | --------------------------------------------------------------- |
| `docs/open-questions.md` as the single coverage file | that is where deferrals are _created_ | config: one or more coverage files, or a per-file opt-in marker |
| `^### (Q\d+)\.` as the section pattern               | that repo's heading convention        | config: the heading regex                                       |
| Coverage rule reaches only the answers file          | plan cards declaring would be noise   | keep the default, but make the scope explicit and overridable   |
| The `--suggest` phrase list                          | that repo's idiom                     | config, and treat the list as a starting point                  |
| Markdown + `dprint`                                  | house format                          | detect the formatter, verify generated-block stability          |
| Green-or-broken gate                                 | that repo's contract                  | detect whether an advisory tier exists                          |

## How this becomes a skill

Proposed surface, in the order a user meets it:

- **`init`** — detect the answers file and heading convention, install the
  script, add ledger markers, write the check-contract paragraph.
- **`backfill`** — walk the answers file section by section _with the user_,
  proposing a record or a `none` for each. It must **not** invent destinations:
  where none exists it offers to create a stub and says so out loud, because the
  count of required stubs is the diagnostic.
- **`validate`** — the gate command.
- **`ledger`** — print or rewrite.
- **`suggest`** — advisory, never failing.

Things the skill must **not** do, each corresponding to a way this becomes
theatre:

- silently create a destination to make a record resolve;
- auto-repair a stale ledger during validation (rewrite must be an explicit act,
  or the number stops being checked and starts being generated);
- widen coverage to every markdown file, which turns the discipline into noise
  and trains people to satisfy it mechanically;
- report the obligation count as a progress metric on its own — see below.

## The discipline the tool cannot supply

- **Register at deferral time, not at review time.** The whole cost is one minute
  and one stub. The alternative cost, measured, was four days of invisible
  accrual and a maintainer asking whether the project was viable.
- **Count what you are actually spending.** Questions-answered is the flattering
  metric; obligations-open is the predictive one. Report both or neither.
- **Be honest about granularity.** In the reference implementation the ledger
  read _10 open_ while a hand count of the underlying task cards was closer to
  23, because records were registered at the altitude the deferral was made (one
  answer → one component, not fourteen cards). Both numbers are true and they
  measure different things. Do not let the smaller one be read as progress.
- **A stub that never gets superseded is a new place for work to hide.** Stubs
  should carry the condition of their own deletion.

## Known limits, stated rather than hedged

- **It cannot see a deferral that was never registered.** Prose can always
  describe work going somewhere without emitting a record. The coverage rule
  closes this in the one file where deferrals are created; `--suggest` gestures
  at the rest; neither closes it in general.
- **Coverage is one file.** A precondition stated in a contract doc — the class
  that hid the worst offender in the origin repo — is registered voluntarily.
- **Ids are a global flat namespace**, which is fine at tens and would need
  scoping at hundreds.
- **The ledger counts registered obligations, not tasks.** It is a measure of
  declared debt, not of remaining work.

## Provenance

Origin repo: `aiutopilot`. The instrument is PR #25; the plan that first adopted
it is PR #23. The independent corroboration is PR #24, in which another agent hit
the same failure mode during an unrelated review and solved it by hand, writing:
_"The real risk was never that task 6 omitted the alert; it was that no card
recorded the debt."_ That sentence is this design's thesis, arrived at
independently and without the mechanism — which is the best available evidence
that the failure mode is structural rather than personal.
