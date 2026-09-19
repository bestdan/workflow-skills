# Why the task loop left Linear for GitHub Issues

**Decided 2026-08-24**, from an interview plus measurement against the live Linear
workspace, 310 Claude Code session logs, and a spike against the real GitHub API.
`workflow-skills` migrated 2026-09-13 and the pilot gate returned **keep** on 2026-09-15.

This records **why the choice was made and what would reopen it**. How the resulting loop
works is [`../gh_issue_task_loop.md`](../gh_issue_task_loop.md), which is live and kept
current; this file is a dated record and is allowed to age.

**Scope: `workflow-skills` only.** `finplan` and `aiutopilot` stay on Linear, by decision
rather than deferral (owner, 2026-09-18). The interview was framed around migrating all
three; only one was answered yes, and the `linear` handler is a permanent part of this
plugin rather than a shim awaiting retirement.

## The complaint

Three, in the user's words:

1. Linear "incorrectly auto-closes issues based on references inside PRs all the time."
2. The 250-item cap on the free plan is hit constantly.
3. The Linear web UI is rarely used, and mostly for meta settings.

Complaint 1 is the load-bearing one, and **it is not "Linear has a bug."** It is that _an
integration between two systems guesses at intent_. Auto-close was already disabled and
still fired: a PR body linking other issues as _related_ closed them too. That framing is
what decided the answer — see "The alternatives" below.

## The baseline

Measured 2026-08-24 across 781 issues and 310 session logs (~13,700 user messages).

| measure                                  | value                                                     |
| ---------------------------------------- | --------------------------------------------------------- |
| issues created, last 4 months            | 781                                                       |
| run rate                                 | **mean 195/month**, median 172; ~2,340/year               |
| already archived                         | 513 (66%)                                                 |
| active at measurement                    | 268, against a **250 cap**                                |
| terminal-state issues sitting unarchived | 0                                                         |
| Linear handler size                      | ~222 KB across 12 files, vs ~51 KB for the `gh-issue` one |

**The cap is not a hygiene failure.** Archiving was already run to exhaustion and the cap
was consumed entirely by open work. On the free plan it counts every non-archived issue of
any state, so completing or cancelling frees nothing — only archiving does.

The workload is **write-heavy, agent-driven, and headless**: no Linear web-UI sessions
appear at all. Note the session-log counts are a floor rather than a measurement —
unattended cloud routines write to the tracker and never appear in local logs.

Feature usage across those 781 issues, which is what the replacement had to carry:
priority 97%, labels 81%, estimate 72%, ≥1 comment 55%, GitHub attachment 42%, any
relation 15%, sub-issue 10%.

**Dependency edges do not cross project boundaries** — of 90 distinct `blocks` edges, 89
are same-project. That is why per-repo state was safe to adopt, and it is the measurement
to re-run if the answer ever needs revisiting.

## The decisions

| #  | Decision           | Settled as                                                                                                                                      |
| -- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| 1  | Motive             | Architecture **and** cost. Will not pay for a solo hobby tracker.                                                                               |
| 2  | Capacity           | A real requirement — "my ideas outrun my agents." The tracker is an idea inbox; permanent loss of archived work is acceptable.                  |
| 3  | Hosting            | Hosted, web-reachable from three machines. Self-hosting permitted but disfavoured.                                                              |
| 4  | Hard requirements  | First-class block-chain dependencies; estimates; labels as automation gates; headless agent access.                                             |
| 5  | State locality     | Per-repo state is fine; cross-repo **read** from one agent is a strong want. A tracker in git was ruled out — no consumer sees all 40 repos.    |
| 6  | Consumers          | The user, local agents, and nightly cloud routines. No other humans.                                                                            |
| 7  | Agent interface    | CLI **or** MCP, provided it is headless-friendly. Not a filter.                                                                                 |
| 8  | Completion         | An open PR is not a done task; a merged PR may complete one. On GitHub the pre-merge review gate **is** the mechanism, so merge is completion.  |
| 9  | State model        | Labels are the source of truth. A Projects v2 board may be added as a **read-only view** — nothing authoritative stored there.                  |
| 10 | Claiming           | Create-only ref creation (`POST /git/refs`), per `commands/handlers/claim-lock.md`. Assignee and labels are a human-visible marker, not a lock. |
| 11 | PR ↔ issue linkage | Issue number in the branch name, in a fixed position after the prefix so both `bestdan/…` and `claude/…` parse.                                 |
| 12 | Migration scope    | Keep the active issues; the archived may be lost, but **export first** — the keys are baked into branch names and commit subjects.              |
| 13 | Pilot              | `workflow-skills`, built to `linear`-level depth, accepting that it is both instrument and subject.                                             |

Decisions 8 through 11 became the design; the reasoning behind each is in
[`../gh_issue_task_loop.md`](../gh_issue_task_loop.md) rather than repeated here.

## What was checked before committing

A spike against two throwaway private repos, 2026-08-24. Six of eight passed, and the two
failures are the ones that shaped the build.

