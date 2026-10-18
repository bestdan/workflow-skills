---
name: research-spike
description: Use when a repo is running a long research spike — questions gating a decision before something gets built — and you need to file a question, record its answer, register the obligation an answer creates, defer work to a stub or existing destination, backfill a free-form open-questions doc into structured records, promote a track's proposed decision, or check convergence — what still blocks a decision, whether obligations are piling up faster than questions close, whether the spike is converging or just feels stuck. Also for auditing a dev_docs/research/ tree or reviewing an obligation ledger.
---

# research-spike — the obligation ledger and question convergence for research spikes

A research spike is a repo whose job, for a while, is answering load-bearing
questions before building the thing. This skill is the judgment half of that
instrument; `scripts/research-spike.py` is the other half, and the two must
never trade places.

Engineering record — final architecture, load-bearing decisions, and the
gotchas a maintainer needs before touching the script:
[`../../dev_docs/research_spike.md`](../../dev_docs/research_spike.md).

## The boundary, first

Presenting this skill's five procedures next to the script's six subcommands
as one flat verb list was reviewed as the most likely way to implement the
wrong half — a reader skimming a table cannot tell "run this" from "walk this
dialogue." So the rule comes before any list:

**The script never edits prose. The LLM never computes a status or a count.**

The one-line test: _if two runs over the same tree could disagree, it belongs
to the script._ Parsing, every validation rule, decision readiness, the
`status` report, ledger derivation and freshness, and the advisory lexical
scan — all script. Prose, proposed ids, `none:` reasons, the interactive walk
of filing, answering, deferring, backfilling, and the judgment of _whether_
something is a deferral at all — all this skill. Every procedure below ends by
running `validate`, and it is the validator — not the procedure — that
guarantees the result held.

## The on-disk structure

```
dev_docs/research/<project>/
  PROJECT.md            # charter: what's being built, links to tracks
  decisions.md          # organizer-owned: the decisions this spike unblocks
  LEDGER.md             # generated roll-up (organizer-owned, never hand-edited)
  tracks/<track>/
    questions.md        # the track's questions + answers + its own ledger
    contracts/          # optional: every file here must declare, like a question section
    obligations/        # stub and receipt cards — where deferred work gets an address
```

Multiple projects coexist under `dev_docs/research/`; each is self-contained.
**Ids are declared bare and qualified by the script**: `project/track/id` for
questions and obligations; `project/id` for decisions (decisions qualify
project-wide even when a `proposed` one is filed inside a track, so promoting
it never changes its id). Cards carry no `id:` field at all — a card is
addressed by its file path under `tracks/<track>/obligations/`, which is what
an obligation's `destination:` points at. Two projects can each have an
`account` track without collision — `blocks:`/`blocking:` references resolve
within the enclosing project only, and **cross-project destinations are
forbidden**: work an answer creates for another project is filed in that
project, with a receipt card recording the handoff back.

## The script surface

Six subcommands, `research-spike.py`, stdlib-only:

| Subcommand                                         | Behavior                                                                                                                                                              |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `init <project> [--track <name>]`                  | Scaffold a project (or add a track to an existing one) — the one verb that legitimately creates files.                                                                |
| `validate [<project>] [--track <name>] [--strict]` | The gate. Whole tree, one project, or one track. `--strict` fails a stale `LEDGER.md` instead of warning — the organizer's tier.                                      |
| `ledger [<project>] [--track <name>]`              | Print the derived ledgers. Writes nothing.                                                                                                                            |
| `write-ledger [<project>] [--track <name>]`        | Rewrite the stored ledgers in place. Explicit act, never auto-repair.                                                                                                 |
| `status [<project>]`                               | The convergence report: what still blocks each decision, per track and total.                                                                                         |
| `suggest`                                          | Advisory lexical scan for unregistered deferral prose. Always exits 0 — a false positive against English prose has nowhere legal to go, so this can never fail a run. |

**Two different paths are involved here, and they must not be conflated.**
The **executable** resolves against the **plugin** — wherever this skill is
installed:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" \
  --root "$(git rev-parse --show-toplevel)" validate
```

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/scripts/research-spike.py` (mirrors `commands/handlers/linear-common.md`'s
fallback for the same class of script).

