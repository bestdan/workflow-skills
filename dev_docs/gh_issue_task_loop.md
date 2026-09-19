# The gh-issue task loop — how it works and why it is shaped this way

The durable design behind the `gh-issue` handler, graduated out of the migration plan
that produced it. It carries the reasoning that is non-obvious and was established by
measurement rather than by reading documentation — the parts that look like arbitrary
choices until you know what was tried.

It is not a usage guide. The runtime prose is authoritative for behaviour:
`commands/handlers/gh-issue*.md`, `commands/handlers/claim-lock.md`, and the assets
under `commands/handlers/assets/`. What is here is why they say what they say.

Every measurement below was made against real repos in August and September 2026. Two
decision records hold the full evidence:
[`decisions/2026-08-24-routine-claim-channel.md`](decisions/2026-08-24-routine-claim-channel.md)
and
[`decisions/2026-09-05-cloud-session-plugin-and-proxy.md`](decisions/2026-09-05-cloud-session-plugin-and-proxy.md).

## 1. Labels are the state model

GitHub Issues has `open`/`closed` and nothing else — no status field, no priority, no
estimate. Issue _types_ exist but are org-only (`GET orgs/bestdan/issue-types` → 404 on a
personal account), so they cannot carry the role. The loop needs a lifecycle, so labels
carry it.

`commands/handlers/assets/labels.yml` is the **single source of the vocabulary**. Four
managed namespaces:

| purpose  | namespace | values                                                                    |
| -------- | --------- | ------------------------------------------------------------------------- |
| status   | `status:` | `0_untriaged` `1_needs_refinement` `2_ready` `3_started` `4_needs_review` |
| routing  | `auto:`   | `eligible` `human-review-needed`                                          |
| priority | `prio:`   | `0` `1` `2` `3`                                                           |
| estimate | `est:`    | `1` `2` `3` `5` `8` `13`                                                  |

Dependencies and sub-issues are **not** labels — they use GitHub's native endpoints.

### The three invariants

1. **An open issue carries exactly one `status:` and exactly one `auto:` rung.**
2. **An issue carries at most one `prio:` and at most one `est:`.**
3. **"Done" is implicit**: a closed issue has neither a `status:` nor an `auto:` rung, and
   keeps its `prio:`/`est:`. Priority and estimate are facts about the work and stay
   useful afterwards; `auto:` is a live instruction to a scheduler, so leaving one on a
   closed issue is a hazard rather than information.

Three design notes that are easy to mistake for taste:

- **Status values are numerically ordered** so the reconciler's "most advanced wins"
  repair is a plain `max()` rather than a precedence table.
- **The routing pair is explicit.** An issue merely _lacking_ `auto:eligible` is
  indistinguishable from an untriaged one. Naming both ends makes an unclassified issue
  visibly unclassified, and fails it safe out of both queues.
- **The prefixes are spelled out** (`status:`, `prio:`, `est:`) because the abbreviated
  `s:`/`p:`/`e:` form was misread in practice — `s:` as _size_.

### "Carrying a rung" means carrying one the vocabulary defines

Never merely a label whose name starts with `status:` or `auto:`. The prefix reading is
the trap: a hand-typed `status:blocked` satisfies it, so the issue reads as healthy while
sitting in a state nothing can act on. Anything asking "does this issue have a rung?" must
ask `labels.yml`. `gh-issue-state.py`'s `carried_rungs()` is the worked example.

## 2. Writes are validate-then-PATCH, and neither half works alone

The write helper (`gh-issue-state.py`) validates every label name against `labels.yml`
locally, exits non-zero before any network call if a name is unknown or an invariant
breaks, and then issues **one** `PATCH /repos/{owner}/{repo}/issues/{n}` carrying the
complete `labels` array.

That shape is forced by two measured failures, and either half on its own leaves a hole:

- **`gh issue edit --add-label X --remove-label Y` is not atomic** — measured at 8 HTTP
  request lines, several of them mutating. So incremental edits cannot carry a state
  transition; there is a window in which two `status:` rungs coexist.
- **A raw REST write silently CREATES an unknown label.** `POST /issues/{n}/labels` with
  `zz-undefined-label` created it. The enum guarantee people assume belongs to GitHub
  actually belongs to the `gh` CLI, which rejects an undefined label outright
  (`'zz-junk-e7' not found`, exit 1) — and the CLI path is exactly the one ruled out
  above.

