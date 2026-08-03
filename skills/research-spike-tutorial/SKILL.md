---
name: research-spike-tutorial
description: Use when someone wants to learn how the research-spike obligation-ledger instrument works, hands-on and with nothing at stake, before ever touching it for real — typically with no live spike of their own yet. Not for managing an actual dev_docs/research/ tree (research-spike) and not a comprehension check on work already done (tutor). Runs the real scripts/research-spike.py against a disposable tree under a temp directory, never the user's repo — scaffold a project, file a question, deliberately hit the "destination must already exist" wall and watch validate fail for real, fix it with a stub card, then a couple more rounds fast enough to watch questions-answered converge while obligations-open climbs faster, on the same screen. Trigger on requests to be walked through, shown, taught hands-on, or given a demo/tutorial of research-spike, the obligation ledger, "the wall", or "the divergence". Ends by offering, not performing, a /tutor comprehension pass.
---

# research-spike-tutorial — feel the divergence, on a tree that isn't real

This is a guided, hands-on build — not a document. The learner does not read
about the instrument; they run it, watch a real `validate` fail on purpose,
fix it, and then watch their own ledger diverge. Everything happens inside a
disposable tree built under a temp directory and destroyed at the end. **At no
point does this skill touch the user's actual repo** — no `dev_docs/research/`
tree gets written anywhere real.

This is not [`references/adoption.md`](../research-spike/references/adoption.md)
— that playbook is "set this up for real, against your actual backlog."
Nothing here is real; the payoff is understanding, not a shipped tree. And
this is not `/tutor` — `/tutor` elicits and quizzes understanding of work that
already happened; this skill _creates_ the work, live, for the first time.
Where a comprehension check is wanted once the walk is done, hand off to
`/tutor` rather than reimplementing its elicit/diagnose/quiz loop here.

## Before you start

Resolve the real script the way every other procedure in this plugin does —
never a description of it, the actual executable:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" <verb>
```

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/scripts/research-spike.py` and use what that finds.

**`--root` is a global option and must precede the subcommand**, exactly as
`dev_docs/research_spike.md` gotcha 6 documents — `research-spike.py validate
--root "$WORK"` is a usage error (exit 2, `unrecognized arguments: --root`).
Every command below carries `--root "$WORK"` right after the script path,
never after the verb. Run every command for real — do not narrate or
paraphrase output. If a command in this file ever produces something other
than what its section says it produces, trust the terminal, not this file,
and tell the learner what actually happened.

**`$WORK` below is a placeholder for a literal path, not a shell variable you
can rely on.** Each Bash call runs in a fresh shell, so the assignment in Step
1 is gone by Step 2. Read the absolute path out of Step 1's `echo` and
substitute it into every later command yourself.

This is not a tidiness rule, and it is the one mistake that turns this
tutorial into the thing it promises never to do. An unset `$WORK` expands to
nothing, leaving `--root ""` — and `Path("")` is `Path(".")`, an existing
directory, so the script accepts it silently and scaffolds
`dev_docs/research/` into whatever directory you happen to be standing in.
That is the learner's own repo. It has already happened once, during this
skill's own development.

So before every command, check the root you are about to pass:

- it is non-empty;
- it is the absolute path Step 1 printed;
- it is **not** inside the learner's repo (compare against
  `git rev-parse --show-toplevel`).

If any of those fails, stop and re-derive the path. Never fall back to a bare
`--root ""` or to the current directory.

Say the whole spine up front, once, before starting: _"You'll answer
questions, and the answered count will go up — that feels like progress. Watch
what the obligation count does at the same time."_ Don't explain further yet;
let them watch it happen.

Number every step as you go — "Step 3 of 8" — so the learner always knows
where they are in an 8-step walk.

## How this walk is paced

**Read [`references/pacing.md`](references/pacing.md) before Step 1** — it
governs the delivery of every step below, and the walk fails at its purpose
without it. In one line: **one step per turn, and every step ends by handing
the terminal back**, because a mechanism the learner scrolled past is one they
did not see. The reference carries the recap/stop/wait contract, how to handle
questions asked mid-walk, the say-it-before-you-run-it rule, and the
terms-of-art glossary each step's checkpoint draws on.

## Step 1 of 8 — Build the throwaway tree

Ask once, with `AskUserQuestion` — this changes only which directory `$WORK`
is created under, never whether it's freshly created:

- **A fresh `mktemp -d` directory** (default; gone the moment this session
  cleans up, nothing to look at afterward)
- **A fresh subdirectory of this session's scratchpad** (also disposable, but
  the learner can browse the files after the walk before you delete them)

