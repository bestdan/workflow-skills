# gh-issue handler — /reconcile-tasks flow

Invoked from `/reconcile-tasks [--apply] [--all]` when `handler: gh-issue` is
configured. This is an **audit of the label invariants**, not the state machine
the Linear reconciler runs.

> **Why the two handlers reconcile different things.** Linear's rows exist
> because an issue's column and its PR can disagree — there is a separate
> tracker state to drift. On GitHub there is no review column: the open PR _is_
> `needs_review`, and a merge with `Closes #<n>` closes the issue natively, so
> the PR-versus-state drift Linear rows 1–4 repair has nowhere to live here. See
> `commands/handlers/assets/gh-issue-pr-sync.py`, which keeps the rung following
> the PR as it happens.
>
> What GitHub has instead is a **label** state model, and labels can be edited
> by hand in the web UI. That is the drift this file audits.

## The rule table

Three rows, closed the same way the Linear table is closed — this is not a
starting point. Do not add a fourth row, and do not widen these three.

| # | Detected drift                                                          | Action                                   |
| - | ----------------------------------------------------------------------- | ---------------------------------------- |
| 1 | **open** issue carrying two or more vocabulary `status:` labels         | keep the **highest** rung, drop the rest |
| 2 | **open** issue missing a `status:` or an `auto:` label                  | **flag only** — never assign             |
| 3 | issue **closed** although it was never labelled `status:4_needs_review` | **flag only**                            |

Rows 2 and 3 are **void** where the labels they ask about are absent from the
repo; each reports that gap once instead of flagging every issue. See below.

**Row 1 is the only row that writes**, and only under `--apply`. It keeps the
highest rung because the ladder is numbered (`0_untriaged` … `4_needs_review`)
exactly so that "highest" is a fact rather than a judgment — and keeping it is
the same forward-only doctrine the Linear rows follow: a wrong read leaves an
issue ahead of where it belongs, never retires live work.

**Row 2 refuses to guess on purpose.** Which rung a bare issue should carry is a
human's call. Defaulting it to `0_untriaged` would quietly demote started work;
defaulting it to anything else would invent state. "Carrying a rung" means
carrying one the vocabulary defines — the same reading `gh-issue-state.py`'s
`validate()` enforces. A hand-typed `status:blocked` is not a rung, and testing
the bare prefix instead would leave that issue invisible to every row, since
row 1 has no ladder position to rank it by either.

**Row 3 is the backstop for merge-as-completion.** Closing IS completion under
this schema, so a stray or mistaken `Closes #<n>` in an unrelated PR body
retires an issue that never passed review, and nothing else in the loop notices.
It reads the issue's `labeled` events, because a closed issue carries no rungs
to inspect. It reports rather than reopens: an issue can be legitimately closed
without review (abandoned, duplicate, filed by hand), and the finding carries
GitHub's `state_reason` so those are dismissible on sight.

**Rows 2 and 3 check that the labels they look for are provisioned.** Label
namespaces are per-repo, so a rung may never have been created on the board — and
then the row's question is unanswerable, not answered "no", and it reports the
gap once instead of flagging every issue. Row 1 needs no guard: it ranks labels
the issue already carries, which cannot exist unprovisioned.

Two things decided here, because a reader of this table will otherwise re-derive
them. Row 2 is guarded by **group, not completeness** — its premise is that a
rung was assignable, which still holds while its group has any member
provisioned. And the report says **absent**, never "never created": several
histories produce the same current label set, and the row cannot tell them apart.
The measurement behind all of this, and the reasoning, live in
`gh-issue-reconcile.py`'s module docstring.

> **This is an audit, not a load-bearing repair.** Every status write goes
> through `gh-issue-state.py`'s validate-then-one-PATCH path, which replaces the
> whole label set in a single request — so row 1's drift cannot arise on the
> happy path. It arises from the web UI, which is a supported way to work with
> this board.

## Steps

1. **Resolve config.** Read the `gh-issue:` block from
   `dev_docs/tasks/.task-config.yml` for `repo` (default: the current repo via
   `gh repo view --json nameWithOwner --jq .nameWithOwner`) and `labels` (the
   task-loop labels, e.g. `[follow-up]`).