So the atomic PATCH is the only usable transition, and the atomic PATCH is a raw REST
write that will invent whatever it is handed. Validation has to happen locally, first.
The rule holds on **every** channel, the GitHub MCP connector included — `issue_write`
replaces the full set the same way.

Two consequences that follow from the full-set replacement:

- It **structurally eliminates** the double-status-label race. The reconciler's repair
  rule is an audit, not a load-bearing fix.
- The helper owns only its four namespaces, so it must **carry forward** every other label
  it finds — `papercut`, `follow-up`, anything a human added — or a state write silently
  strips them.

## 3. The claim lock is ref creation, and cannot be anything cheaper

Two agents must not build the same issue. The primitive is
`POST /repos/<owner>/<repo>/git/refs`, which **creates a ref or fails** — a second call
for an existing ref returns `422 Reference already exists` regardless of the sha it
names. Exactly one caller wins, whatever account each session authenticates as. It runs
as the first mutating step of the claim, not at PR time an hour later, which is what turns
the pre-flight `git ls-remote` probe into a real lock rather than a TOCTOU window spanning
the whole execution.

**Why not the assignee.** Both handlers used to confirm a claim by re-reading the issue
and checking the assignee was their own account. That defeats a different user, but not a
second session authenticated as the _same_ user: both write the identical account id, both
read it back, both conclude they won, both build the issue. The check cannot be repaired
in place — GitHub's issue-edit API offers no compare-and-swap, so every read-then-write on
`assignee` has a window, and same-account racers are reading for a value identical for
both. Assignee, labels and status stay on as the **human-visible** claim marker; they no
longer decide the race.

**Why not `git push origin <branch>`.** Measured against a real remote: two racers cut the
branch from the same base tip, so they push the **identical sha** — the loser's push
reports `Everything up-to-date` and **exits 0**, and both sessions conclude they won. That
is the same both-confirm bug one layer down. `--force-with-lease` does not rescue it
either: git short-circuits on nothing-to-update before evaluating the lease, and also
exits 0. A plain push is a reliable compare-and-swap only when the racers push _different_
commits, which two sessions branching from one base do not.

**The general rule this is an instance of:** any primitive whose success is
indistinguishable from a no-op cannot be a lock.

Full mechanics, including the fallback election and release: `commands/handlers/claim-lock.md`.

## 4. Merge is completion, and the review gate is convention

An issue completes when its PR merges, via GitHub's closing keywords (`closes`/`fixes`/
`resolves` — a bare `#123` only cross-references). Three reasons this is right here rather
than a swept state:

- On GitHub the review gate is **pre-merge**. The open, **non-draft** PR _is_
  `status:4_needs_review`; merging is the act of accepting it. The draft qualifier is
  load-bearing rather than pedantic — the house convention makes some repos' PRs always
  draft, which is why `.github/workflows/gh-issue-pr-sync.yml` guards its `opened` trigger
  on `!github.event.pull_request.draft` rather than trusting the event name.
- `/auto-pilot`'s contract is "nothing is merged or tracker-completed unattended", so a
  merge is always a human act — which makes it a sound completion signal.
- Closing keywords are GitHub's documented, supported mechanism, with explicit rather than
  heuristic semantics.

Hence `/sweep-for-complete` is **unsupported** on this handler and refuses by design: it
existed because Linear could not be trusted to close correctly.

> **The review gate is a convention, not an enforced rule — and the doc trail hides
> this.** All three repos use **rulesets**, not legacy branch protection, so
> `/branches/main/protection` returns **404 and that does not mean "unprotected"**. Read
> the rulesets. On `workflow-skills` the effective requirement is: PR required,
> **0 approvals**, owner may bypass. `required_approving_review_count` cannot usefully be
> raised, because GitHub forbids approving your own PR.
>
> So the only mechanical backstop against a stray `Closes #123` closing undelivered work
> is the reconciler rule that flags an issue closed without ever having carried
> `status:4_needs_review`. Note closing keywords fire from **commit messages** too, once
> the commit reaches the default branch — any rule about keywords has two enforcement
> surfaces, not one.

**A bare `gh issue close` is the wrong tool.** It leaves live `status:`/`auto:` rungs on a
closed issue, violating invariant 3. The reconciler's row 4 does repair that — it strips
both rungs and keeps `prio:`/`est:` — but it is a **sweep, not an instant repair**, so the
issue sits in violation until someone runs it, with a live `auto:` instruction pointing a
scheduler at finished work. Use `gh-issue-state.py --done`, which never creates the
violation in the first place.

