# The gh-issue task loop — why it is shaped this way

The reasoning behind the `gh-issue` handler: the parts that look arbitrary until you know
what was tried. Not a usage guide — `commands/handlers/gh-issue*.md`, `claim-lock.md` and
the assets under `commands/handlers/assets/` are authoritative for behaviour. Everything
below was measured against real repos in August and September 2026.

## 1. Labels are the state model

GitHub Issues has `open`/`closed` and nothing else. Issue _types_ are org-only
(`GET orgs/bestdan/issue-types` → 404 on a personal account), so labels carry the
lifecycle. `commands/handlers/assets/labels.yml` is the **single source of the
vocabulary**:

| purpose  | namespace | values                                                                    |
| -------- | --------- | ------------------------------------------------------------------------- |
| status   | `status:` | `0_untriaged` `1_needs_refinement` `2_ready` `3_started` `4_needs_review` |
| routing  | `auto:`   | `eligible` `human-review-needed`                                          |
| priority | `prio:`   | `0` `1` `2` `3`                                                           |
| estimate | `est:`    | `1` `2` `3` `5` `8` `13`                                                  |

Dependencies and sub-issues are **not** labels — they use GitHub's native endpoints.

**The three invariants:**

1. An open issue carries exactly one `status:` and exactly one `auto:` rung.
2. An issue carries at most one `prio:` and at most one `est:`.
3. "Done" is implicit: a closed issue has neither rung and keeps its `prio:`/`est:`.
   Priority and estimate are facts about the work; `auto:` is a live instruction to a
   scheduler, so leaving one on finished work is a hazard rather than information.

Three choices that look like taste and are not:

- **Status values are numerically ordered**, so the reconciler's "most advanced wins"
  repair is a plain `max()` rather than a precedence table.
- **The routing pair is explicit.** An issue merely _lacking_ `auto:eligible` is
  indistinguishable from an untriaged one; naming both ends fails an unclassified issue
  safe out of both queues.
- **The prefixes are spelled out** because `s:`/`p:`/`e:` was misread in practice — `s:`
  as _size_.

**"Carrying a rung" means carrying one the vocabulary defines**, never merely a label
whose name starts with `status:`. A hand-typed `status:blocked` passes the prefix test, so
the issue reads as healthy while sitting in a state nothing can act on. Ask `labels.yml`;
`gh-issue-state.py`'s `carried_rungs()` is the worked example.

## 2. Writes are validate-then-PATCH, and neither half works alone

`gh-issue-state.py` validates every label name against `labels.yml` locally, exits
non-zero before any network call on an unknown name or a broken invariant, then issues
**one** `PATCH /repos/{owner}/{repo}/issues/{n}` carrying the complete `labels` array.

Two measured failures force that shape, and each closes the other's hole:

- **`gh issue edit --add-label X --remove-label Y` is not atomic** — 8 HTTP request lines,
  several mutating. Incremental edits cannot carry a state transition; there is a window
  in which two `status:` rungs coexist. That leaves the full-set PATCH as the only usable
  transition.
- **A raw REST write silently CREATES an unknown label** — `POST /issues/{n}/labels` with
  `zz-undefined-label` created it. The enum guarantee belongs to the `gh` CLI, which
  rejects one outright (`'zz-junk-e7' not found`, exit 1) — and the CLI path is the one
  just ruled out. So validation has to happen locally, first.

The rule holds on **every** channel, the GitHub MCP connector included: `issue_write`
replaces the full set the same way.

Two consequences of full-set replacement. It **structurally eliminates** the
double-status-label race, making the reconciler's repair an audit rather than a
load-bearing fix. And the helper must **carry forward** every label outside its four
namespaces — `papercut`, `follow-up`, anything a human added — or a state write silently
strips them.

## 3. The claim lock is ref creation, and cannot be anything cheaper

Two agents must not build the same issue. `POST /repos/<owner>/<repo>/git/refs` **creates
a ref or fails**: a second call returns `422 Reference already exists` regardless of the
sha it names, so exactly one caller wins whatever account each authenticates as. It runs
as the first mutating step of the claim, not at PR time an hour later — that is what turns
the pre-flight `git ls-remote` probe into a lock rather than a TOCTOU window spanning the
whole execution.

**Why not the assignee.** Two sessions authenticated as the _same_ user write the identical
account id, read it back, and both conclude they won. It cannot be repaired in place —
GitHub's issue-edit API offers no compare-and-swap. Assignee, labels and status remain the
**human-visible** claim marker; they no longer decide the race.

**Why not `git push origin <branch>`.** Measured against a real remote: two racers cutting
from the same base tip push the **identical sha**, so the loser gets `Everything
up-to-date` and **exits 0**. `--force-with-lease` does not rescue it — git short-circuits
on nothing-to-update before evaluating the lease, and also exits 0. A plain push is a
reliable compare-and-swap only when the racers push _different_ commits, which two sessions
branching from one base do not.

**The general rule:** any primitive whose success is indistinguishable from a no-op cannot
be a lock.

Fallback election and release mechanics: `commands/handlers/claim-lock.md`.

## 4. Merge is completion, and the review gate is convention

An issue completes when its PR merges, via GitHub's closing keywords (`closes`/`fixes`/
`resolves` — a bare `#123` only cross-references). Two reasons this beats a swept state:
the review gate on GitHub is **pre-merge**, so the open, **non-draft** PR _is_
`status:4_needs_review` and merging is the act of accepting it; and `/auto-pilot`'s
contract is "nothing is merged or tracker-completed unattended", which makes a merge always
a human act. The keywords themselves are GitHub's documented mechanism with **explicit
rather than heuristic** semantics — which is the whole point, since heuristic auto-close is
what made Linear untrustworthy here.

