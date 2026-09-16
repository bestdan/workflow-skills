---
title: Graduate durable decisions to dev_docs and delete the plan folder
priority: low
size: 2
status: new
created: 2026-08-24
source_branch: bestdan/gh-issue-migration-plan
parent: gh_migration
is_blocked_by: gh_migration_task_10
related_files:
  - dev_docs/
tags: [cleanup, docs]
---

← [gh_migration_plan.md](../gh_migration_plan.md)

# Graduate durable decisions to dev_docs and delete the plan folder

## Context

The plan folder is temporary scaffolding. The durable material is the
architecture and the reasoning behind it — particularly the parts that are
non-obvious and were established empirically.

## Task

Write `dev_docs/gh_issue_task_loop.md` carrying:

- The label schema and the three invariants.
- **Validate-then-PATCH**, and why neither half works alone (the two spike
  failures).
- Why the claim lock is ref creation and not assignee or `git push` — including
  the measured `Everything up-to-date` exit-0 trap.
- Merge-as-completion, and the fact that the review gate is convention rather
  than enforcement.
- Label provisioning as a migration prerequisite, because transfer drops
  unprovisioned labels.
- Which Linear commands were retired and which remain while any repo is on
  Linear.

Then delete `dev_docs/tasks/gh_migration_plan/`.

> **That path is now only the untracked original, and deleting it does NOT dispose
> of the plan docs.** Verified 2026-09-13: it still exists in the main checkout, 8
> entries, 0 tracked, gitignored, carrying its own stale `HANDOFF.md`. The live
> plan moved to `dev_docs/gh-issue-migration/` — 18 files, tracked, on draft
> **PR #441** — specifically so it would have git history. So this step is still
> correct as written (delete the stale copy, and it is worth doing early: an
> untracked plan copy has misled a session here twice), but it leaves the real
> question open.
>
> **The open question: what happens to the tracked copy.** PR #441 is "never
> merged" by design, so those files never reach `main` on their own. Deleting them
> would discard the history the move bought. **Decide deliberately between merging
> #441 and closing it, leaving the record on an abandoned branch** — both are
> defensible; drift is not. This task is where that decision belongs, since it owns
> "graduate the durable material".

**Only retire the Linear handler and its four Linear-only commands once no repo
is still on Linear.** If `finplan` remains, they stay.

> **`linear-import.py` is the case this rule does not cover, and it is now DECIDED:
> remove it.** Owner's call, 2026-09-15. It is ~2,500 lines of one-shot migration tooling
> that every installed plugin user carries, and it was kept only against the possibility
> that task 10 said _extend_. It said _keep_. **No future use is known, so it goes** —
> along with `linear-verify.py`, `linear-successor.py`, `linear-export.py` and
> `_linear_auth.py` if nothing else imports them.
>
> **The recovery path is git history, and that is the whole reason this is a cheap
> decision.** Nothing is lost that a `git log --diff-filter=D` and a checkout cannot
> restore. If `finplan` is ever migrated, the assets come back from the commit that
> deleted them — which is why the delete commit's message must name them explicitly
> rather than saying "remove migration tooling".
>
> **Two checks before deleting, because the rule above is not the same rule.** The Linear
> **handler** and its four Linear-only commands **stay** — `finplan` is still on Linear.
> These five assets are migration tooling, not loop tooling. **Verify that rather than
> inheriting it from this sentence:** grep the handlers, commands and scripts for each
> asset name, and check `scripts/check.sh`'s test list for the `test-linear-*.sh` pairs
> that must go with them. An asset something in the day-to-day path still calls is not
> migration tooling, whatever this note says.
>
> **`linear-export.py` is the one to look at hardest.** It is the only one of the five
> with a plausible non-migration use (dumping a Linear workspace), and `_linear_auth.py`
> exists because two assets share it — so if either survives the grep, the pair may need
> to.

**`finplan` is not scheduled to migrate, and there are no firm plans either way.**
(Owner, 2026-09-15.) Not-now rather than never — which is precisely why the deletion above
is defensible: reversing it is a checkout, not a rewrite.

## Acceptance Criteria

**Code-enforced**

- `dev_docs/gh_issue_task_loop.md` exists and covers all six points
- `dev_docs/tasks/gh_migration_plan/` no longer exists
- No Linear command is deleted while any repo's `.task-config.yml` still says `handler: linear`

**User-run**

- Read the graduated doc cold and confirm it explains the design without reference to the plan folder