## 5. Label provisioning is a prerequisite, not hygiene

Label namespaces are **per-repo**. A rung the vocabulary defines may never have been
created on a given board, and then a check asking "does this issue carry it?" has an
_unanswerable_ question, not an answer of "no".

This is load-bearing for two reasons, and both were measured:

- **`gh issue transfer` silently drops labels the target repo lacks.** Since labels _are_
  the state model here, transferring into an unprovisioned repo **destroys every issue's
  status, priority and estimate**. The 2026-09-07 transfer of 28 papercut issues from
  `bestdan/dotfiles` is the live instance — seven labels it needed had to be created
  directly on the target.
- **A label scope does not vouch for provisioning.** `gh-issue.labels` separates loop
  issues from strangers; it says nothing about which rungs exist.

`gh-label-sync.py` is the tool. It is **dry-run by default** and idempotent, so
`python3 commands/handlers/assets/gh-label-sync.py --repo <repo>` prints a board's gap for
the cost of one `gh label list`; `--apply` closes it. Its `existing_labels()` **refuses**
above 500 labels rather than truncating — anything reading a repo's labels should go
through that helper rather than re-implementing the read and inheriting a silent cap.

**Guard by group, not by completeness.** A rung being _assignable_ still holds while its
group has any member provisioned; only an entirely empty group voids it. Guarding on
"every label exists" turns one missing rung into a total outage.

## 6. What Linear keeps

**Nothing was retired.** The `linear` handler and every command that dispatches to it stay
in place, and the rule is _no Linear command is deleted while any repo's
`.task-config.yml` says `handler: linear`_.

**`workflow-skills` is the only repo that moved.** `finplan` and `aiutopilot` stay on
Linear by decision, not by deferral — so the `linear` handler is a permanent part of this
plugin, not a compatibility shim waiting to be retired. Anyone reading the migration
record should note its scope line asks about three repos; only one of them was answered
yes.

`linear` remains the most complete handler. `commands/task-config.md` holds the capability
matrix, which is the single source of truth for the **verbs it lists** — capture, list,
promote, do, process, archive, reoptimize, reconcile. Three commands are not verbs in it,
are **Linear-only**, and refuse on every other handler; each command's own "Resolve the
handler" section is authoritative for that. The reason lives there too, in the refusal text
each one prints. The table below is a cross-reference, gathering those three refusals into
one view so they can be read against §4's merge-is-completion argument, which is what they
all turn on:

| command                | why it is Linear-only                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `/sweep-for-complete`  | GitHub closes the issue natively on merge — see §4                                    |
| `/find-false-closures` | GitHub closes only on an explicit keyword, so the bare-id over-close bug cannot occur |
| `/sweep-for-archive`   | composes the two above                                                                |

What **was** removed is the one-shot migration tooling that moved the board across —
`linear-export.py`, `linear-import.py`, `linear-verify.py`, `linear-successor.py` and the
shared `_linear_auth.py`, with their test pairs. That is migration tooling, not loop
tooling, and it was carried by every installed plugin user for no ongoing purpose. With no
further repo migrating, `linear-import.py`, `linear-verify.py` and `linear-successor.py`
have no remaining consumer at all — the latter two cannot even be invoked without the plan
and mapping files that one migration produced, both of which they take as required
arguments. The recovery path is git history: the commit that deleted them names each file,
so they come back with a checkout rather than a rewrite.

> One shape worth carrying forward from those assets, since the code is gone: **`gql()`
> answered a GraphQL error with `sys.exit`**, so a single bad issue ended a whole batch
> pass — 19 issues in, taking the remaining 105 with it — while the docstring two lines
> up claimed a failed write does not abort the others. The catch belongs at the call site
> in every sibling that copies the pattern.

## What is deliberately not here

- **The requirements and the measured record behind them** — every fact cited above, with
  the spike tables, the rejected alternatives and what the first pass got wrong — is
  [`decisions/2026-08-24-gh-issue-migration-requirements-and-evidence.md`](decisions/2026-08-24-gh-issue-migration-requirements-and-evidence.md).
  It is a dated snapshot: read it as what was true that day.
- **Unattended operation.** `/auto-pilot` does not support this handler, and
  `gh-issue.remote_batch` defaults to `false`. Both are tracked as open work, not settled
  design.
- **Anything a runtime file already says.** One fact, one home: if `labels.yml`,
  `claim-lock.md` or `task-config.md` states it, this file links rather than copies.
