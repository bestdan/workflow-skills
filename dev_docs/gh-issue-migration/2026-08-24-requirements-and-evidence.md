# Tracker migration off Linear — grilling outcome

**Date:** 2026-08-24
**Status:** Requirements settled. Research phase not yet started.
**Scope:** Replacing Linear as the task tracker for solo/hobby development across
`finplan`, `workflow-skills`, `aiutopilot` (currently `linear` handler) and
`dotfiles`, `gregan_finances` (currently `gh-issue` handler).

**Revision note:** §3 decisions 8, 11 and 16 were **reopened and reversed**
after the first pass, when two facts surfaced late: nightly cloud routines as a
consumer class, and the fact that the user _does_ use GitHub's web UI.
Decisions 17–18 were **added**, not reversed. §11 records what the first pass
got wrong.

---

## 1. Why this came up

Three complaints, in the user's words:

1. Linear "incorrectly auto-closes issues based on references inside PRs all the time."
2. The 250-item cap on the free plan is hit constantly.
3. The web UI is rarely used, and mostly for meta settings.

Complaint 3 is specific to **Linear's** UI. The user does use GitHub's web UI.
This distinction was missed on the first pass and changed the Projects v2
decision (§5).

## 2. Measured baseline

Evidence: 310 Claude Code session logs (2026-07-27 → 2026-08-24, ~13,700 user
messages) plus direct queries against the live Linear workspace.

### How the tracker is actually used

| Linear MCP op                  | calls in 28 days |
| ------------------------------ | ---------------- |
| `save_issue`                   | 155              |
| `get_issue`                    | 79               |
| `save_comment`                 | 56               |
| `list_issues`                  | 44               |
| `list_teams` / `list_projects` | 18               |

_Single snapshot, 2026-08-24T15:33Z, over 310 log files. An earlier snapshot
7.5 h before gave 140 / 76 / 56 / 40 across 298 files. **The counts moved while
nobody was working locally** — corroborating finding 2 below._

No Linear web-UI sessions appear. This is a write-heavy, agent-driven API
workload.

`/co-review` is the dominant workflow verb (37 typed invocations), far ahead of
any task command. The task loop itself is largely automated: `/add-task` (7),
`/find-false-closures` (5), `/promote-tasks` (2), `/sweep-for-complete` (1).

**These counts are a floor, not a measurement.** See §7 finding 2 — unattended
cloud routines write to the tracker and do not appear in local session logs.

### Volume

| measure                                  | value                                                      |
| ---------------------------------------- | ---------------------------------------------------------- |
| issues created, last 4 months            | 781 (85 / 227 / 351 / 118 by month)                        |
| run rate                                 | **mean 195/month**, median 172, one 351 spike; ~2,340/year |
| already archived                         | 513 (66%)                                                  |
| active at time of measurement            | 268, against a 250 cap                                     |
| terminal-state issues sitting unarchived | 0                                                          |

The cap is not a hygiene failure. Archiving is already run to exhaustion, and
the cap is consumed entirely by open work.

_Two caveats on these figures._ The monthly series covers 2026-05 → 2026-08;
**the first and last months are partial**, so the mean understates the current
rate. And the state split (134 backlog + 115 unstarted + 15 started = **264**)
came from a query minutes before the 781/513 census that yields **268** active —
four issues were created between the two reads.

### Which Linear features are load-bearing

Across all 781 issues:

| feature                  | usage                                      |
| ------------------------ | ------------------------------------------ |
| priority set             | 97%                                        |
| labels set               | 81%                                        |
| estimate set             | 72%                                        |
| GitHub attachment linked | 42%                                        |
| ≥1 comment               | 55%                                        |
| any relation             | 15% (178 `blocks` endpoints, 72 `related`) |
| sub-issue (has parent)   | 10% (77)                                   |

`list_issues` scoping (of 44): **22 project-scoped (50%)**, 16 team-scoped
(36%), 6 neither (14%).

**Dependency edges do not cross project boundaries.** Of **90 distinct
`blocks` edges** examined, 89 are same-project and 1 crosses
(`FinPlanTools Backlog` → `Scenarios`). Per-repo state therefore does not
endanger the dependency graph.

_Unit note:_ the 178 above counts each edge from **both** endpoints
(`relations` + `inverseRelations`); the 90 counts each edge **once**. The two
should differ by exactly 2×, and 178 vs 180 does not quite reconcile — a
two-endpoint discrepancy that is unexplained and too small to affect the
conclusion.

### Cost of the incumbent

The Linear handler is **~222 KB** of skill instruction across 12 files, versus
~51 KB (6 files) for `gh-issue` and ~38 KB (4 files) for `repo-pr`. Four commands are Linear-only:
`/sweep-for-complete`, `/reconcile-tasks`, `/find-false-closures`,
`/sweep-for-archive`.

Two exist solely to work around the complaints above. `linear-archive.md` cannot
use the MCP at all (no archive mutation), so it drops to raw GraphQL with a
1Password-held API key.

---

## 3. Settled decisions

