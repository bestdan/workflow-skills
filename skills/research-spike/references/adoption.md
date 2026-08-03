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

## 1a. Name the decisions the spike exists to unblock — before backfilling

**This step is not optional and not cosmetic: the backfill cannot validate
without it.** Every question carries `blocks:`, which must name a decision id
that exists in `decisions.md` or the sentinel `none: <reason>`. Naming a
decision that isn't there fails validation — deliberately, it is the
"track that did not exist" bug prevented for decisions. So a backfill
attempted against an empty `decisions.md` can only produce questions that
block `none:`, which quietly throws away the thing the roll-up and `status`
exist to compute.

Expect this to be harder than it sounds, and expect it to be informative.
A repo that has been tracking questions in prose usually has **one implicit
decision, unnamed**, encoded as a section heading — the first adoption's
source doc sorted its questions under "Blocking the ceiling" and "Not
blocking", which names one decision by implication and leaves every other
one unwritten. Making those explicit took three decisions where the prose
had gestured at one.

That is a finding in its own right, and the same failure this instrument
exists to catch, one level up: **a question can gate something that has no
address either.** Report it the way step 3 says to report the stub count.

Keep them scarce. A decision per question is not a decision list, it is a
restatement of the question list, and it makes `status` useless — everything
blocks something, so nothing converges.

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

### The script is not in the adopting repo — how it reaches CI

Step 4 says "add the gate" as though the gate can just call the script. It
can, on a laptop: `$CLAUDE_PLUGIN_ROOT` resolves wherever the skill is
installed. **It cannot on a CI runner**, which has no plugin install — so a
naive `if [ -f <script> ]` guard silently no-ops and the gate reports green
having checked nothing. That is the same fail-open the validator itself is
built to refuse, relocated into the wiring.

The first adoption (below) settled this, and the pattern generalizes:
**fetch the script at a pinned commit and verify its digest before running
it.** In GitHub Actions, alongside however the repo already installs its
other pinned tools:

```yaml
env:
  RESEARCH_SPIKE_COMMIT: "<40-char commit sha>"
  RESEARCH_SPIKE_SHA256: "<sha256 of scripts/research-spike.py at that commit>"
```

```bash
curl -fsSL -o research-spike.py \
  "https://raw.githubusercontent.com/<owner>/workflow-skills/${RESEARCH_SPIKE_COMMIT}/scripts/research-spike.py"
echo "${RESEARCH_SPIKE_SHA256}  research-spike.py" | sha256sum -c -
```

Two rules that came out of getting this wrong:

- **Fetch into a scratch directory, not the workspace.** `--root` points at
  the checkout the script is about to scan; dropping an untracked `.py`
  inside that tree is how a checker ends up reading itself.
- **Do not resolve the script from a local plugin install as a fallback.**
  It is the obvious shortcut and it reintroduces version drift: the
  maintainer's laptop runs whatever version is installed while CI runs the
  pin, so the two disagree about what a valid tree is. **Identity matters
  more than location** — a commit plus a digest makes an out-of-tree script a
  versioned dependency, and that is what makes it gate-worthy at all.

The cost to state honestly in the adopting repo's own check contract: the
gate is now the repo's check script **plus** this job, and this job does not
run locally. A violation is found at push time.

Note what the pin also buys: if the pinned commit ever becomes unreachable,
the fetch fails and the gate **blocks** rather than passing. That is the
correct direction — a gate that cannot verify itself must not pass — but it
is a hard dependency on that URL resolving, and an adopter should know it
before it surprises them.

## 5. The advisory-tier generalization — and its one hard limit

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

### `validate` never goes in that lane — this is where the first adoption went wrong

**The generalization above applies to `suggest` and to nothing else.** Read
quickly it sounds like a principle about checks in general, and the first
adopter over-applied it exactly that way: having built an advisory lane for
`suggest`, they put `validate --strict` in it too, contradicting step 4. The
argument felt principled — `validate` bundles three checks behind one flag,
and only destination resolution is a property any commit can break, so why
fail an unrelated PR because someone forgot to re-run `write-ledger`? Two
independent consults rejected it, and both gave the same two reasons:

1. **There is no unrelated PR.** Coverage and ledger freshness are derived
   from the records, and the records change only in a commit that edits
   them. Both already fail exactly the author who caused them. The PR being
   taxed for someone else's lapse cannot occur.
2. **"It's only an authoring lapse" does not distinguish these from the rest
   of any real gate.** A formatter check is nothing but "someone forgot to
   run the formatter," and it blocks. A stale ledger is a stale generated
   file. That the remedy is one mechanical command named in the error is an
   argument for failing on it, not against.

And the demotion inverted the point. **Coverage is the check that carries
this instrument's entire purpose** — that no commit closes a question while
losing the work closing it created. Destination resolution only stops an
already-recorded obligation being orphaned by a later rename. An adopter who
gates on destinations alone has enforced the weaker property and waved
through the stated one.

So: **`validate --strict` blocks. `suggest` reports. The boundary falls
between the two subcommands, never inside `validate`.** If a repo puts
`suggest` in an advisory job, put the `continue-on-error` (or equivalent) on
that **step**, not the job, so it cannot silently come to cover `validate`
sitting above it.

## Prove the gate fails, before trusting it

A gate observed only passing is indistinguishable from a gate that is inert.
Before an adoption is finished, break the tree on purpose and watch it fail:

- Delete a stub card an obligation points at → destination resolution errors.
- Remove a question section's `obligation` block → coverage errors, with
  "question is 'answered' while its section declares no obligations."
- Edit a record without re-running `write-ledger` → `--strict` errors on the
  stale ledger.

Then revert. In the first adoption all three were confirmed locally, and the
coverage break was additionally pushed to CI to confirm the merge actually
locked — which is the only way to learn whether the repo's branch protection
lists the new job as a **required** context. It usually does not: adding a
blocking job to a workflow does not make it required, and a job that is not
required shows red while the merge button stays enabled.

## This repo's own status

`workflow-skills` has not run this playbook on itself. The plan that built
this skill deliberately deferred that to whoever initializes the first live
project here — see `dev_docs/research_spike.md` for the engineering record
and `scripts/check.sh`'s header comment for the reasoning against gating on
a tree that doesn't exist yet.

**The playbook has now been run elsewhere, once.** `bestdan/aiutopilot`
adopted it in August 2026, migrating a bespoke predecessor of this mechanism
onto it. What that adoption found — including the two steps this file was
missing, and the one place the adopter deviated from step 4 and was wrong to
— is recorded under "First adoption" in
[`../../../dev_docs/research_spike.md`](../../../dev_docs/research_spike.md).
