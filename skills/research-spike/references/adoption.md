# research-spike — the adoption playbook

The mechanism is worthless empty. This is the sequence for turning it on in a
repo that has real, live deferred work — not a checklist of equally-weighted
steps, but one payload step (2) bracketed by setup (1) and cleanup (3–5). In
the reference implementation the payload step alone produced **13 records,
and 4 of them had no destination at all**. Needing stub cards to complete a
backfill **is the finding, not an inconvenience** — it is the first
measurement the instrument ever produces about the repo it was just installed
into, and it is worth reporting exactly like one.

Every invocation below carries `--root` explicitly, and it is written
**before** the subcommand — `--root` is a global option, and argparse rejects
it if it comes after (verified: `research-spike.py validate --root <dir>`
exits 2 with `unrecognized arguments: --root`). The commands are abbreviated
the same way `SKILL.md` abbreviates them: the leading
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py"` is dropped from
each line below and nothing else is.

## 1. `init` the project and its first track

```bash
--root "$(git rev-parse --show-toplevel)" init <project> --track <name>
```

Scaffolds `dev_docs/research/<project>/` with the ledger markers already in
place and a worked example inert inside an HTML comment — a freshly
initialized tree passes its own `validate` before anything real is written
into it. This step is cheap and mechanical; it is not where the finding is.

## 2. Backfill every deferral already made — the payload

Run the `backfill` procedure (`SKILL.md` procedure 4) against every existing
free-form doc where the repo has been tracking open questions or deferred
work in prose. This is a section-by-section interactive walk, not a script —
`backfill` is judgment (deciding whether a sentence is a deferral at all), so
it lives in `SKILL.md` and stays there.

**Treat the backfill as the measurement, not the formality.** The prior
question count looking flat or improving is not evidence the project is
healthy; it is evidence nobody was counting obligations. The backfill is what
converts prose that reads as though it routed work somewhere into records
that either resolve to a file that exists or don't — and the ones that don't
are the actual finding.

## 3. Stub cards for the ones with no destination — report the count out loud

Every obligation the backfill surfaces with nowhere to point gets a stub
card, created by the `defer` procedure (`SKILL.md` procedure 3) as part of
the same walk:

```card
kind: stub
superseded_when: the account track files its uid-domain-provisioning task card
```

Then point the obligation's `destination:` at the stub's path. `destination:`
is repo-relative, so the full path is required —
`dev_docs/research/<project>/tracks/<track>/obligations/<name>.md`, not the
shortened `tracks/<track>/...` form — a path that now exists, so the
obligation resolves.

**State the stub count plainly when the backfill finishes** — "N obligations
had no destination and got a stub" — not folded into a paragraph where it can
be skimmed past. The stub count _is_ the number the whole instrument exists
to surface: in the reference repo it was 4 of 13. A backfill that produces
zero stubs on a repo with real deferred history is itself worth a second
look, not a clean bill of health taken at face value.

### The stub-hygiene rule

**A stub carries `superseded_when:` — the condition of its own deletion —
because a stub that never gets superseded is a new place for work to hide.**
This is not optional decoration: `validate` rejects a `kind: stub` card with
no `superseded_when:` field. Write the condition as something checkable
("the account track files its uid-domain-provisioning task card"), not as a
vague aspiration ("eventually"). One stub in the reference implementation
existed only until two real cards appeared, and said so in its own
`superseded_when:` line — that is the shape to copy.

## 4. Wire `validate` into the repo's check, and document what was left out

Once step 1 has created a real `dev_docs/research/` tree — in the **same
PR**, not a follow-up — add the gate:

```bash
--root "$(git rev-parse --show-toplevel)" validate --strict
```

`--strict` is the organizer's tier: it fails a stale `LEDGER.md` instead of
warning, so the roll-up a plain per-track `validate` deliberately lets slide
(so a track PR is never failed by a file it doesn't own) is still checked
somewhere. Run it at organizer merge time, the same way this repo runs its
own `scripts/check.sh` at merge time.

**Document the reasoning for what was left out, in the same place the gate
itself is documented** — the check's own header comment or contract, not a
separate doc a reader has to go find. Specifically: `suggest` is deliberately
**not** part of the gate. It is an advisory lexical scan for unregistered
deferral prose ("deferred to", "gated on", …), and its scan can never fail a
build on its findings — not as an oversight, but because a lexical scan
against English prose has false positives (measured: 29 hits on the
reference tree, mostly prose _describing_ behaviour rather than deferring
work), and **a check whose false positives have nowhere legal to go must not
be able to fail the build.** (A malformed invocation — a bad `--root`, an
unrecognized flag — still exits non-zero from argument handling, same as any
other subcommand; the guarantee is about the scan's own findings, not the
process.) A repo with no baseline file, no allowlist, and no skip flag has no
legal place to put an exception, so the only sound answer is to keep the
check itself from being able to block anyone. This is the reasoning to
restate in the repo's own check contract — not just that `suggest` was
skipped, but why skipping it was correct rather than lazy.

## 5. The advisory-tier generalization

The rule above is narrower than "keep `suggest` out of CI" — it is
conditional on the adopting repo having nowhere to record an exception. **If
the adopting repo already has an advisory tier** — a CI job that reports
without gating, a non-blocking check, a warnings-only lane — that constraint
relaxes, and `suggest` can run there as a non-failing report:

```bash
--root "$(git rev-parse --show-toplevel)" suggest
```

The rule to carry across an adoption is not "`suggest` never runs in CI." It
is: **a check whose false positives have nowhere to go must not be able to
fail the build.** Where an advisory lane already exists, that condition is
already satisfied, and `suggest` belongs there the same way any other
report-only signal does.

## This repo's own status

`workflow-skills` has not run this playbook. Plan decision 2
(`dev_docs/tasks/research_spike_plan/research_spike_plan.md`) deliberately
defers the first real adoption to whoever initializes the first live
project — see `scripts/check.sh`'s header comment for the reasoning against
gating on a tree that doesn't exist yet.