| #  | Decision           | Settled as                                                                                                                                                                                                                                                                               |
| -- | ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | Motive             | Architecture **and** cost. Will not pay for a solo hobby tracker.                                                                                                                                                                                                                        |
| 2  | Auto-close         | A genuine Linear defect. Auto-close is **already disabled** and it still fires. Specific failure: a PR body linking other issues as _related_ causes Linear to close those too.                                                                                                          |
| 3  | Capacity           | Real requirement — "my ideas outrun my agents." The tracker is an idea inbox. Permanent deletion of archived work is acceptable.                                                                                                                                                         |
| 4  | Hosting            | Hosted, web-reachable from three machines. Self-hosting permitted but disfavoured.                                                                                                                                                                                                       |
| 5  | Local-in-git       | **Ruled out** — a routine clones only its _configured_ repos into an ephemeral per-session VM, so no consumer can see the whole 40-repo backlog, and concurrent writers would resolve conflicts by merge. (The original rationale — "no clone, no filesystem" — was **wrong**; see §11.) |
| 6  | State locality     | Per-repo state is fine. Cross-repo **read** from a single agent is a strong want.                                                                                                                                                                                                        |
| 7  | Hard requirements  | First-class block-chain dependencies; estimates; labels as automation gates; headless agent access.                                                                                                                                                                                      |
| 8  | Agent interface    | CLI **or** MCP, provided it is headless-friendly. **Not a filter** — see §4, routines have a shell and a pre-installed authenticated `gh`.                                                                                                                                               |
| 9  | Consumers          | The user, local agents, and nightly cloud routines. No other humans.                                                                                                                                                                                                                     |
| 10 | Repo-less ideas    | Do not occur. No inbox repo needed.                                                                                                                                                                                                                                                      |
| 11 | Completion         | **An open PR is not a done task. A merged PR may complete a task.** On GitHub the open PR _is_ the review gate and merge is the act of accepting it, so **merge is completion** via closing keywords. See §6.                                                                            |
| 12 | Migration scope    | Keep all ~268 active issues. The 513 archived may be lost — **but export first** (§7 finding 4).                                                                                                                                                                                         |
| 13 | Rebuild budget     | One solid project, tilting open-ended.                                                                                                                                                                                                                                                   |
| 14 | Pilot              | `workflow-skills`, built to `linear`-level depth. `finplan` stays on Linear as the control.                                                                                                                                                                                              |
| 15 | Research shape     | Wide scan first, then deep on the winner. firecrawl authorized.                                                                                                                                                                                                                          |
| 16 | Custom fields      | Labels are the source of truth (§5). A Projects v2 board may be added as a **read-only view** — nothing authoritative stored there.                                                                                                                                                      |
| 17 | PR ↔ issue linkage | Issue number in the branch name, in a fixed position after the prefix so both `bestdan/…` and `claude/…` parse.                                                                                                                                                                          |
| 18 | Claiming           | Create-only ref creation (`POST /git/refs`), per the existing `claim-lock.md`. See §8.                                                                                                                                                                                                   |

### Rejected along the way

- **Pay for Linear Standard.** Lifts the cap, leaves the defect.
- **Fix auto-close by PR-body convention.** Already attempted; the setting is
  off and it still fires.
- **Self-hosted OSS as the presumptive answer.** Not eliminated from the scan,
  but disfavoured.
- **Storing status/estimate/priority in Projects v2 fields.** See §5.
- **Assignee or labels as the claim lock.** See §8 — provably broken for
  same-account racers.

---

## 4. Verified facts

All checked directly, not asserted from memory.

**GitHub Issues has native dependencies and sub-issues.**

```
GET repos/bestdan/workflow-skills/issues/413/dependencies/blocked_by  → [] (200)
GET repos/bestdan/workflow-skills/issues/413/dependencies/blocking    → [] (200)
GET repos/bestdan/workflow-skills/issues/413/sub_issues               → [] (200)
GET orgs/bestdan/issue-types                                          → 404
```

Issue _types_ are org-only and unavailable on a personal account; labels cover
that role.

**GitHub closes only on explicit keywords.** The only closing keywords are
`close`/`closes`/`closed`, `fix`/`fixes`/`fixed`, `resolve`/`resolves`/`resolved`.
A bare `#123` creates a cross-reference and does **not** close. Cross-repository
closing requires `owner/repo#123` with a keyword. This resolves complaint 1 —
the Linear failure mode cannot occur.

Keyword-based linking is also what GitHub **documents as the supported
mechanism**. Manual linking exists via the Development sidebar (≤10 issues, same
repo, write permission) but its closure behaviour and API support are
undocumented, so it is not usable headless.

**Closing keywords also work in commit messages**, closing the issue when the
commit reaches the default branch. Any rule about keywords has two enforcement
surfaces, not one.

**Cross-references are durable and queryable.** `finplan#1102` carries a
`cross-referenced` timeline event. Timeline is REST-only.

**Cross-repo read needs no aggregation layer.**

```
$ gh search issues --owner bestdan --state open
bestdan/finplan#1107 · bestdan/gregan_finances#68 · bestdan/dotfiles#532
```

One call across all 40 repos.

**`gh` rejects undefined labels.**

```
$ gh issue edit 541 -R bestdan/dotfiles --add-label zz-junk-e7
failed to update …: 'zz-junk-e7' not found       (exit 1)
```

Neither created nor applied. Pre-creating the legal vocabulary yields a real
enum with a loud failure mode.

**…but raw REST _does_ auto-create them.** Measured: `POST /issues/{n}/labels`
with `zz-undefined-label` **created the label**. So the enum guarantee holds for
the `gh` CLI path only. **Any raw REST write must validate label names against
the vocabulary first** — including the atomic PATCH below, which is itself a raw
REST write.

