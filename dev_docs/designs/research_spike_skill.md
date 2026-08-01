# Design — the obligation ledger for research spikes

Status: designed. The ledger mechanism is extracted from a working
implementation in `aiutopilot` (PR #25), which is where every measurement
below comes from; the skill design ("How this becomes a skill" onward) was
settled 2026-08-01 and extends it to long-running, multi-track projects.
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

The design below resolves the first three rows **structurally rather than by
config**: the skill owns the directory convention, so coverage files, heading
convention, and coverage scope are fixed by `init` instead of configured per
repo. Only the formatter check and the advisory-tier detection remain
environmental.

## How this becomes a skill

Design settled 2026-08-01. The scope is wider than the reference
implementation: not just the obligation ledger, but **research-spike
management for long-running, big projects** — the ledger is one instrument
inside it. The settled constraints:

| Question            | Decision                                                                |
| ------------------- | ----------------------------------------------------------------------- |
| Lifecycle ownership | Full — the skill owns the question format, filing, and convergence      |
| Structure           | Track directories with a project roll-up; multiple concurrent projects  |
| Composition         | Sibling to plan-with-docs / task loop, with explicit bridges            |
| Agent dispatch      | Structure now, dispatch later — tracks are agent-sized context bundles  |
| Convergence         | Decisions-gated: questions and obligations name the decision they block |
| Reconciliation      | Script-driven, always — the LLM never computes a status or a count      |

### On-disk structure

Multiple research projects coexist under one root; each is self-contained:

```
dev_docs/research/<project>/
  PROJECT.md            # charter: what's being built, links to tracks
  decisions.md          # the decisions this spike exists to unblock
  LEDGER.md             # generated roll-up (organizer-owned)
  tracks/<track>/
    questions.md        # the track's questions + answers + track ledger
    contracts/          # optional: contract docs whose preconditions register here
    obligations/        # stub and receipt cards (see record formats)
```

Ids scope as `project/track/id` (`project/id` for decisions), so two projects
can each have an `account` track without collision. **Cross-project
destinations are forbidden in v1**: a self-contained project must be able to
see every inbound dependency in its own ledger, and a destination pointing
into a foreign tree gives the receiving project no inbound view. Work that an
answer creates for another project is filed in that project (a question or a
stub card there), and the local obligation's destination points at a receipt
card recording the handoff.

The structural payoff is compartmentalization: an agent told to "work track X"
needs `tracks/X/` plus `decisions.md` and nothing else. That contract is stated
in the SKILL.md so a future `/work-track` dispatcher (or auto-pilot
integration) composes without redesign.

### Record formats and grammar

The parser stays at reference-implementation simplicity: one `key: value` per
line, unknown keys are errors, **no inline comments** (a `#` is part of the
value and will fail the enum check — the reference parser has no comment
syntax and gains none). List values are comma-separated. Declared `id`s are
always bare; the script derives the qualified form (`project/track/id`,
`project/id` for decisions) for reports and uniqueness checks. In-record
references (`blocks:`, `blocking:`) name decisions by bare id, resolved within
the enclosing project — with cross-project destinations forbidden, no
reference ever needs qualifying.

**Questions** get structure — this is the full-lifecycle choice. Each question
is a section in `questions.md` (recognized by the `### Q<n>.` heading
convention `init` installs; a section ends at the next same-level heading)
with a fenced header block:

````markdown
### Q3. Must the baseline stop contain a process-group escapee?

```question
id: baseline-stop-escapee
status: open
blocks: stop-semantics
```
````

- `status` is `open | answered | retired`; `blocks` is one or more decision
  ids, or `none: <reason>`.
- `retired` is for questions whose premise died; it requires
  `retired_because`, so questions leave the board without pretending to be
  answered.
- `blocks` is the convergence hook. As with obligation coverage, `none` must
  carry a reason — a question that gates nothing is worth noticing.
- `answered` requires **both** a recorded conclusion — an `answer:` one-line
  field on the block, with the evidence in the section prose — and the
  obligation coverage rule satisfied (a record or an explicit `none`), exactly
  as in the reference implementation. Coverage cannot be satisfied by prose
  alone, and `answered` cannot be reached without saying what the answer is.

**Decisions** (`decisions.md`) store **only the human lifecycle state**;
everything else is derived:

````markdown
```decision
id: stop-semantics
state: pending
```
````

- `state` is `pending | decided`. There is no stored `blocked`/`ready` — the
  script computes those from the blockers, so a stale stored status cannot
  disagree with the derived one (storing both was reviewed as a defect, not a
  convenience).
- A decision is **ready** when every question blocking it is answered or
  retired and every obligation marked `blocking: <decision-id>` is discharged.
- `pending → decided` is a human act, recorded with `decided_in:` — a pointer
  to **durable** decision evidence: an ADR, a permanent design doc, or a
  receipt card. Never a plan directory; `/push-plan` deletes plan directories
  after tracker migration, so that pointer would rot on first push.
- Filing a new blocker against a `decided` decision is a validation error
  unless the decision is explicitly reopened (`state: pending` plus
  `reopened_because:`).
- New decisions from track agents are filed as `state: proposed` blocks inside
  the agent's own `questions.md`, next to the question that needs them;
  promoting one into `decisions.md` is an organizer act. A `blocks:` reference
  to a proposed decision is valid, and the status report lists the decision as
  awaiting promotion.

**Obligations** are unchanged from the record format above, plus one optional
field: `blocking: <decision-id>` for the subset that gates a decision rather
than merely being owed. Most obligations should not carry it — scarcity is
what keeps convergence meaningful. Two field tightenings from review:

- `destination` must be a normalized repo-relative path to an **existing
  regular file** that resolves inside the repository — no `../` traversal, no
  symlink escape, and a bare directory no longer qualifies (a directory can
  exist while saying nothing about the work).
- `discharged_by` is free text naming the discharging change — typically a PR
  or commit reference. It is deliberately **not** path-checked; the discharging
  artifact usually lives outside the tree.

**Cards** under `tracks/<track>/obligations/` make stubs and handoffs
first-class instead of folklore. Each card file carries one block:

````markdown
```card
kind: stub
superseded_when: the account track files its two measurement cards
```
````

- `kind: stub` requires `superseded_when:` — the condition of the card's own
  deletion, mechanizing the discipline rule below.
- `kind: receipt` requires `url:` (plus optional `handler:` and `tracker_id:`)
  and records work handed to an external system — a tracker task from
  `/add-task`, a plan pushed by `/push-plan`. The obligation's `destination`
  points at the receipt file, so the path-must-exist invariant holds untouched
  while the URL lives in content the validator does not (and cannot, offline)
  verify.
- The script counts stubs and receipts by kind; both appear in the ledgers.

**Ledgers**: each track stores its own ledger at the top of its
`questions.md`, so parallel agents never contend on a shared file:

```markdown
- **Questions:** 8 answered, 4 open, 1 retired
- **Obligations:** 3 discharged, 10 open (2 blocking, 1 stub, 1 external)
```

The project `LEDGER.md` is the roll-up — the same lines per track, per-decision
blocker status, project totals. Only the organizer regenerates it; the
validator flags it stale rather than auto-fixing.

### The verb surface

Two layers with a hard boundary. **The script never edits prose; the LLM never
computes a status or a count.** Presenting these as one flat verb list was
reviewed as the most likely way to implement the wrong half.

**Script subcommands** (`research-spike.py`, stdlib-only, `--root`-testable —
deterministic, the only thing CI ever runs):

| Subcommand                | Behaviour                                                                                                                 |
| ------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `init <project>`          | Scaffold the project directory — charter stub, empty `decisions.md`, ledger markers. `--track <name>` adds a track later. |
| `validate`                | The gate; per-project, per-track (`--track`), or whole tree. `--strict` for the organizer flavor (below).                 |
| `ledger` / `write-ledger` | Derive or rewrite the stored ledgers. Explicit act, never auto-repair.                                                    |
| `status <project>`        | The convergence report — below.                                                                                           |
| `suggest`                 | Advisory lexical scan, never failing, unchanged.                                                                          |

**SKILL.md procedures** (judgment; the LLM writes the fenced blocks directly
into the markdown, then immediately runs `validate` — the validator, not the
procedure, is what guarantees the result):

| Procedure          | Behaviour                                                                                                                                                                                                                                              |
| ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `file`             | Add a question: prose, id, `blocks` (offering the existing decision list, or filing a `proposed` decision in the agent's own track). Naming a nonexistent decision fails validation — the "track that did not exist" bug, prevented for decisions too. |
| `answer`           | Walk a question to `answered`: record the `answer:` conclusion, then the coverage rule — obligations registered or `none` declared before the status flips.                                                                                            |
| `defer`            | Register an obligation next to prose, creating a stub card when no destination exists and saying so out loud — the stub count is the diagnostic.                                                                                                       |
| `backfill`         | Section-by-section interactive walk of an existing doc, as above — now also imports free-form questions into the structured format.                                                                                                                    |
| `promote-decision` | Organizer-only: move a track's `proposed` decision block into `decisions.md`.                                                                                                                                                                          |

### Convergence: the `status` report

`status` answers "what still blocks building?" — the question the maintainer
was really asking on day four. Derived entirely by the script, printed per
decision:

```
foo — decisions: 1 decided, 1 ready, 3 blocked

  stop-semantics        READY    awaiting decision
  account-provisioning  BLOCKED  by 2 questions, 1 obligation
    Q: account/uid-domain-isolation   open
    O: account/keychain-invariant     open   → tracks/account/obligations/keychain.md
  account-tooling       PROPOSED awaiting promotion (filed in tracks/account)

  account:  Q  4 answered /  3 open / 1 retired    O  2 discharged /  8 open (2 stubs)
  watcher:  Q 10 answered /  3 open / 1 retired    O  7 discharged /  9 open (1 external)
  total:    Q 14 answered /  6 open / 2 retired    O  9 discharged / 17 open (3 blocking)
```

Rules that keep the report trustworthy rather than decorative:

1. **Decision readiness is derived, never stored** — stated above, enforced by
   the record format itself (there is nothing to hand-edit). The three counts
   in the header are `decided`, `ready` (awaiting a human decision), and
   `blocked`, printed separately: "ready" is not "done", and the project gate
   is all required decisions **decided**, not ready.
2. **The divergence signal survives the roll-up.** The health metric — the
   _current_ divergence between the question pair and the obligation pair (the
   ledgers are snapshots; the script has no history and does not pretend to
   report a trend) — is printed per track and per project. Big projects are
   exactly where one sick track hides inside healthy totals, so totals never
   print without the per-track breakdown.
3. **Blocking obligations stay scarce.** `blocking:` must name an existing
   decision; the script warns — warn only, fixed threshold, deliberately not
   configurable — when more than a third of open obligations are blocking. If
   everything blocks, nothing converges and the flag has become emphasis.
4. **Retired questions count separately everywhere.** Retirement is legitimate
   scope reduction, but folding it into "answered" would let a project
   converge by giving up — and when a retirement is what removed a decision's
   last blocker, the report says so on that decision's line.

### Script/LLM split

If two runs over the same tree could disagree, it belongs to the script: all
parsing, every validation rule, decision-status computation, the `status`
report, ledger derivation and freshness, `suggest`. The gate in CI is only
ever the script. The LLM contributes judgment only: prose, proposed ids and
`none:` reasons, the interactive `file`/`answer`/`backfill` dialogues, and
deciding _whether_ something is a deferral at all. The agent never computes a
status or a count itself.

### Validation rules and parallel-agent safety

All reference-implementation rules carry over (block syntax, unknown-key
rejection, kebab-case scoped ids, destination-must-exist, coverage rule,
ledger freshness, `discharged_by` iff discharged). New rules:

1. **Referential integrity across record types.** `blocks:` and `blocking:`
   must name an existing (or proposed) decision; `decided_in:` /
   `retired_because` / `reopened_because` / `answer:` / `superseded_when:` /
   `url:` required by their statuses and kinds; a decision nothing references
   is a warning (dead decision); a new blocker naming a `decided` decision is
   an error absent an explicit reopen.
2. **Status consistency.** `answered` ⇒ `answer:` present and coverage
   satisfied; `decided` ⇒ zero open blockers at decision time and a durable
   `decided_in:`. Errors, not warnings — this is what makes hand-editing the
   files safe to allow. (Readiness needs no rule: it is derived, so it cannot
   be asserted wrongly.)
3. **Ledger freshness is scoped, and `LEDGER.md` has its own tier.** A track's
   stored ledger is checked against that track only, so a parallel agent fails
   validation only for its own forgotten `write-ledger` — another track's
   activity cannot fail it; track agents gate on `validate --track <theirs>`.
   `LEDGER.md` staleness is a **warning** in plain `validate` — otherwise every
   track PR would fail on a file it is forbidden to touch — and an **error**
   under `validate --strict`, which is the organizer's gate, run on merge to
   the mainline. Warning-only everywhere would let the roll-up rot; the strict
   tier is what keeps the number checked rather than decorative.
4. **Write-ownership by convention, checked by consequence.** Nothing stops an
   agent writing outside its track, but every cross-track effect it could
   cause — duplicate id, broken destination, stale foreign ledger — is caught
   by the organizer's project-wide `validate`. Convention in SKILL.md: track
   agents touch `tracks/<theirs>/` only (proposed decisions included —
   promotion into `decisions.md` is the organizer's act); `decisions.md` and
   `LEDGER.md` are organizer-owned. Concurrency within a track is one agent at
   a time; if two agents do collide there, the stored ledger block at the top
   of `questions.md` produces an ordinary git merge conflict, which is the
   intended detection — no hashing or locking machinery on top of what git
   already does.
5. **Contract docs are covered, not voluntary.** Every file under
   `tracks/*/contracts/` must contain at least one `obligation` block or a
   file-level `none: <reason>` block. Contract preconditions are the class
   that hid the worst offender in the origin repo, and unlike arbitrary prose
   this is a bounded, opt-in directory the skill itself owns — the mechanical
   rule is available here, so declining it would be building a labeled hiding
   place.

### Bridges (sibling, not extension)

The spike machinery stands alone and works in any repo. Where the
workflow-skills task loop is configured, two explicit bridges exist — and both
were redesigned in review, because their first drafts violated the
destination-must-exist invariant (tracker handlers return URLs, not paths; and
`/push-plan` deletes plan directories after migration, so a pointer at one
rots on first push). Both bridges now go through **receipt cards**:

- **`defer` → task loop.** When task-config is set up, `defer` offers
  "/add-task this instead of a stub"; the procedure writes a `kind: receipt`
  card carrying the handler, tracker id, and returned URL, and the obligation's
  destination points at that card. The invariant is untouched; the external
  reference lives in card content the validator never path-checks.
- **`decided` → plan-with-docs.** A decided decision points `decided_in:` at
  durable evidence — an ADR, or a receipt card recording the plan handoff
  (which survives `/push-plan` migrating the plan to a tracker, because the
  card is updated to the tracker URL rather than deleted).

No auto-promotion in either direction — promotion is an explicit act, for the
same reason ledger rewriting is. And no reverse reconciliation in v1:
completing a bridged task in the tracker does **not** discharge the local
obligation; the ledger measures declared debt as last hand-updated. The
receipt cards are what make a later `sweep` verb possible (walk receipts, check
tracker state, propose discharges), but that verb is deliberately not in v1.

### What the skill must **not** do

Each corresponds to a way this becomes theatre:

- silently create a destination to make a record resolve;
- auto-repair a stale ledger during validation (rewrite must be an explicit
  act, or the number stops being checked and starts being generated);
- widen coverage to every markdown file, which turns the discipline into noise
  and trains people to satisfy it mechanically;
- let the LLM assert any status or count the script can compute;
- report the obligation count as a progress metric on its own — see below.

### Test surface, extended

The fixture-tree/`--root` discipline above carries over, extended with
fixtures for: cross-record referential integrity (including a blocker filed
against a `decided` decision without a reopen); derived readiness (a stored
`blocked`/`ready` value is an unknown-key error — only `state:` parses);
`answered` without an `answer:` field failing; destination tightenings (a
directory fails, `../` traversal fails, a symlink escaping the repo fails);
card rules (`stub` without `superseded_when` fails, `receipt` without `url`
fails); the contracts coverage rule (a contracts file with neither an
obligation nor a `none` fails); scoped ledger freshness (a stale foreign track
does not fail `--track`; stale `LEDGER.md` warns in plain `validate` and fails
`--strict`); grammar (inline `#` comment fails the enum, comma-list `blocks:`
parses); and a two-project tree (id scoping; a cross-project destination
fails).

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
- **Coverage reaches questions files and `contracts/` directories only.** A
  precondition stated in prose anywhere else is still registered voluntarily.
- **The ledger counts registered obligations, not tasks.** It is a measure of
  declared debt, not of remaining work.
- **Bridged work is not reconciled.** An obligation whose receipt points at a
  tracker task stays open until a human discharges it, whatever the tracker
  says. The ledger is declared debt as last hand-updated; a `sweep` verb over
  receipt cards is the designed-for later fix.
- **The divergence signal is a snapshot.** The ledgers store no history, so
  "questions converging while obligations climb" is read by a human across
  commits (or `git log` of the ledger lines), not computed by the script.

## Provenance

Origin repo: `aiutopilot`. The instrument is PR #25; the plan that first adopted
it is PR #23. The independent corroboration is PR #24, in which another agent hit
the same failure mode during an unrelated review and solved it by hand, writing:
_"The real risk was never that task 6 omitted the alert; it was that no card
recorded the debt."_ That sentence is this design's thesis, arrived at
independently and without the mechanism — which is the best available evidence
that the failure mode is structural rather than personal.