`$WORK` must always be a directory this walk just created, never a
pre-existing one — Step 8 deletes it, and it must be impossible for that
deletion to reach anything the learner didn't just watch you make.

```bash
WORK="$(mktemp -d)"
# or, if the scratchpad was chosen, a fresh subdirectory under it instead:
WORK="$(mktemp -d "<scratchpad-dir>/research-spike-tutorial.XXXXXX")"
echo "$WORK"
```

**Read that printed path and keep it.** It is the value you substitute into
every command for the rest of the walk — the variable itself does not survive
into the next Bash call. If `mktemp` fails, the assignment is empty and `echo`
prints a blank line: that is a hard stop, not something to work around. Do not
continue with an empty root.

Tell the learner exactly where it is, and that it is not, and will never be,
anywhere in their repo. Nothing under `dev_docs/research/` in their actual
checkout is touched by anything in this walk.

### Get them a window into the tree

Everything from here on happens in a directory the learner is not standing in,
and half of what this walk teaches is what the _files_ do — the ledger block
rewriting itself, the stub card appearing, `questions.md` growing a section
per round. Watching that in an editor is the difference between following
along and taking your word for it.

So offer, right now, to open the tree in an editor window they keep beside the
conversation:

```bash
code "/absolute/path/printed/above"
```

Substitute the literal path, same rule as everywhere else. `code` is the usual
one — probe it with `command -v code` first, and fall back to whatever's
present (`cursor`, `subl`, `zed`, `open` on macOS for the Finder) rather than
insisting on one editor. If none resolves, say so and offer `open`/`xdg-open`
on the directory instead; this is a nicety, not a prerequisite, so a missing
editor never blocks the walk.

Two things to tell them when it opens: the window will be **empty** until
Step 2 scaffolds the project, and it is worth leaving open until Step 7,
because Step 8 deletes the directory out from under it.

**Checkpoint.** Empty directory, real path, an editor pointed at it, nothing
else yet. Stop and ask.

## Step 2 of 8 — Scaffold a project

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  init onboarding --track auth
```

`init` is the only verb that creates _new_ files — `write-ledger` rewrites
the ledger blocks inside files `init` already created, it never adds one of
its own. Show the learner what it printed and what now exists: `PROJECT.md`,
`decisions.md`, `LEDGER.md`, and `tracks/auth/questions.md` with the ledger
markers already in place and a worked example sitting inert inside an HTML
comment.

This scaffold has no decision to gate yet. Append one — this is the thing the
whole spike exists to unblock — to `dev_docs/research/onboarding/decisions.md`:

```decision
id: sso-rollout
state: pending
```

Refresh the ledger you just changed, then run `validate`, so the learner sees
the freshly-scaffolded tree pass:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" write-ledger
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" validate
```

`validate` exits `0` and prints:

```
  ⚠ dev_docs/research/onboarding/decisions.md:10: decision 'onboarding/sso-rollout' is referenced by nothing — no question `blocks:` it and no obligation is `blocking:` it, so the convergence report can only ever show it as ready, whatever is actually outstanding. Either it is already settled and belongs in the record of decisions taken, or the questions that gate it have not been wired to it yet. A warning, not an error: a decision with no blockers left is a normal end state.
research-spike: OK — 1 projects, 1 tracks, 1 records
  onboarding — tracks: auth (1 records)
```

A warning, not a bug: nothing `blocks:` the decision yet, and nothing should
until Step 3 files the question that does. A warning never fails the exit
code; only an `✘` finding does. A blank tree with one open, unblocked
decision is clean — just not silent.

**Checkpoint.** Four files exist, a decision is pending, `validate` passes
with one warning. Terms first used here: _spike_, _track_, _decision_,
_the ledger_ — define each, then stop and ask. This is the natural moment to
look at the files: if they took the editor window, tell them it just went from
empty to four files and let them read it there; otherwise offer to show any
one of them. Don't paste all four unasked either way.

_obligation_ is the exception, and say so rather than letting it pass: the
word has already gone by twice — in the spine you opened with, and in the
warning on screen right now — and it is a term of art, not the ordinary
sense. Name it as one, say its definition is coming at Step 4 where the first
real obligation appears, and leave it there. Defining it now would spend the
tease the spine is built on.

## Step 3 of 8 — File your first question

Show the learner **R1** from [`references/records.md`](references/records.md)
before writing it — that file holds every literal block this walk creates, and
the ids and paths are wired together, so copy it verbatim rather than
improvising one.