**The GitHub MCP server has no issue-dependencies tool.** Toolsets include
`issues`, `labels`, `pull_requests`, `projects`, `sub_issues`. Confirmed tools:
`issue_write`, `issue_read`, `list_issues`, `search_issues`, `label_write`,
`sub_issue_write`, `list_pull_requests`. Dependencies and timeline are absent.
**This does not matter** — see the routines finding below.

**Cloud routines have a shell and an authenticated `gh`.**

- _"The session can run shell commands, use skills committed to the cloned
  repository, and call any connectors you include."_
- _"GitHub's `gh` CLI is pre-installed… `gh` reads `GH_TOKEN` automatically, so
  you don't need to run `gh auth login`."_
- `api.github.com`, `github.com`, `codeload.github.com`, `gist.github.com` are
  on the **default Trusted allowlist**. No network change needed.
- Leave `GH_TOKEN` unset and the GitHub proxy authenticates outbound requests;
  the credential never enters the container. **No PAT required.**
- _Caveat:_ with the proxy path, a script reading `$GITHUB_TOKEN` directly gets
  the literal placeholder `proxy-injected`. `gh` works; hand-rolled HTTP does
  not. Setting your own `GH_TOKEN` fixes that, but env vars are _"visible to
  anyone who uses the environment"_ — not a secret store.
- Routines push to `claude/`-prefixed branches, _"which are always accepted"_.
  Other branches are checked and rejected if protected, if someone else has an
  open PR from them, or if they carry another author's commits.

**Repo posture.**

| repo              | visibility | active rulesets                  | effective requirement                                            |
| ----------------- | ---------- | -------------------------------- | ---------------------------------------------------------------- |
| `workflow-skills` | public     | `protect-main`, `Copilot review` | PR required · **0 approvals** · owner may bypass                 |
| `finplan`         | private    | `protect-main`, `Copilot review` | status checks + code quality + non-fast-forward · **no PR rule** |
| `dotfiles`        | private    | `protect main`, `Copilot review` | —                                                                |

(The `dotfiles` ruleset really is named `protect main`, without the hyphen the
other two use — an inconsistency in the repos, not in this document.)