| test                                                   | result                                                          |
| ------------------------------------------------------ | --------------------------------------------------------------- |
| raw REST auto-creates an undefined label               | **FAIL** — it created it; the enum holds for the `gh` CLI only  |
| `gh issue edit --add-label X --remove-label Y` atomic? | **FAIL** — 8 HTTP request lines, several mutating               |
| full-set `PATCH` replaces labels atomically            | PASS — one request, exact set applied                           |
| `POST /git/refs` rejects the second caller             | PASS — `201`, then `422 Reference already exists`               |
| `blocked_by` writes and reads back on a personal repo  | PASS — a real edge                                              |
| sub-issues write and read back                         | PASS                                                            |
| `gh issue transfer` preserves dependencies             | PASS for dependencies · **labels were lost**                    |
| rate limits                                            | core 5,000/h, graphql 5,000/h, **search 30/min** — search binds |

Four consequences, each of which outlived the spike:

- **The two failures interact**, which is why the write helper validates and then PATCHes
  rather than doing either alone.
- **The claim lock holds against same-account racers** on the real API — the direct fix
  for the reported symptom of agents racing one issue.
- **Dependencies are not org-only**, unlike issue _types_. This was the result most likely
  to have overturned the whole direction.
- **Transfer drops labels the target repo lacks**, and labels _are_ the state model — so
  provisioning is a migration prerequisite rather than hygiene.

**Search at 30/min is the binding limit**, not core. Cross-repo read runs on search, so
that is the number to size `/auto-pilot` fan-out against.

Two things a reader should not look for here. Whether **unattended** agents can reach
GitHub is [`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md) and
[`2026-09-05-cloud-session-plugin-and-proxy.md`](2026-09-05-cloud-session-plugin-and-proxy.md),
which own that question and have been re-measured since. And **"nothing merges before
review" is a convention, not an enforced rule** — the repos use rulesets rather than legacy
protection, so `/branches/main/protection` returns 404 and that does not mean unprotected.

## The alternatives, and why each was eliminated

A narrow adversarial scan, one question per candidate: _does it offer a native blocking
edge on a free tier without re-creating a cross-system integration boundary?_

| candidate                     | native `blocks` edge      | verdict                                                        |
| ----------------------------- | ------------------------- | -------------------------------------------------------------- |
| **GitLab**                    | **Premium/Ultimate only** | Eliminated — fails the hard requirement on the free tier       |
| **Jira Free**                 | Yes                       | Passes on features; fails the boundary argument                |
| **Gitea / Forgejo**           | Yes, documented REST      | Passes on features; self-hosted, so fails the hosting decision |
| **Codeberg** (hosted Forgejo) | Yes                       | Passes on features; fails the boundary argument                |
| **Shortcut**                  | Not established           | Not chased — fails the boundary regardless                     |

**The decisive finding is structural, not a feature comparison.** Every candidate that
clears the dependency requirement is a separate system from where the code lives, and so
re-creates exactly the integration surface that produced complaint 1. GitHub Issues is the
only option that deletes the boundary instead of re-implementing it.

**The most informative single result was GitLab** — the most obvious competitor, and one
that would also have removed the boundary by hosting code and issues together. It puts the
single feature named as a hard requirement behind a paywall, which is strong evidence that
GitHub's free tier is unusually generous on exactly the axis that mattered.

**The bias was declared and tested.** The interview narrowed hard toward GitHub Issues
over six rounds; this scan was the falsification pass, and GitLab was eliminated on the
user's own hard requirement rather than on the interviewer's preference.

## Revisit when

- **A `duplicate` relation appears in a Linear export.** They carry no direction today and
  there were zero of them, which is why the crosswalk puts `related`/`similar`/`duplicate`
  on one prose footer line. One real instance reopens that contract.
- **Dependency edges start crossing project boundaries.** The 89-of-90 measurement is what
  made per-repo state safe; it is a snapshot of one workspace on one day.
- **GitHub puts issue dependencies or sub-issues behind a paid tier.** Both are free today
  and both are hard requirements. This is the GitLab failure mode, and nothing prevents it.
- **Cross-repo read outgrows 30 requests/minute.** That is the one measured ceiling with a
  plausible path to being hit.
- **Another repo is proposed for migration.** `finplan` and `aiutopilot` staying is a
  decision, so moving one is a new decision and not an extension of this one — and the
  one-shot migration tooling is gone from the tree, recoverable from the commit that
  deleted it.

Two things settled after this record was written, noted so they are not re-litigated from
it: the free-plan cap counts every non-archived issue rather than `unstarted`+`started`
(corrected in #739, and `linear-common.md` owns the figure as a dated observation because
Linear exposes no field to re-derive it); and GitHub **does** support uploading issue
attachments via API — `uploads.github.com/user-attachments/assets` accepts a bearer token
as of August 2026 — so the spike-era worry that Linear-hosted screenshots could not be
re-homed headlessly was wrong rather than merely unverified.