Name the rule as you write it: **coverage is required the instant the section
exists**, not once it's answered. That bare `none:` is not decoration — drop
it and this same question, still `open`, already fails `validate` with
"declares nothing it owes." Write the block, then run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  write-ledger onboarding --track auth
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  validate --track auth
```

Both succeed. One question filed, nothing owed yet, tree still clean.

**Checkpoint.** Terms first used here: _question record_, and the
coverage rule — a section owes a declaration of what it owes from the moment
it exists, even when the honest declaration is `none:`. Stop and ask. Worth
inviting a specific one before moving on: _what do you think happens if you
delete that `none:` block?_ — they can guess, and Step 4 is about to reward
guessing.

## Step 4 of 8 — Answer it, and hit the wall

Tell the learner plainly, before running anything: **the next `validate` is
going to fail. Run it anyway — the failure is the lesson.**

Replace the Q1 section in place with **R2** from
[`references/records.md`](references/records.md): `status` flips to
`answered`, a one-line `answer:` and a sentence of evidence arrive, and the
bare `none:` becomes a real obligation pointing at a stub card **that does not
exist yet**. Show it before writing it, as always.

Answering a question mostly _creates_ obligations, not new questions — that's
the pattern to name here. Now run, for real:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  write-ledger onboarding --track auth
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  validate --track auth
```

`write-ledger` succeeds — it only rewrites the stored counts, it never checks
the tree it just wrote. `validate` exits `1`, and prints (line number will
differ slightly depending on exactly what you typed; the message text will
not):

```
research-spike: FAIL
  ✘ dev_docs/research/onboarding/tracks/auth/questions.md:79: destination 'dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md' does not exist — this is how deferred work goes dark: naming a place does not create one, and a deferral to a path that is not there reads as routed while routing nowhere. Write the file first — a stub card under tracks/<track>/obligations/ carrying its own `superseded_when:` is the usual move — then point at it.
```

Stop here and make them read it. That sentence — _naming a place does not
create one_ — is the entire mechanism this instrument exists to enforce. A
`destination:` is a promise the filesystem checks, not prose that sounds
routed.

**Checkpoint — the longest pause in the walk. Do not fix it in this turn.**
The tree is sitting in a failing state and it should stay there while they
look at it. Terms first used here: _obligation_ (spend a beat on it: a debt an
_answer_ created, which is why answering can leave you further from done than
you started) and _destination_. Then ask them what they'd do about it before
Step 5 tells them — the fix is guessable from the error text, and guessing it
is worth more than reading it.

## Step 5 of 8 — The stub card that fixes it

Show **R3** from [`references/records.md`](references/records.md), then write
it to
`dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md`.

Say it out loud, the way `SKILL.md`'s `defer` procedure requires: _a stub was
just created_ — the stub count is the diagnostic this whole instrument exists
to surface, and `superseded_when:` is required precisely so this file cannot
sit forever with no stated condition for its own deletion.

Run `validate` again:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  validate --track auth
```

It fails again — but differently. The destination now resolves, but creating
the stub changed the track's true obligation count, so the _stored_ ledger
(written one command ago, before the stub existed) is now stale:

```
research-spike: FAIL
  ✘ dev_docs/research/onboarding/tracks/auth/questions.md:3: stored ledger is stale — run `write-ledger` to refresh it. stored: - **Questions:** 1 answered, 0 open, 0 retired | - **Obligations:** 0 discharged, 1 open (0 blocking, 0 stubs, 0 external) — derived: - **Questions:** 1 answered, 0 open, 0 retired | - **Obligations:** 0 discharged, 1 open (0 blocking, 1 stub, 0 external)
```

This is not a new bug — it is the same "stored, not computed" contract from
Step 3, catching itself. Refresh it and check again:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  write-ledger onboarding --track auth
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  validate --track auth
```

`research-spike: OK — onboarding/auth: 3 records`. One full cycle done: file,
answer, hit the wall, fix it with an addressed, self-expiring stub.

**Checkpoint.** Two failures and a pass, and the second failure was not the
one anyone predicted — say that plainly, it's the most instructive surprise in
the walk. Terms first used here: _stub card_, _`superseded_when:`_, _stale
ledger_. Stop and ask, and flag what to watch next — without numbers, they're
Step 7's to spend: whether the obligation count keeps pace with the question
count.

## Step 6 of 8 — Two more rounds, faster

Now that the mechanism is familiar, compress it: write the stub cards _before_
running `validate`, so the next two rounds pass clean on the first try — same
rule, no more waiting on it. Append both sections to the same
`tracks/auth/questions.md` — that's **R4** in
[`references/records.md`](references/records.md), where each answer creates
**two** obligations rather than one. That fan-out is deliberate: real answers
routinely land across more than one component, which is exactly how the count
outpaces the question that made it.