The draft qualifier is load-bearing, not pedantic: the house convention makes some repos'
PRs always draft, which is why `.github/workflows/gh-issue-pr-sync.yml` guards its `opened`
trigger on `!github.event.pull_request.draft` rather than trusting the event name.

Hence `/sweep-for-complete` is **unsupported** here and refuses by design — it existed
because Linear could not be trusted to close correctly.

> **The gate is a convention, and the doc trail hides that.** These repos use **rulesets**,
> not legacy branch protection, so `/branches/main/protection` returns **404 and that does
> not mean "unprotected"** — read the rulesets. On `workflow-skills` the effective
> requirement is: PR required, **0 approvals**, owner may bypass.
> `required_approving_review_count` cannot usefully be raised, since GitHub forbids
> approving your own PR.
>
> So the only mechanical backstop against a stray `Closes #123` retiring undelivered work
> is the reconciler rule flagging an issue closed without ever having carried
> `status:4_needs_review`. Closing keywords also fire from **commit messages**, so any rule
> about them has two enforcement surfaces.

**A bare `gh issue close` is the wrong tool.** It leaves live rungs on a closed issue,
violating invariant 3. The reconciler's row 4 repairs that, but it is a **sweep, not an
instant repair** — until it runs, a live `auto:` points a scheduler at finished work. Use
`gh-issue-state.py --done`, which never creates the violation.

## 5. Label provisioning is a prerequisite, not hygiene

Label namespaces are **per-repo**, so a rung the vocabulary defines may never have been
created on a given board — and then "does this issue carry it?" is _unanswerable_, not
answered "no". Two measured consequences:

- **`gh issue transfer` silently drops labels the target repo lacks.** Since labels _are_
  the state model, transferring into an unprovisioned repo **destroys every issue's status,
  priority and estimate**. The 2026-09-07 transfer of 28 papercut issues from
  `bestdan/dotfiles` is the live instance — seven labels had to be created on the target
  first.
- **A label scope does not vouch for provisioning.** `gh-issue.labels` separates loop
  issues from strangers; it says nothing about which rungs exist.

`gh-label-sync.py` is **dry-run by default** and idempotent, so
`python3 commands/handlers/assets/gh-label-sync.py --repo <repo>` prints a board's gap for
one `gh label list`; `--apply` closes it. Its `existing_labels()` **refuses** above 500
labels rather than truncating — read a repo's labels through that helper rather than
inheriting a silent cap.

**Guard by group, not by completeness.** A rung stays assignable while its group has any
member provisioned; only an entirely empty group voids it. Guarding on "every label exists"
turns one missing rung into a total outage.

## 6. What Linear keeps

**Nothing was retired.** The `linear` handler and every command dispatching to it stay, and
the rule is _no Linear command is deleted while any repo's `.task-config.yml` says
`handler: linear`_. **`workflow-skills` is the only repo that moved** — `finplan` and
`aiutopilot` stay on Linear by decision rather than deferral, so the handler is a permanent
part of this plugin, not a shim awaiting retirement.

`commands/task-config.md`'s capability matrix is the source of truth for the **verbs it
lists**. Three commands are not verbs in it and are **Linear-only**, each command's own
"Resolve the handler" section being authoritative, and its refusal text the home of the
reason. Gathered here only because all three turn on §4:

| command                | why it is Linear-only                                                                 |
| ---------------------- | ------------------------------------------------------------------------------------- |
| `/sweep-for-complete`  | GitHub closes the issue natively on merge                                             |
| `/find-false-closures` | GitHub closes only on an explicit keyword, so the bare-id over-close bug cannot occur |
| `/sweep-for-archive`   | composes the two above                                                                |

What **was** removed is the one-shot tooling that moved the board: `linear-export.py`,
`linear-import.py`, `linear-verify.py`, `linear-successor.py` and the shared
`_linear_auth.py`, with their test pairs. Every installed user carried them for no ongoing
purpose, and with no further repo migrating they have no remaining consumer — the verifier
and the successor tool cannot even be invoked, since they take as required arguments the
plan and mapping files one migration produced. The commit that deleted them names each
file, so recovery is a checkout rather than a rewrite.

> Worth carrying forward, since the code is gone: **`gql()` answered a GraphQL error with
> `sys.exit`**, so one bad issue ended a whole batch pass — 19 issues in, taking the
> remaining 105 with it — while the docstring two lines up claimed a failed write does not
> abort the others. The catch belongs at the call site in every sibling copying that
> pattern.

## Not here

- **Why Linear was left, and what would reopen the choice** —
  [`decisions/2026-08-24-leaving-linear-for-github-issues.md`](decisions/2026-08-24-leaving-linear-for-github-issues.md).
- **How an unattended agent reaches GitHub** —
  [`decisions/2026-08-24-routine-claim-channel.md`](decisions/2026-08-24-routine-claim-channel.md)
  and
  [`decisions/2026-09-05-cloud-session-plugin-and-proxy.md`](decisions/2026-09-05-cloud-session-plugin-and-proxy.md).
  `/auto-pilot` does not support this handler and `gh-issue.remote_batch` defaults to
  `false`; both are open work, not settled design.
- **Anything a runtime file states.** One fact, one home — this file links rather than
  copies.
