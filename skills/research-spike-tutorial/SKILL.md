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
   the point is room for an arbitrary question, not a menu.
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

### Say what you are about to do, before you do it

Each step opens with a sentence of intent in plain language — _what_ this
command is about to do and _why the walk needs it now_ — before the command
runs. The learner should never watch a command execute without knowing what
it was for. Same on the way out: the recap says what actually happened, which
is not always what you intended (Step 5's stale-ledger failure is the case in
point).

### Terms of art: define at first use, every one of them

This instrument runs on a small private vocabulary, and every one of these
words also has a loose everyday meaning that will quietly mislead. Whenever
one appears for the first time, define it in one sentence, inline, before
using it as though it were shared:

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

Show the learner this block before writing it — this is the record they're
about to create, appended to `tracks/auth/questions.md`:

````markdown
### Q1. Should login redirect through the SSO gateway before issuing a session token?

```question
id: sso-redirect-required
status: open
blocks: sso-rollout
```

```obligation
none: filing only, no work identified yet
```
````

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

Edit the Q1 section in place: flip `status: open` to `status: answered`, add
one-line `answer:`, add a sentence of evidence in the prose, and replace the
bare `none:` obligation with a real one — pointing at a stub card **that does
not exist yet**:

````markdown
### Q1. Should login redirect through the SSO gateway before issuing a session token?

```question
id: sso-redirect-required
status: answered
blocks: sso-rollout
answer: yes — the gateway must own the redirect, so the session token is only issued after SSO succeeds
```

Traced through the current login handler: without the redirect, a client
can request a session token directly and skip SSO entirely. The handler
needs a check added before token issuance.

```obligation
id: sso-redirect-check
owes: the pre-issuance redirect check in the login handler
destination: dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md
status: open
```
````

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

Show the block, then write it to
`dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md`:

````markdown
# sso-redirect-check stub

```card
kind: stub
superseded_when: the auth track files its sso-redirect-check implementation task
```
````

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
`tracks/auth/questions.md`, each answer creating **two** obligations, not one
— this is deliberate: real answers routinely fan out into more than one
component, which is exactly how the count outpaces the question that made it:

````markdown
### Q2. Does the session token need a shorter TTL when SSO is used?

```question
id: sso-session-ttl
status: answered
blocks: sso-rollout
answer: yes — SSO sessions should expire sooner than password sessions, and logout should revoke them immediately
```

SSO sessions inherit trust from the identity provider, so a stale token
is a wider blast radius than a stale password session. Two things follow.

```obligation
id: ttl-config-change
owes: a shorter configurable TTL for SSO-issued sessions
destination: dev_docs/research/onboarding/tracks/auth/obligations/ttl-config-change.md
status: open
```

```obligation
id: revoke-on-logout
owes: immediate session revocation on logout for SSO sessions
destination: dev_docs/research/onboarding/tracks/auth/obligations/revoke-on-logout.md
status: open
```

### Q3. Should password-based login be disabled once SSO is required?

```question
id: password-login-disable
status: answered
blocks: sso-rollout
answer: yes, eventually — but not in the same release as the SSO redirect
```

Turning it off immediately would lock out any account not yet migrated.
The rollout needs a flag and a heads-up to existing users first.

```obligation
id: legacy-password-flag
owes: a feature flag that gates password login off per-account
destination: dev_docs/research/onboarding/tracks/auth/obligations/legacy-password-flag.md
status: open
```

```obligation
id: migration-notice-copy
owes: the in-product notice telling password users to switch to SSO
destination: dev_docs/research/onboarding/tracks/auth/obligations/migration-notice-copy.md
status: open
```
````

Write the four stub cards up front (same shape as Step 5's, one
`superseded_when:` each, filenames matching the four `destination:` paths
above: `ttl-config-change.md`, `revoke-on-logout.md`, `legacy-password-flag.md`,
`migration-notice-copy.md`), then:

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