Two consequences: the legacy `/branches/main/protection` endpoint returns 404
on all three (they use **rulesets**, not legacy protection — 404 is not "no
protection"); and **"nothing merges before review" is a convention, not an
enforced rule.** `required_approving_review_count` is 0 and cannot be raised
usefully, since GitHub forbids approving your own PR. `finplan` additionally
has no `pull_request` rule, so direct pushes to `main` pass if checks do —
probably unintended, and unrelated to this migration.

Actions are enabled on all repos. `workflow-skills` is **public**, so Actions
minutes there are free and unlimited.

### Empirical spike, 2026-08-24

Run against two throwaway private repos. Six of eight passed.

| # | test                                                   | result                                                                   |
| - | ------------------------------------------------------ | ------------------------------------------------------------------------ |
| A | raw REST auto-creates an undefined label               | **FAIL** — it created `zz-undefined-label`; the enum holds only for `gh` |
| B | `gh issue edit --add-label X --remove-label Y` atomic? | **FAIL** — 8 HTTP request lines; multiple mutating calls                 |
| C | full-set `PATCH` replaces labels atomically            | **PASS** — one request, exact set applied                                |
| D | `POST /git/refs` rejects the second caller             | **PASS** — `201 Created`, then `422 Reference already exists`            |
| E | `blocked_by` writes and reads back on a personal repo  | **PASS** — real edge                                                     |
| F | sub-issues write and read back                         | **PASS**                                                                 |
| G | `gh issue transfer` preserves dependencies             | **PASS** for dependencies · **labels were lost**                         |
| H | rate limits                                            | core **5,000/h**, graphql **5,000/h**, search **30/min**                 |

Four consequences, each load-bearing:

1. **A and B interact badly, and C is only half a fix.** B rules out incremental
   `--add-label`/`--remove-label` edits, pushing every state transition onto the
   atomic PATCH of C. But the PATCH _is_ a raw REST write, so by A it will
   happily invent any label name handed to it. **The write helper must validate
   against the vocabulary and then PATCH.** Neither half is sufficient alone.
2. **The claim lock is confirmed on the real API** (D), same-account racers
   included. This is the direct fix for agents racing the same issue.
3. **Hard requirement 7 clears** (E). This was the result most likely to
   overturn the whole direction — dependencies could have been org-only like
   issue _types_. They are not.
4. **Transfer drops labels** (G). Almost certainly because the destination repo
   had none of the schema labels defined and GitHub discards labels absent from
   the target — consistent with per-repo label namespaces. **Inferred, not
   proven**: re-run with the labels pre-provisioned in the target to confirm.
   Either way, since labels _are_ the state model, a transfer without
   pre-provisioned labels **destroys every issue's status, priority and
   estimate**.

**The binding rate limit is search, not core.** At ~195 issues/month even 20
calls per issue is trivial against 5,000/h. But cross-repo read (decision 6)
runs on `gh search issues`, capped at **30/min** — that is what could bite under
`/auto-pilot` fan-out, and it is the number to size against.

---

## 5. The state model

GitHub Issues has only `open`/`closed` and no native estimate, priority, or
lifecycle-status field. Fable was consulted and recommended **labels on the
issue** over Projects v2 custom fields:

- One API surface. `gh issue list --label status:3_started` _is_ the `wip_limit`
  query. Projects v2 makes every such query a REST read plus a GraphQL join over
  project items, with item-ID bookkeeping `gh` covers only partially.
- Labels return inline in the ordinary issue payload and filter natively.
- Atomicity is a wash — neither offers compare-and-swap.

Its original third argument — _"the payoff is a board and you never look at a
board"_ — rested on a false premise and is withdrawn. The user does use GitHub's
UI. The conclusion survives on the first two arguments, with a board added as a
**read-only view** (auto-added issues, label columns) rather than a store.

### Final schema

| purpose                  | labels                                                                                                               | invariant                      |
| ------------------------ | -------------------------------------------------------------------------------------------------------------------- | ------------------------------ |
| status                   | `status:0_untriaged` · `status:1_needs_refinement` · `status:2_ready` · `status:3_started` · `status:4_needs_review` | open issue has **exactly one** |
| routing                  | `auto:eligible` · `auto:human-review-needed`                                                                         | open issue has **exactly one** |
| priority                 | `prio:0` · `prio:1` · `prio:2` · `prio:3`                                                                            | at most one                    |
| estimate                 | `est:1` · `est:2` · `est:3` · `est:5` · `est:8` · `est:13`                                                           | at most one                    |
| dependencies, sub-issues | native GitHub endpoints — no labels                                                                                  | —                              |

Design notes:

- **Numeric status ordering** (the user's contribution) makes the reconciler's
  "most advanced wins" repair a plain `max()` rather than a precedence table.
- **`status:` / `prio:` / `est:` prefixes** replaced `s:` / `p:` / `e:`, which
  the user misread (`s:` as _size_).
- **Explicit routing pair** (the user's contribution): an issue merely lacking
  `auto:eligible` is indistinguishable from an untriaged one. An explicit pair
  makes unclassified issues visibly unclassified and fail safe out of both
  queues.
- **"Done" is implicit** — a closed issue with no status rung. A done issue
  **keeps** its estimate and priority labels.
- `auto-eligible` → `auto:eligible` is a real change to existing tooling, and
  across 40 repos it is a migration, not a config edit (§7 finding 6).

### Write labels as a complete set, never incrementally

```
PATCH /repos/{owner}/{repo}/issues/{n}
{ "labels": ["status:4_needs_review", "prio:1", "est:3", "auto:eligible"] }
```

One request, whole set replaced, atomic. **This structurally eliminates the
double-status-label race** — there is no window in which two status labels
coexist, so the reconciler rule below becomes an audit rather than a
load-bearing repair.

**Measured: `gh issue edit --add-label X --remove-label Y` is NOT atomic** — 8
HTTP request lines, multiple mutating calls. It must not be used for state
transitions.

**But the PATCH needs a guard.** Raw REST auto-creates unknown labels (§4 test
A), so the helper must validate every name against the vocabulary _before_
writing. The shape is: validate → PATCH full set. The local
`sandbox-network-guard` blocks non-GET `gh api`, so this needs an allowlist
entry locally; Actions and routines have no such hook.

### Reconciler obligations

`/reconcile-tasks` gains three rules:

1. Repair a double-labelled issue by `max()` on the status number.
2. Flag any open issue missing a `status:` or `auto:` label.
3. Flag any issue closed while it never carried `status:4_needs_review` —
   the backstop against a stray `Closes #123` (§6).

---

## 6. Completion and PR↔issue linkage

**Merge is completion, via closing keywords.** The reasoning that settles this:

- On GitHub the review gate is **pre-merge**. The open PR _is_ the
  `needs_review` state; merging is the act of accepting it.
- `/auto-pilot`'s own contract is _"Nothing is merged or tracker-completed
  unattended."_ A merge is therefore always a human act.
- Closing keywords are GitHub's documented, supported mechanism, and their
  semantics are explicit rather than heuristic.

Consequences:

- **`/sweep-for-complete` becomes unnecessary.** It existed because Linear could
  not be trusted to close correctly.
- **Issue numbers still go in branch names** — cheap, gives traceability, and
  works on the MCP path via `list_pull_requests`, which returns the head branch.
- **Residual risk:** a stray or mistaken `Closes #123` closes an undelivered
  issue. Mitigated by reconciler rule 3 above, and by the fact that the keyword
  is visible in the PR body during the review that precedes merge — though
  note §4: that review is a **convention, not an enforced rule**, so reconciler
  rule 3 is the only mechanical backstop.

### Moving an issue to `needs_review` when a PR opens

Ranked:

1. **The agent that opens the PR sets the label in the same step.** `/deliver-task`
   already opens the PR and already writes to the issue. Zero new
   infrastructure; identical locally and in a routine. Primary path.
2. **A GitHub Action on `pull_request` as backstop** — parse the issue number
   from the branch, set the label. Catches PRs opened outside the loop. Free on
   the public pilot repo.
3. **A routine with a GitHub trigger** — supported, but spins up a full session
   per PR and draws on the daily routine cap. Wrong tool for a one-field write.

Two wrinkles:

- **Draft PRs.** House convention makes `bestdan`-owned PRs ready-for-review but
  `gregan_finances` always draft. Trigger on `ready_for_review`, or filter
  `is draft = false` — a draft PR is not `needs_review`.
- **The reverse transition.** A PR closed without merging should return the
  issue to `status:3_started`, or it sits in `needs_review` with no open PR
  forever.

---

## 7. Adversarial review findings

Fable reviewed the first draft of this document. Two claims were measurable and
were checked.

1. **PR↔issue linkage was undesigned.** Resolved in §6 — this was the highest-
   damage finding and it forced the completion model to be settled properly.
2. **The baseline undercounts writes — CONFIRMED.** 140 issues were created in
   the log window against 140 `save_issue` calls, which cover creates _and_
   updates. Updates certainly occurred, so some creates happened outside local
   session logs. The cause is decision 9: cloud routines are unattended writers
   invisible to local logs. Re-derive the write rate from tracker-side audit
   data before any rate-limit analysis.
3. **Grouping dimension missing.** Just over half of reads are project-scoped, and
   Linear _projects_ have no row in the schema. Milestones are the likely
   mapping; a project→milestone mapping is needed for migration either way.
4. **Export the 513 before deleting.** Deletion was approved as a _storage_
   question, but those keys are baked into branch names and commit messages
   (e.g. `bestdan/ss-earnings-record-task-6`). One GraphQL call with the key
   already held turns a permanent provenance hole into a file.
5. **Cross-repo dependency edges — REFUTED.** 89 of 90 `blocks` edges are
   same-project (§2). Per-repo state is safe.
6. **Label provisioning across 40 repos has no owner — now upgraded to
   load-bearing.** Seventeen labels per repo, per-repo namespaces. Two spike
   results sharpen this: an unprovisioned repo breaks `gh`-path writes outright,
   and **issue transfer silently drops labels the target repo lacks** (test G),
   which for this schema means losing an issue's entire state. A checked-in
   `labels.yml` plus an idempotent sync script is a prerequisite for migration,
   not hygiene.
7. **Image attachments probably cannot migrate headlessly.** Fable claims GitHub
   has no API for uploading issue images. **Unverified.** If true, Linear-hosted
   screenshots in comments die with the workspace unless re-homed into a repo.
8. **Pilot confound (accepted).** `workflow-skills` is both instrument and
   subject, so handler bugs and tracker friction will be indistinguishable.
   Acceptable for a hobby; do not read the comparison as controlled.

---

## 8. Claiming — already solved, needs porting

`commands/handlers/claim-lock.md` (shared by the `jira` and `gh-issue` handlers)
already defines the atomic primitive:

```bash
gh api --method POST "repos/<repo>/git/refs" \
  -f "ref=refs/heads/task/<KEY>" -f "sha=$base_sha"
```

`POST /git/refs` **creates or fails** — it never updates. A second caller gets
`422 Reference already exists` regardless of sha, so exactly one wins **even
when both sessions authenticate as the same account**. 201 = claim held;
422 = claim lost, do not touch the issue.

Two traps it documents, both directly relevant to the reported symptom of
agents racing for the same issue:

- **Assignee cannot be the lock.** Two sessions as the same user write the
  identical account id, read it back, and both conclude they won.
- **`git push` is not a CAS.** Measured: two racers cutting from the same base
  push the _identical sha_, so the loser gets `Everything up-to-date` and
  **exits 0**. `--force-with-lease` does not rescue it — git short-circuits on
  nothing-to-update before evaluating the lease.

Labels and assignee remain the **human-visible** claim marker; they do not
decide the race. `status:3_started` displays a claim rather than being one.

The claim must be acquired **before** the work, not at PR time — that is what
turns a TOCTOU probe into a lock.

### Three things to reconcile

1. **Branch naming collides three ways.** `claim-lock.md` uses `task/<KEY>`;
   decision 17 puts the issue number in the branch name; the house rule requires
   a `bestdan/` prefix. `bestdan/task-1234` satisfies all three and is
   deterministic — important, because both racers must compute the _same_ name,
   which a title-derived slug does not guarantee.
2. **The documented fallback may be obsolete.** `claim-lock.md` says web
   sessions are pinned to `claude/<session>` and cannot create `task/<KEY>`,
   degrading to a weaker comment-token election. Current routines docs say
   non-`claude/` pushes _are_ accepted under three conditions — and the claim is
   a `gh api` call, not a `git push`, so the push-check may not apply at all.
   **Needs a real test.**
3. **The mechanism exists; the lifecycle around it does not.**
   `gh-issue-claim.md` is 16 KB against `linear-claim.md`'s 42 KB. The observed
   racing is most likely because `gh-issue` was never built out to `linear`-level
   depth — which is exactly what the pilot is for.

---

## 9. What changes in the tooling

**Deleted, not ported:**

- `/find-false-closures` — the bug it works around cannot occur on GitHub.
- `/sweep-for-complete` — merge is completion (§6).
- `/sweep-for-archive` and most of `linear-archive.md` — no cap, no forced
  archive.

**Ported with changes:** `/add-task`, `/list-tasks`, `/promote-tasks`,
`/do-tasks`, `/deliver-task`, `/complete-task`, `/reconcile-tasks` (plus three
new rules), `/reoptimize-tasks` (upgradeable from report-only now that native
dependency edges exist).

**New, small:** a `labels.yml` + idempotent sync script; a `pull_request`
Action for the `needs_review` transition and its reverse.

---

## 10. Open items

- ~~raw REST label auto-create~~ — **RESOLVED: it does** (§4 test A).
- ~~`gh issue edit` atomicity~~ — **RESOLVED: not atomic**, 8 requests (test B).
- ~~dependencies on a personal repo~~ — **RESOLVED: they work** (test E).
- **Unverified:** whether GitHub supports uploading issue image attachments via
  API (§7 finding 7).
- **Untested:** whether `POST /git/refs` succeeds from inside a routine (§8).
  Now the single highest-leverage unknown left.
- **Inferred, not proven:** that transfer drops labels only when the target repo
  lacks them (test G). Two-minute re-test with labels pre-provisioned.
- **Undecided:** project→milestone mapping for migration (§7 finding 3).
- **Unrelated but worth a look:** `finplan` has no `pull_request` ruleset rule.

## 10b. Amendment — routine access, measured 2026-08-24 (later same day)

Three of the routine facts in §4 were sourced from documentation, not measurement.
Probed directly in a Claude Code cloud routine (environment
`env_017t5zvhK6AhSx23Hn8xP5mN`, repo `bestdan/workflow-skills`). The controlled
run is the 17:21Z one, whose read control succeeded.

| §4 claim                                                  | measured                                                                                                                            |
| --------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| routines have "a pre-installed `gh`"                      | **FALSE** — not on `PATH`, and absent from `find / -maxdepth 4 -name gh -type f`. `git`, `curl`, `python3`, `jq` are present        |
| `api.github.com` allowlisted, proxy auth, "no PAT needed" | **TRUE for reads** — an unauthenticated-looking `curl` returns `login: bestdan`                                                     |
| (not claimed) GitHub API **writes**                       | **BLOCKED** — `POST`/`DELETE` on `/git/refs` return `403 Write access to this GitHub API path is not permitted through this proxy.` |
| (not claimed) `git push`                                  | creating a ref **succeeds**; `git push --delete` returns a **403 RPC error**                                                        |
| `GH_TOKEN` / `GITHUB_TOKEN` hold the proxy placeholder    | **TRUE** — both are the literal `proxy-injected`                                                                                    |

### What this overturns

- **Decision 8 loses its stated reason.** "CLI **or** MCP … **not a filter** — routines
  have a shell and a pre-installed authenticated `gh`" is false in its second half.
- **§4's dismissal of the GitHub MCP dependency gap collapses.** "This does not matter
  — routines have `gh`" was the whole argument, and it is void.
- **Hard requirement 7 (headless agent access) is not established for the routine
  consumer class** named in decision 9. It still holds for local agents, which are the
  bulk of the measured baseline.

This is the same failure mode §11 already records twice, now measured rather than
suspected.

### What it does NOT overturn

§12's decisive argument is structural — every candidate clearing the dependency
requirement is a separate system from where the code lives, re-creating the
integration boundary that caused complaint 1. Routine write access does not bear on
that, and neither does the local-agent workload.

### The open question this creates

The tempting inference — "Linear wins here, its MCP connector is attached to
routines" — is **not** supported. Runs at 17:16Z and 17:18Z both logged an
`mcp_auth_required` event, and the Workflow tooling warns that interactively
authenticated MCP servers may be absent in headless runs. If MCP connectors do not
authenticate unattended, Linear's routine path is broken too and this ceases to be a
tracker differentiator — it becomes a fact about routines.

**Unresolved, and it gates tasks 4-6:** can a cloud routine write to _any_ tracker?
Note that no GitHub MCP connector is currently connected to this account, so that
path would need connecting at claude.ai/customize/connectors before it could even be
tested.

### CORRECTION, later 2026-08-24 — a routine CAN write GitHub issues

A routine created in the web UI, with connectors authorized interactively, produced a
clean controlled result that overturns two claims made above and one made in the first
version of `claim-lock.md`.

| question                                         | answer                                                                                                                                                                                                                                                   |
| ------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Do MCP connectors authenticate unattended?       | **YES.** Linear `list_teams` returned the real `PreThink` team.                                                                                                                                                                                          |
| Is a GitHub MCP connector available to routines? | **YES** — a `github` server with `mcp__github__*` tools. An earlier note here claiming none was connected was wrong; it inferred absence from the /schedule connector list, which is not the full set.                                                   |
| Can a routine write GitHub issues?               | **YES.** `mcp__github__get_me` resolves to `bestdan`; `add_issue_comment` against nonexistent issue 999999999 returns a clean **404 from GitHub**, not a proxy 403 — the write reached GitHub.                                                           |
| Is raw `curl` to api.github.com usable?          | **NO.** It carries no credential. Reads returned `403 GitHub access is not enabled for this session` in this run, though an earlier run read `login: bestdan` — behaviour is **inconsistent across runs**, so treat the raw path as unusable either way. |

**So decision 8 survives, on new grounds.** The headless interface for routines is
**MCP**, not the `gh` CLI. Hard requirement 7's "headless agent access" holds for issue
writes.

**But §4's dismissal of the GitHub MCP dependency gap now matters more, not less.** The
credentialed channel is MCP, so anything MCP does not expose is unreachable from a
routine. Known toolsets are `issues`, `labels`, `pull_requests`, `projects`,
`sub_issues`. Not yet enumerated, and each one gates real work:

- a **git-data / create-ref** tool — without it the atomic claim lock is unavailable
  unattended (task 4)
- a **dependencies** tool (`blocked_by`/`blocking`) — without it hard requirement 7's
  _dependency_ half is not satisfiable from a routine (tasks 4, 8)
- a **delete-comment** tool — without it the fallback election cannot retract (task 4)
- whether `issue_write` replaces labels as a **complete set** or merges them — this
  decides whether the validate-then-PATCH guarantee holds on the routine path at all
  (task 3's helper shells out to `gh api PATCH`, so it is **local-only** today)
- a **milestone** tool — bears on open question 3

**Consequence for task 3 as built.** `gh-label-write.py` uses `gh api --method PATCH`.
That works locally and cannot work in a routine. Either the handler documents the write
helper as local-only and routines take an MCP path, or the helper grows an MCP branch.
Do not assume the atomicity guarantee carries over until `issue_write`'s label
semantics are checked.

### GitHub MCP connector inventory, 2026-08-24 — 58 tools enumerated

The connector is the only credentialed channel in a routine, so its surface is the
routine's capability ceiling. Enumerated in full rather than sampled.

| capability                                   | tool                                                    | consequence                                                                                                 |
| -------------------------------------------- | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| create a ref/branch                          | `create_branch` (`owner`,`repo`,`branch`,`from_branch`) | the acquire primitive **exists**; whether it rejects a duplicate is **UNTESTED** and decides task 4         |
| delete a ref/branch                          | **ABSENT**                                              | a ref created in a routine can only be removed locally                                                      |
| issue dependencies (`blocked_by`/`blocking`) | **ABSENT**                                              | see below — the sharpest finding                                                                            |
| sub-issues                                   | `sub_issue_write`                                       | present, and a _different_ feature from dependencies                                                        |
| set an issue's labels                        | `issue_write` (`labels`)                                | semantics **UNCLEAR** — the schema says only "Labels to apply to this issue", with no replace/merge wording |
| delete an issue comment                      | **ABSENT**                                              | the fallback election cannot retract from a routine                                                         |
| set an issue's milestone                     | `issue_write` (`milestone`, number)                     | present                                                                                                     |
| create a milestone                           | **ABSENT**                                              | milestones must be created locally — bears on open question 3                                               |
| transfer an issue                            | **ABSENT**                                              | task 9's migration is local-only; acceptable, it is a one-off                                               |

**Hard requirement 7 is now split, and half of it fails unattended.** The requirement
is "first-class block-chain dependencies". Native GitHub dependencies were measured
working (§4 test E) — but **only from a local session**. From a routine there is no MCP
dependency tool and raw HTTP carries no credential, so dependency edges can be neither
read nor written unattended. Anything that schedules on the dependency graph — task 8,
and `/auto-pilot`'s ready-task selection — is therefore a **local-only** capability.
This was not known when decision 9 named routines as a consumer class.

**`issue_write`'s label semantics are the other live risk.** Task 3's whole guarantee
is validate-then-replace, and `gh-label-write.py` gets that from `gh api PATCH`, whose
replace semantics are documented. The MCP wrapper's are not, and the underlying REST
behaviour must **not** be assumed to carry through a wrapper that does not state it. If
`issue_write` merges rather than replaces, the routine path cannot express a state
transition atomically and needs its own design. Settle this empirically before task 5.

### RESOLVED — the claim lock works in a routine; the RELEASE does not

`mcp__github__create_branch` was tested against the live API from inside a routine:
first call created `refs/heads/zz/probe-b-20260824` at `4b6379aa`, the immediate repeat
returned `failed to create branch: Reference already exists`. Create-only with a
rejected duplicate — the same election semantics as `POST /git/refs`. Probe branch
deleted locally afterwards; the repo is clean.

**So decision 18 (create-only ref creation as the claim primitive) survives for
routines**, on a different tool than it was written for. Local sessions use
`gh api --method POST .../git/refs`; routines use `create_branch`. Note the tool takes
`from_branch`, not a sha, so it cannot pin an exact base — harmless for the election,
since the lock is the name.

**The asymmetry is the finding.** A routine can acquire and cannot release: no
delete-branch/delete-ref tool in the connector, and `git push --delete` returns a 403
RPC error there. A routine that acquires and then bails strands a lock ref that every
later session reads as a live claim. Task 4 must handle this explicitly — acquire only
when intending to run to completion, report any stranded ref, and rely on a local
stale-lock sweep as the backstop. This is a new obligation the plan did not have.

### RESOLVED — validate-then-replace is the rule on BOTH channels

Measured 2026-08-24 from inside a routine, against a throwaway issue (#414, created
and deleted afterwards along with the label the test conjured):

| question                                      | result                                                                                                      |
| --------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Can a routine create an issue?                | **YES** — `issue_write` method `create` made #414 with three labels                                         |
| Does `issue_write` REPLACE or MERGE labels?   | **REPLACE** — updated with `["bug"]`, read back as exactly `["bug"]`; `question` and `wontfix` were dropped |
| Does the MCP path auto-create unknown labels? | **YES** — `zz-mcp-undefined-20260824` was created (color `ededed`, empty description) and attached          |

**Task 3's design generalises unchanged.** The two facts that forced validate-then-PATCH
on the `gh api` path hold identically on the MCP path: the write is a full-set replace,
and an unvalidated name silently enters the repo's namespace. So the rule is not an
artefact of one API — it is the rule, and the routine path needs the same vocabulary
check before it writes.

**What still differs is the implementation, not the rule.** `gh-label-write.py` shells
out to `gh api --method PATCH` and is therefore local-only. Task 5 must either give the
handler an MCP branch that reuses `labels.yml` for validation, or document that
unattended writes go through a separate path with the same guarantee. Do not let the
routine path reach `issue_write` without validating first.

**Also settled:** a routine can create issues, so `/add-task` works unattended.

### A failed probe, recorded so it is not mistaken for evidence

An 18:21Z run attempting the same question for **issue** writes is **inconclusive**
and must not be cited. Two defects: the probe sent form-encoded bodies (no
`Content-Type: application/json`), and every call including the read control returned
`403 GitHub access is not enabled for this session` — an Anthropic-proxy error, not a
GitHub one (its `documentation_url` points at docs.anthropic.com). The same read had
succeeded an hour earlier through the same proxy, so GitHub access in that
environment is not stable across runs. Cause unknown.

## 11. What the first pass got wrong

Recorded because the corrections were load-bearing.

- **"`gh` works identically in a cloud routine"** was asserted without checking.
  It happens to be true, but only because the GitHub proxy authenticates `gh`;
  the claim was unverified when made.
- **"MCP is not a hard filter"** was argued from a false premise. It survives,
  but because routines have a shell — not because MCP coverage was sufficient.
  The GitHub MCP has no dependency tools.
- **"Closing keywords are banned"** was over-derived from the user's "a merged
  PR _may_ complete a task." The user's actual position is that Linear's failure
  motivates wanting robustness, not that GitHub's mechanism is unsafe.
- **"Reviewing before merging loses the gate"** was backwards. Pre-merge review
  _is_ the gate; merge is it firing.
- **"You never look at a board"** was the interviewer's premise, not the user's,
  and it invalidated one of Fable's three arguments against Projects v2.
- **The run-rate "correction" was itself wrong.** The first pass said
  ~200/month; a reviewer called that an overstatement of "a ~140 median" and
  that was adopted without checking. Recomputed: **mean 195, median 172**. The
  original figure was right and the correction was the error — adopted because
  it arrived as a confident-sounding nitpick.
- **Decision 5's rationale** claimed cloud consumers have no clone or
  filesystem. Routines clone their configured repos and have a shell. The
  conclusion survives on different grounds; the reason did not.

## 12. Falsification scan (2026-08-24)

Decision 15 called for a wide scan. It was run **narrow and adversarial**: one
question per candidate — _does it offer a native blocking edge on a free tier
without re-creating a cross-system integration boundary?_

| candidate                     | native `blocks` edge                                                                                | verdict                                                                                                                                                                                                          |
| ----------------------------- | --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **GitLab**                    | **Premium/Ultimate only.** Free gets `relates to` only — tier badge reads `Tier: Premium, Ultimate` | **Eliminated.** Fails hard requirement 7 on the free tier.                                                                                                                                                       |
| **Jira Free**                 | Yes — native issue links                                                                            | Passes on features. 10 users, 2 GB storage, 100 emails/day, every user an admin, no permission customisation, no audit log. No issue cap found. Fails on the boundary argument below.                            |
| **Gitea / Forgejo**           | Yes — documented REST endpoint `/repos/{owner}/{repo}/issues/{index}/dependencies`                  | Passes on features. Self-hosted ⇒ fails decision 4.                                                                                                                                                              |
| **Codeberg** (hosted Forgejo) | Yes, same as Forgejo                                                                                | Passes on features, fails on the boundary. **Also flagged:** Codeberg's Terms of Use reportedly restrict LLM-generated content — a potential outright disqualifier for an agent-driven workflow, and unverified. |
| **Shortcut**                  | Not established (page blocked)                                                                      | Not chased — fails the boundary argument regardless.                                                                                                                                                             |

**The decisive finding is structural, not a feature comparison.** Complaint 1 is
not "Linear has a bug"; it is _"an integration between two systems guesses at
intent."_ Every candidate that clears the dependency requirement is a **separate
system from where the code lives**, and therefore re-creates precisely the
integration surface that produced the original failure. GitHub Issues is the
only option that deletes the boundary instead of re-implementing it.

**The most informative single result:** GitLab — the most obvious competitor,
and one that would _also_ have removed the boundary by hosting code and issues
together — puts the one feature named as a hard requirement behind a paywall.
That is strong evidence that GitHub's free tier is unusually generous on exactly
the axis that matters here.

**Scan cost:** 12 firecrawl credits plus two free doc fetches. The wide survey
was not run; the narrowing it was meant to falsify survived the one question
that could have overturned it.

---

## 13. Next phase

Wide scan against the settled filters — self-hosted OSS (Plane, Huly, Vikunja,
Forgejo, OpenProject), hosted free tiers (GitLab, Jira, Shortcut, Codeberg), and
non-tracker options — then a deep adversarial pass on whatever survives.

The adversarial pass should target, at minimum:

- Item caps that bite at ~2,400/year.
- ~~rate limits~~ — **measured**: core 5,000/h, graphql 5,000/h, search
  **30/min**. Search is the binding constraint, because cross-repo read runs on
  it. Size `/auto-pilot` fan-out against 30/min.
- ~~whether transfer preserves dependency edges~~ — **measured: it does**;
  labels do not survive unless pre-provisioned (§4 test G).
- Whether any cross-repo closing path could reintroduce the auto-close failure.
- **Whether `POST /git/refs` succeeds inside a routine** — this single result
  decides whether unattended agents get the strong claim lock or the degraded
  comment election.

**Bias declared, and now tested.** The interview narrowed hard toward GitHub
Issues over six rounds. §12 was the falsification pass; the narrowing survived
it, and GitLab — the strongest alternative — was eliminated on the user's own
hard requirement rather than on the interviewer's preference. The residual risk
is no longer "was a better option missed" but "does the build hit something the
spike did not probe."

**Recommendation: proceed to `/plan-with-docs`.** The first plan task should be
the routine `POST /git/refs` test, which is the last unknown with the power to
change an implementation decision.