2. **Scope to task-loop issues.** Pass one `--label` per configured label. This
   is not hygiene: every issue in the repo that is _not_ part of the task loop —
   a bug a user filed, a bot's issue — is missing both rungs and never reached
   review, so it is a row-2 **and** a row-3 hit. Unscoped, the report is mostly
   issues that were never wrong.

   **What the scope does not do is vouch for the labels themselves.** It
   separates loop issues from strangers; it says nothing about whether the rung a
   row asks about was ever provisioned on the repo. Those are independent, and
   reading the scope as covering both is what let row 3 flag 50 correctly-scoped
   closed issues on `bestdan/dotfiles`, which has never created
   `status:4_needs_review`. The script guards that itself — see step 4 — so this
   is a note about what to conclude from a clean report, not a step to perform.

   When `gh-issue.labels` is **empty or unset** there is no marker to scope by.
   **Stop** and report that this audit needs at least one configured label to
   tell loop issues from the rest of the repo — the same rule, for the same
   reason, as `commands/handlers/gh-issue-archive.md` step 2. `--all` is the
   explicit override: it drops the label scope and audits the whole repo, which
   is the right call on a repo whose issues are _all_ task issues.

3. **`--project` is unsupported here.** The gh-issue handler has no project
   dimension yet — milestones are the presumed mapping and that is still an open
   question on the migration plan, so guessing one would scope the audit by
   something nothing writes.

   With `--apply`, **stop**. A request to narrow that produces a write outside
   the narrowing is the one combination worth refusing, and it is the same rule
   step 2 applies to a missing label scope: a scope this handler cannot honour,
   plus a write, acts outside what the user named. Say `--project` is
   unsupported and ask them to re-run without it, or without `--apply`.

   Without `--apply` the run is read-only, so say `--project` is unsupported and
   continue at the default label scope — a report the user did not quite ask for
   costs them nothing, and refusing it would throw away a free answer.

4. **Run the audit.** Dry-run by default; `--apply` repairs row 1 and nothing
   else:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/gh-issue-reconcile.py" \
     --repo "<repo>" --label "<label1>" --label "<label2>" [--apply]
   ```

   If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
   `**/handlers/assets/gh-issue-reconcile.py`.

   The script reads the 50 most recently **created** issues per state — `gh
   issue list` orders by creation date, not by close date, so a long-lived issue
   closed yesterday can sit outside the window and go unaudited by row 3. If the
   operator asks for a wider window, add `--limit <N>` to the invocation above —
   it is a flag on this script, not on `/reconcile-tasks`, so it never arrives
   as a command argument. Mind the cost: row 3 spends one API call per
   **closed** issue in the window, so the limit is what bounds the run — unless
   `status:4_needs_review` is absent from the repo, in which case row 3 is void,
   skips those reads entirely, and the run costs three base `gh` invocations —
   one `gh label list` and one `gh issue list` per state, before any row-1 repair
   under `--apply` adds writes of its own. The script makes that `gh label list`
   before any per-issue work to decide the row's fate, and its report opens with
   the repo's provisioning gap and the `gh-label-sync.py` command that closes it.

   Row 1's repair goes through `gh-issue-state.py` — validate, then one full-set
   PATCH — so it carries `follow-up` and every other unmanaged label forward
   rather than deleting them. It does purge a label **inside** the four managed
   namespaces that `labels.yml` does not define (`prio:urgent`, invented by
   hand), which is the point of validate-then-replace — and it names each one it
   purged, because a deletion nobody reports is indistinguishable from a label
   that was never there. If the repaired set would still be illegal (row 1
   drift on an issue that is _also_ missing its `auto:` rung), the script
   **refuses that issue** and reports why. That is row 2's ban holding: it will
   not invent the missing rung to make its own write legal.

5. **Report.** The script's own output is the report — a scope line, then one
   section per row with its findings and, for row 1, whether each was repaired
   or refused. A dry run ends with an explicit "nothing changed (dry-run)".
   Fold nothing else in; unlike the Linear flow this file delegates to no other
   command.

## What this handler does NOT do

- **It never closes or reopens an issue.** Row 3 flags; the decision to reopen a
  wrongly-closed issue is the user's, and `gh-issue-state.py --reopen` is how it
  is carried out.
- **It never assigns a rung to an issue that has none.** That is row 2, and it
  is a refusal, not a gap.
- **It does not reconcile an issue against its PR.** That is
  `gh-issue-pr-sync.py`, which runs from
  `.github/workflows/gh-issue-pr-sync.yml` on every PR event rather than on a
  sweep.
- **It has no unattended channel yet.** The script shells out to `gh`, which a
  cloud routine does not have. A GitHub Actions runner does — see the
  `permissions: issues: write` note in `gh-issue-pr-sync.py` — so scheduling
  this in a workflow is possible, but no workflow ships one.
