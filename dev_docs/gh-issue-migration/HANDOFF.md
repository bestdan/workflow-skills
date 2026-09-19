# Handoff — the Linear→GitHub migration, closed out

**Redrafted 2026-09-17. The plan is COMPLETE. There is no next task.** Tasks 4–11 and
14–17 are done; task 12 was never written and gates nothing; task 13 is tracked as a
GitHub issue outside this plan and is the owner's.

**PR #441 is closed without merging.** The decision task 11 owned was _merge, close, or
graduate a curated subset_, and the answer was the subset. Two files reached `main` in
[PR #755](https://github.com/bestdan/workflow-skills/pull/755):

- **`dev_docs/gh_issue_task_loop.md`** — the durable design. Label schema and the three
  invariants, validate-then-PATCH, the claim lock, merge-as-completion, provisioning,
  what Linear keeps. **Read that, not this file**, for how the loop works.
- **`dev_docs/decisions/2026-08-24-gh-issue-migration-requirements-and-evidence.md`** —
  the measured record, unchanged and dated.

Everything else — the per-task scaffolding, the plan, this file — stays on
`origin/bestdan/gh-issue-migration` and never reaches `main`. **That branch is the only
copy. Do not delete it**, and note an abandoned-branch sweep is exactly what would.

## If you are here for the migration's outcome

`workflow-skills` runs on the `gh-issue` handler and has since 2026-09-07; `finplan` stays
on Linear. 125 Linear issues landed on GitHub on 2026-09-13 (117 created as #617–#733,
8 reopened), and all 123 writable originals are archived pointing at their successors.
That archival was the point — it took the Linear workspace from ~253 against a 250 cap to
~130.

**`workflow-skills` is the only repo that moved, and that is the end of it** (owner,
2026-09-18). `finplan` and `aiutopilot` stay on Linear **by decision, not deferral** —
superseding this file's earlier "not-now rather than never" framing. Nothing is queued
behind this plan, and the `linear` handler is a permanent part of the plugin rather than
a shim awaiting retirement.

Note the evidence record's scope line asks about all three repos, because that is how the
question was framed on 2026-08-24. Only one was answered yes; the record carries a dated
scope note saying so.

## What is still owed, and who owns it

**Nothing here is waiting on migration work.** These outlived the plan and none has an
owner inside it.

- **74 ready issues and no unattended capability.** Measured 2026-09-17: 177 open, 74 at
  `status:2_ready`, 0 started, 0 stale claim refs. `/auto-pilot` stops at
  `skills/auto-pilot/SKILL.md:135` on any handler but `linear`/`repo-pr`, and
  `gh-issue.remote_batch` is `false`, so foreground `/do-tasks` is the only working path.
  **Tracked as a GitHub issue outside this plan** — do not re-derive the case for it here.
- **A crashed claim strands its issue, and nothing sweeps it.** A session that claims and
  dies before opening a PR leaves the issue assigned and on `status:3_started`; the
  candidate query excludes it on both counts, so no later run picks it up. Recovery today
  is a human `gh issue edit`. Harmless at 0 started; it stops being harmless the moment
  anything unattended runs, and scales with the batch. **Treat it as a precondition for
  unattended operation, not a follow-up. No task owns it.**
- **`remote_batch` needs two things and the plugin-install gap is the binding one.** A
  cloud session does not install the plugin from a committed `.claude/settings.json`
  (measured 2026-09-05); a per-environment setup script closed it once, which is config the
  owner sets and a repo cannot commit. The handler's writes also go through
  `gh-issue-state.py` calling `gh`, and a routine has no `gh`.
- **`state_reason` on the close path is unowned.** `gh-issue-state.py --done` writes
  `state: closed` and nothing else, so a completed issue and an abandoned one are
  indistinguishable afterwards — and task 8's stale-versus-satisfied split reads it.
- **Two label invariants have no reconciler rule**: at most one `prio:`/`est:` (a duplicate
  stays invisible until the next write, which then refuses), and a closed issue still
  carrying live `status:`/`auto:` rungs (reachable with a bare `gh issue close`). The rule
  table is deliberately closed; `linear-verify.py` used to spot-check both and **is now
  deleted**, so nothing checks them at all.
- **The provisioning class is wider than the reconciler.** Task 17 guarded the three
  reconciler rows. Every other verb that asks whether an issue carries a rung inherits the
  same blind spot, is unaudited, and nothing detects it.
- **`sandbox-network-guard` blocks non-GET `gh api`**, so every local write costs a sandbox
  escape. Outside this repo; needs the operator.
- **Three findings filed and unowned**: **#737** (archived Linear issues reached the import
  — a finding about #511's selector, which reads state type, not archival), **#738**
  (`_body_refs.py` reads any `#<digits>` as an issue, but issues and PRs share one number
  space — ~54% false positives here), **#740** (`active_issue_quota`'s domain hardcodes the
  cap).