Then write the four stub cards up front — **R5** in the same file, one per
`destination:` R4 names — and run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  write-ledger onboarding --track auth
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  validate
```

Clean on the first try this time — you'll likely also see a warning that
`LEDGER.md` (the project-level roll-up) is stale. That's expected: the scoped
`write-ledger onboarding --track auth` deliberately only ever touches the
track's own `questions.md`, never the organizer-owned `LEDGER.md` — leave it
for Step 7.

**Checkpoint.** Faster, but still one turn — this step wrote two questions,
four obligations and four stub cards, which is more new state than any step so
far. Recap what the tree holds now, and stop and ask before Step 7 pulls the
numbers. Don't preview the counts here; Step 7 is the payoff and stating it
early spends it.

## Step 7 of 8 — Read the divergence

Refresh the organizer-owned roll-up, then pull both reports:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" write-ledger
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" ledger
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" status
```

`status` prints:

```
onboarding — decisions: 0 decided, 1 ready, 0 blocked

  sso-rollout  READY awaiting decision

  auth:   Q 3 answered / 0 open / 0 retired    O 0 discharged / 5 open (5 stubs)
  total:  Q 3 answered / 0 open / 0 retired    O 0 discharged / 5 open (5 stubs)
```

Stop. Don't explain it — point at the two numbers and ask the learner what
they see. **3 answered, 0 open** — every question you filed got closed out;
by the flattering metric, this spike is done. **5 open, 0 discharged** — every
obligation those same three answers created is still sitting there, and it
grew faster than the questions that made it (one answer became two
components, twice). Make them say back, in their own words, which number
would appear in a status update and which number is the one that actually
predicts how much work is left. Don't supply the answer if they can get there
themselves — that's the whole instrument, on one screen, and it's real
output from a real run, not a mocked-up table.

Terms first used here: _discharged_ versus _open_ (worth the extra beat —
discharged means the work was actually done, not closed, deferred, or routed),
_retired_, and _convergence_. The `ledger` and `status` reports answer
different questions and it's worth saying which is which: `ledger` is the
stored roll-up, `status` is what's computed live from the tree right now.

**Checkpoint — the one that matters most.** Do not move to cleanup in the same
turn under any circumstances: the tree still exists, and the numbers are still
on screen only until you delete it. Stop, ask what they make of it, and stay
for follow-ups. Offer to look at anything in the tree that would help — the
stub cards, the questions file, the roll-up.

If they want to be checked on whether that actually landed — the wall, the
stub, the two counts diverging, why — offer `/tutor` now rather than quizzing
them yourself here; that loop (elicit, diagnose, verify with a counterfactual)
is what `/tutor` is for, and duplicating it in this file would drift from it
the first time either one changes.

## Step 8 of 8 — Clean up

Confirm first that they're done looking — this is the step that destroys the
evidence, and it is the one step in the walk that cannot be re-run. If they
have the Step 1 editor window open, say plainly that this is what empties it,
so the files vanishing reads as the cleanup working rather than something
breaking.

Substitute the literal absolute path from Step 1, exactly as everywhere else
in this walk. **Do not paste `$WORK` here.** The variable is unset in this
fresh shell, so `[ -n "${WORK:-}" ]` is false, the `&&` short-circuits, and
the command exits quietly having deleted nothing — leaving the tutorial's one
promise (nothing survives this walk) broken with no error to notice:

```bash
rm -rf "/absolute/path/printed/by/step/1"
```

Before running it, check the path the same three ways Step 1 did: non-empty,
the absolute path Step 1 printed, and not inside the learner's repo. A blank
path there is not "nothing to clean up," it is one quoting mistake away from
deleting whatever the shell happens to be sitting in. Then confirm the
directory is actually gone rather than assuming it:

```bash
[ -e "/absolute/path/printed/by/step/1" ] && echo "STILL THERE" || echo "gone"
```

Tell the learner plainly: the tree lived at that path, it is now gone, and
nothing under their own repo's `dev_docs/research/` was ever touched. If they
want to try the real thing next, point at
[`references/adoption.md`](../research-spike/references/adoption.md) in the
`research-spike` skill — that playbook is the same `init` → backfill → stub →
gate sequence, run for keeps.

Ask, with `AskUserQuestion`, whether to hand off to `/tutor` for a
comprehension pass on this walkthrough now, or stop here. Don't perform that
check yourself either way — inviting it and stopping are the only two things
this step does.