The **data root** — where `dev_docs/research/` actually lives — resolves
against the **consumer repo**, and must be passed explicitly on **every**
invocation, as `--root "$(git rev-parse --show-toplevel)"` above. **Never rely
on the default root inside a procedure.** `--root` defaults to the current
working directory, not to the script's own location — but a procedure's
working directory is not guaranteed to be the repo root, and confusing the two
paths is not hypothetical: it is the same defect class the script's own
docstring documents as PRE-611, where an `__file__`-anchored default scanned
the plugin checkout instead of the consumer repo and reported success,
because the plugin has no `dev_docs/research/` tree to fail on. "No research
dir is clean" and "wrong tree scanned" produce byte-identical output. Every
invocation in this skill and in `references/record-grammar.md` carries
`--root` for exactly this reason.

## The five procedures

Judgment, not computation: the LLM writes the fenced blocks directly into the
markdown, then immediately runs `validate`. **The validator, not the
procedure, is what guarantees the result** — none of what follows is
trustworthy on its own say-so.

The commands below are abbreviated: each one is missing the
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py"` prefix from the
section above, and nothing else. What is **not** abbreviated is the leading
`--root` — it is a global option, so it must sit before the subcommand or
argparse rejects the whole invocation, and it is written out every time here
rather than left to a reader's memory.

### 1. `file` — add a question

1. Confirm the track (`tracks/<track>/questions.md`) and read its existing
   `### Q<n>.` headings to pick the next number.