- **Cosmetic and unowned**: `skills/task/SKILL.md`'s flag annotations are stale, and `--remote`
  is a deprecated alias for `--cloud` in §3, §4 and `repo-pr-execute.md`. Note `--cloud`
  refuses `--print`, so a cloud session cannot be created non-interactively.

## What will bite you

### The verifier is gone, and with it the only independent check

`linear-verify.py` was deleted by task 11 along with `linear-export.py`,
`linear-import.py`, `linear-successor.py`, `_linear_auth.py` and their nine test files. It
was the only thing that could audit the imported board — 125 entries, three reads each,
about three minutes — and its standing expected failure (20 issues, `body` only, from the
`Blocked by:` footers applied after the plan was written) is now unreproducible.

**The recovery path is history, and the delete commit names every file** so
`log --diff-filter=D` finds it. That was the condition on which the deletion was cheap.

### Three provenance files, no remote, not regenerable

All in `$HOME/src/linear-export/`, **keep forever**, recorded in `linear-common.md` by
#516 step 3:

- `2026-09-13-prethink.json` — the export. 3.3 MB, 810 issues (557 archived), sha256
  `60276d3c65fb8334a74b84ea65ba880816532137fcc5dba5a513ea3723ecc4b3`.
- `2026-09-13-import-plan.json` — the plan. 451 KB, sha256
  `b080ff8585da0e10ee7ae2402858b215a55e4badbcd60323642a3a3536d4644e`.
- `2026-09-13-mapping.json` — **the Linear-key-to-GitHub-number crosswalk.** Nothing else
  on this machine holds it.

**The plan is no longer regenerable.** Regenerating re-runs the create-versus-reopen
decision, and the eight reopen targets are now open — which that decision correctly refuses
as "two live homes already exist". A regenerated plan aborts rather than reproducing this
one. Rebuilding any of the three is a fresh argument to make explicitly.

Read the 810 against `PRE-835`, not against 781: the export spans `PRE-5 … PRE-835` with 21
numbers missing to deleted issues. **An identifier is not an index.**

### Two credentials are still live on this machine

`~/.linear-key` holds a full-account OAuth token in plaintext (mode 600) and
`~/src/linear-token.py` is the throwaway that mints it. Nothing needs either now. Any coder
backend with filesystem access can read the token. Deleting both is cheap — re-minting takes
seconds from `~/.config/linear/client-credentials`. **Needs the operator.**

Note `op` cannot work over SSH here: the read returns `authorization timeout`, which means
the session lives on another machine and `op signin` will never help — not `promptError`,
which is the fixable one.

### A green gate is not evidence for a live run

`scripts/check.sh:148` excludes `scripts/test-*-live.sh` outright, and each live harness
exits **0** with a warning when no key resolves. A task can be written, tested and merged
green while the thing it exists to do has never happened once. Say "it skipped" when it
skipped. The cheaper form of the same blind spot: a second output path — a `--json` mode, a
`--cancel` gate — that a green gate and a clean live run both miss because nothing ever
invokes it. **Run every flag once before calling a task done.**

### Reading `commands/` from this branch

The plan files are the only thing this branch is authoritative about. It was 155 commits
behind `main` on 2026-09-15 and its stale `gh-issue-reoptimize.md` said GitHub Issues have
no native dependency edge — the opposite of the truth, and it nearly drove #516's whole
analysis. The branch was brought current on 2026-09-16 and is now frozen, so the trap
returns the moment `main` moves. **Read every `commands/`, `skills/` and `scripts/` file
from `main`.**

### Things that are settled — do not reopen them by accident

- **The `Blocked by:` footer stays**, and `/push-plan` keeps writing it beside the native
  edge, which it has done since v2.17.0 (#444). The argument that justified it — that an
  unattended agent could not read the edge — was measured **false** in
  [#741](https://github.com/bestdan/workflow-skills/pull/741) and
  [#751](https://github.com/bestdan/workflow-skills/pull/751): a cloud session and a
  scheduled routine both read `blocked_by` over plain `curl`. Nothing follows about removing
  the footer. Anyone who wants it gone is reopening a settled decision and should say so.
- **Reads are credentialed and writes are path-scoped.** A routine `PATCH`ed issue labels
  and got `HTTP 200`; `POST`/`DELETE` on `/git/refs` is still `403 … not permitted through
  this proxy`. So `claim-lock.md`'s "a routine cannot release" stands. The `$GH_TOKEN` in
  those environments is the literal `proxy-injected` placeholder — the capability belongs to
  the proxy path and does not travel.
- **`gh-issue.labels: []` is deliberate.** It is an AND filter on `/list-tasks`, not a stamp
  on new issues, and every issue in this repo is a task issue. The known cost is
  [#504](https://github.com/bestdan/workflow-skills/issues/504): `/archive-tasks` has no
  `--all`.
- **`duplicate` relations keep no direction**, and the export contains zero of them. If a
  later export ever carries one, that is the decision to revisit, starting with the
  milestone 1 description — which is also where the Linear-to-GitHub crosswalk contract
  lives, and the only place it is stated.
