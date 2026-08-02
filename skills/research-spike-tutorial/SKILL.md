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

Say the whole spine up front, once, before starting: _"You'll answer
questions, and the answered count will go up — that feels like progress. Watch
what the obligation count does at the same time."_ Don't explain further yet;
let them watch it happen.

Number every step as you go — "Step 3 of 8" — so the learner always knows
where they are in an 8-step walk.

## Step 1 of 8 — Build the throwaway tree

Ask once, with `AskUserQuestion` — this changes where the tree actually lands:

- **A fresh `mktemp -d` directory** (default; gone the moment this session
  cleans up, nothing to look at afterward)
- **This session's scratchpad directory** (also disposable, but the learner
  can browse the files after the walk before you delete them)

Either way, capture it once and reuse it for every command that follows:

```bash
WORK="$(mktemp -d)"   # or the chosen scratchpad path
echo "$WORK"
```

Tell the learner exactly where it is, and that it is not, and will never be,
anywhere in their repo. Nothing under `dev_docs/research/` in their actual
checkout is touched by anything in this walk.

## Step 2 of 8 — Scaffold a project

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  init onboarding --track auth
```

`init` is the one verb allowed to create files. Show the learner what it
printed and what now exists: `PROJECT.md`, `decisions.md`, `LEDGER.md`, and
`tracks/auth/questions.md` with the ledger markers already in place and a
worked example sitting inert inside an HTML comment.

This scaffold has no decision to gate yet. Append one — this is the thing the
whole spike exists to unblock — to `dev_docs/research/onboarding/decisions.md`:

```decision
id: sso-rollout
state: pending
```

Run `validate` now, before anything else is added, so the learner sees a
freshly-scaffolded tree pass cleanly:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" validate
```

`research-spike: OK`. A blank tree is clean, not an achievement yet.

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
✘ dev_docs/research/onboarding/tracks/auth/questions.md:79: destination 'dev_docs/research/onboarding/tracks/auth/obligations/sso-redirect-check.md' does not exist — this is how deferred work goes dark: naming a place does not create one, and a deferral to a path that is not there reads as routed while routing nowhere. Write the file first — a stub card under tracks/<track>/obligations/ carrying its own `superseded_when:` is the usual move — then point at it.
```

Stop here and make them read it. That sentence — _naming a place does not
create one_ — is the entire mechanism this instrument exists to enforce. A
`destination:` is a promise the filesystem checks, not prose that sounds
routed.

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
✘ …questions.md:3: stored ledger is stale — run `write-ledger` to refresh it. …
```

This is not a new bug — it is the same "stored, not computed" contract from
Step 3, catching itself. Refresh it and check again:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  write-ledger onboarding --track auth
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py" --root "$WORK" \
  validate --track auth
```

`research-spike: OK`. One full cycle done: file, answer, hit the wall, fix it
with an addressed, self-expiring stub.

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

If they want to be checked on whether that actually landed — the wall, the
stub, the two counts diverging, why — offer `/tutor` now rather than quizzing
them yourself here; that loop (elicit, diagnose, verify with a counterfactual)
is what `/tutor` is for, and duplicating it in this file would drift from it
the first time either one changes.

## Step 8 of 8 — Clean up

```bash
rm -rf "$WORK"
```

Tell the learner plainly: the tree lived at `$WORK`, it is now gone, and
nothing under their own repo's `dev_docs/research/` was ever touched. If they
want to try the real thing next, point at
[`references/adoption.md`](../research-spike/references/adoption.md) in the
`research-spike` skill — that playbook is the same `init` → backfill → stub →
gate sequence, run for keeps.

Ask, with `AskUserQuestion`, whether to hand off to `/tutor` for a
comprehension pass on this walkthrough now, or stop here. Don't perform that
check yourself either way — inviting it and stopping are the only two things
this step does.