2. Pick a kebab-case id, unique within the track. The script qualifies it as
   `project/track/id`, so the same bare id in a different track — even in the
   same project — is fine; it is only a collision within the same qualified
   scope (and across kinds: a question and an obligation sharing a track
   cannot reuse each other's bare id either).
3. Write the section and its `question` block:

   ````markdown
   ### Q4. Must the account use an isolated uid domain?

   ```question
   id: uid-domain-isolation
   status: open
   blocks: account-provisioning
   ```
   ````

   `blocks:` names one or more decision ids (comma-separated), or the
   sentinel `blocks: none: <reason>` for a question that gates nothing yet.
   **Naming a decision that doesn't exist fails validation** — the "track
   that did not exist" bug, prevented for decisions too. If the decision
   doesn't exist yet, either offer the existing decision list to the person
   filing, or file a `state: proposed` decision block in this track's own
   `questions.md` (never in `decisions.md` — that promotion is the
   organizer's act, see procedure 5).
4. **Coverage is required immediately, not deferred to `answer`.** The
   script's coverage rule is unconditional per section — it fires the moment
   the section exists, whatever the question's status. A freshly filed
   `open` question with no `obligation` block already fails `validate` with
   "declares nothing it owes." So `file` always ends the section with either
   a real `obligation` block or a bare `none:` declaration:

   ````markdown
   ```obligation
   none: filing only, no work identified yet
   ```
   ````
5. Run `--root "$(git rev-parse --show-toplevel)" write-ledger <project> --track <name>`
   (the new question changed the track's counts). Scope it explicitly, with
   **both** the project and the track: `write-ledger` with no scope at all
   rewrites every track's `questions.md` _and_ every project's
   organizer-owned `LEDGER.md`, which is exactly what a track procedure must
   not touch (see "Write-ownership convention" below). `--track <name>` alone
   is not enough either — if two projects share a track name it is
   ambiguous, and the script exits 2 asking for the project argument. Then
   run `--root "$(git rev-parse --show-toplevel)" validate --track <name>`.

### 2. `answer` — walk a question to `answered`

1. Add the one-line conclusion as `answer:` on the `question` block, and put
   the evidence in the section's prose.
2. Satisfy coverage **before** flipping `status: answered` — same rule as
   filing, but this time it usually needs a real obligation rather than a
   bare `none:`, because answering a question in a spike mostly creates
   obligations, not new questions. Replace or add the section's `obligation`
   block(s) with what the answer actually implies.
3. Flip `status: open` to `status: answered`.
4. Run `--root "$(git rev-parse --show-toplevel)" write-ledger <project> --track <name>`
   (scoped — see procedure 1's note on why the bare form is wrong), then
   `--root "$(git rev-parse --show-toplevel)" validate --track <name>`.

### 3. `defer` — register an obligation

Used mid-answer, or any time prose is about to create work with nowhere to go
yet.

1. Write the `obligation` block next to the prose that creates the
   deferral — locality is what stops it drifting from the sentence it
   describes.
2. `destination:` must point at a file that **already exists**.
   - If the repo's merged task-loop config resolves to an **external
     tracker handler** (`gh-issue` / `jira` / `linear` — "The two bridges"
     below has the exact resolution rule and why), offer `/add-task` as an
     alternative to a stub. On acceptance, write the receipt card that
     section describes and point `destination:` at it.
   - Otherwise — the common case, and also the fallback whenever `/add-task`
     is declined — **create a stub card first, and say so out loud**:
     announce that a stub was created, because the stub count is the
     diagnostic this whole instrument exists to surface.

     ````markdown
     ```card
     kind: stub
     superseded_when: the account track files its uid-domain-provisioning task card
     ```
     ````

     Then point the obligation's `destination:` at the stub's path
     (`tracks/<track>/obligations/<name>.md`).
3. Set `status: open` — there is no in-between state; a half-state is a
   place for work to sit and look accounted for. Add
   `blocking: <decision-id>` only when this obligation genuinely gates a
   decision — most should not; scarcity is what keeps convergence
   meaningful.
4. Run `--root "$(git rev-parse --show-toplevel)" write-ledger <project> --track <name>`
   (scoped — see procedure 1's note), then
   `--root "$(git rev-parse --show-toplevel)" validate`.

### 4. `backfill` — import an existing doc

A section-by-section, interactive walk of an existing free-form
questions/answers document, converting each into a structured `question`
(plus its coverage) in the track's `questions.md`.

1. Walk the source doc top to bottom. For each question found: file it
   (procedure 1), carry its answer if it has one (procedure 2), and register
   whatever obligations the prose already implies (procedure 3) — creating
   stub cards for anything with no destination.
2. **The backfill is the moment of truth, not a formality.** In the
   reference repo, backfilling one file produced 13 records, and **4 of them
   had no destination at all** — obligations that had been living as prose,
   invisible, until this walk gave them an address. Expect the same shape
   here: needing several stub cards to complete a backfill is the finding,
   not an inconvenience.
3. Once every section in the source is imported, run
   `--root "$(git rev-parse --show-toplevel)" write-ledger <project> --track <name>`
   for the track (scoped — see procedure 1's note), then
   `--root "$(git rev-parse --show-toplevel)" validate` over the whole
   tree — a backfill is the procedure most likely to touch multiple sections
   at once, so scope the final check wide.

### 5. `promote-decision` — organizer-only

Moves a track's `proposed` decision block into the project's `decisions.md`.

1. Find the `decision` block with `state: proposed` inside
   `tracks/<track>/questions.md`.
2. Copy it into `decisions.md` verbatim except `state: proposed` →
   `state: pending`. Delete the block from the track's `questions.md` — a
   decision may exist in exactly one of the two places, and the script
   rejects a `proposed` block anywhere but a track's own `questions.md`
   (`decisions.md` holds only decisions the organizer has already promoted).
3. Run `--root "$(git rev-parse --show-toplevel)" validate --strict` —
   `promote-decision` writes `decisions.md`, which is organizer territory,
   so check it at the organizer's tier.

## The two bridges

The spike machinery stands alone and works in any repo. Two bridges exist —
and both had their first drafts rejected in review for violating the
destination-must-exist invariant. Tracker handlers return **URLs, not
paths**, and `/push-plan` **deletes** plan directories after migration, so
an obligation or a decision pointing at either rots on first contact. Both
bridges therefore route through **receipt cards** (`kind: receipt`, field
reference in `references/record-grammar.md`): the pointer — `destination:`
for an obligation, `decided_in:` for a decision — names the card file, a
path that exists, while the external reference lives in card content the
validator never path-checks and, being offline by contract, could not
verify anyway.

The `defer` bridge is gated on **the repo's merged task-loop config**
(`dev_docs/tasks/.task-config.yml` overlaid with the optional
`dev_docs/tasks/.task-config.local.yml` — see `commands/task-config.md`
"Resolving the handler") **resolving to an external tracker handler**
(`gh-issue` / `jira` / `linear`) — not on `.task-config.yml` merely existing.
With no config at all, resolution defaults to `repo-pr`
(`skills/task/SKILL.md` → "Handlers and config"), and `repo-pr` local
staging returns a staged file path and branch name, never a URL
(`commands/add-task.md` "The drafted task (handler input)") — there is
nothing for a receipt card's `url:` to carry, so the bridge is inapplicable
and the stub path (procedure 3) is the right answer. Under an explicit
`handler: repo-pr` the conclusion is the same, for the same reason: the
destination is a local file anyway.

### `defer` → task loop

Procedure 3, step 2: when the merged config resolves to an external tracker
handler, `/add-task` is offered as an alternative to a stub. On acceptance,
`/add-task` runs and returns the created work item's **URL** — its contract
does not return a tracker id back to the caller (`commands/add-task.md`
"The drafted task (handler input)"; a handler such as
`commands/handlers/linear-add.md` step 5 hands back `issue.url` only). The
procedure writes a `kind: receipt` card carrying that `url:` and points the
obligation's `destination:` at the card rather than at a stub.
`validate_cards` requires only `url:` on a receipt — `tracker_id:` is
accepted but never enforced (`references/record-grammar.md` → `card`), so
fill it in only when it's worth restating: for linear/jira/gh-issue the key
is visible in the URL itself (`PRE-142` in `.../issue/PRE-142/...`). Card
and obligation, side by side — the shape is the whole point:

```card
kind: receipt
handler: linear
url: https://linear.app/example-team/issue/PRE-142/account-quota-followup
```

```obligation
id: account-quota-followup
owes: the quota-check helper this answer implies but doesn't build
destination: dev_docs/research/demo/tracks/account/obligations/account-quota-followup-receipt.md
status: open
```

If `/add-task` is declined, or the merged config resolves to `repo-pr` (no
config, or set explicitly), fall back to the stub path in procedure 3.

### `decided` → plan-with-docs

A `decided` decision's `decided_in:` must point at **durable** evidence: an
ADR, a permanent design doc, or a receipt card recording the plan handoff —
never a plan directory. A plan directory is not durable: `/push-plan`
deletes it once every task in it has migrated and the overview was written
to the tracker this run — a **partial** push (anything held by
`--ready-only`, or a failed create) retains the directory
(`commands/push-plan.md` §6) — and the validator does not merely warn about
that the way it does for an obligation's `destination:` — for `decided_in:`
a plan-directory pointer is an **error** (`check_decided_in`), because a
decision's evidence has to outlive the work it documents, not just survive
until the next push.

A handoff receipt survives the same migration for a simpler reason: the
card lives in the spike's own `tracks/<track>/obligations/` directory, not
in the plan directory `/push-plan` deletes, so nothing that command does
touches it. `/push-plan` has no receipt discovery or update step at all —
it records tracker ids in the plan's own task and epic frontmatter and then
deletes those files once migrated (`commands/push-plan.md` §6, the §4.5/
§5b.6 cleanup steps); it has no way to know a receipt card exists.
**Updating the card's `url:` to the pushed tracker URL is a manual step in
the handoff**, done by whoever runs `/push-plan` — not something the push
performs.

### No auto-promotion in either direction

Neither bridge fires on its own. `defer` only offers `/add-task` — accepting
it is a human choice, same as picking a stub. A decision still moves
`pending` → `decided` only by a human act, recorded with `decided_in:`.
Promotion is an explicit act here for the same reason `write-ledger` is:
fold either into the walk and a number, or a state, stops being checked and
starts being generated.

## Write-ownership convention

Track agents touch `tracks/<theirs>/` only — proposed decisions included.
`decisions.md` and `LEDGER.md` are organizer-owned. Nothing in the script
enforces this directly; every cross-track effect it could cause anyway (a
duplicate id, a broken destination, a stale foreign ledger) is caught by the
organizer's project-wide `validate`. One agent at a time per track: if two do
collide there, the stored ledger block at the top of `questions.md` produces
an ordinary git merge conflict — that is the intended detection, not a bug to
route around.

## The agent-context contract

An agent told to "work track X" needs exactly `tracks/X/` plus the project's
`decisions.md`, and nothing else. State this explicitly here so a future
`/work-track` dispatcher or auto-pilot integration composes without
redesign — it is the structural payoff of the on-disk layout above, not an
incidental convenience.

## The discipline the tool cannot supply

- **Register at deferral time, not at review time.** The cost of registering
  is one minute and one stub card. The measured cost of not registering was
  four days of invisible accrual and a maintainer asking whether the project
  was viable.
- **Count what you are actually spending.** Questions-answered is the
  flattering metric; obligations-open is the predictive one. Report both, or
  report neither — reporting only the first is how a converging-question
  count hides a climbing obligation count.
- **Be honest about granularity.** The reference ledger read _10 open_
  against a hand count nearer _23_, because records are registered at the
  altitude the deferral was made — one answer becomes one component
  obligation, not the fourteen task cards that component will eventually
  need. Both numbers are true; they measure different things. Never let the
  smaller one be read as progress.
- **A stub that never gets superseded is a new place for work to hide.**
  That is why `superseded_when:` is required on every stub — a card with no
  stated condition for its own deletion is exactly the kind of file this
  instrument exists to prevent.

## What the skill must not do

- **Silently create a destination to make a record resolve** — or this
  becomes theatre: the whole point of `destination:`/`decided_in:` is that
  they name something that was already true before the record referenced it;
  writing the file to satisfy the pointer, backwards, forges the evidence
  the check exists to require.
- **Auto-repair a stale ledger during validation** — or this becomes
  theatre: `write-ledger` is a separate, explicit act. Fold it into
  `validate` and the number stops being _checked_ and starts being
  _generated_, silently, on every run — the exact invisibility this
  instrument exists to end.
- **Widen coverage past questions files and `contracts/`** — or this
  becomes theatre: requiring every markdown file in the tree to declare
  turns a discipline into noise, and noise trains people to satisfy a rule
  mechanically instead of thinking about it.
- **Let the LLM assert any status or count the script can compute** — or
  this becomes theatre: a hand-typed "READY" or "3 open" in a report is
  exactly the stale-copy failure the record format's own field list is
  designed to make impossible; restating it in prose reopens the hole.
- **Report the obligation count as a progress metric on its own** — or this
  becomes theatre: without the question count next to it, a shrinking or
  growing number means nothing. The divergence between the two is the
  signal, not either count alone.

## Known limits, stated rather than hedged

- **It cannot see a deferral that was never registered.** Prose can always
  describe work going somewhere without emitting a record. The coverage rule
  closes this in the one file where deferrals are created; `suggest`
  gestures at the rest; neither closes it in general.
- **Coverage reaches `questions.md` and `contracts/` directories only.** A
  precondition stated in prose anywhere else in the tree is still registered
  voluntarily, not mechanically.
- **The ledger counts registered obligations, not tasks.** It measures
  declared debt, not remaining work — see the granularity note above.
- **Bridged work is not reconciled.** An obligation whose receipt card
  points at a tracker task stays `open` until a human discharges it by
  hand, whatever the tracker itself says — closing the tracker issue does
  **not** discharge the local obligation. The ledger reports declared debt
  as last hand-updated. The receipt cards are what would make a later
  `sweep` verb possible — walk the receipts, check tracker state, propose
  discharges — but that verb is deliberately not in v1.
- **The divergence signal is a snapshot.** The ledgers store no history, so
  "questions converging while obligations climb" is read by a human across
  commits — `git log` on the ledger lines — not computed or trended by the
  script.

## Record grammar

Full field reference for all four record types, the parser's exact rules, and
a worked example of each: [`references/record-grammar.md`](references/record-grammar.md).

## Adoption playbook

The setup sequence for turning this on in a repo with real, live deferred
work — `init`, then the backfill (the payload step, not a formality), then
stub cards for what has no destination, then wiring `validate` into the
repo's own check with the reasoning for what stays out of it:
[`references/adoption.md`](references/adoption.md).
