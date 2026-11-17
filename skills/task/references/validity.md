# Ticket validity

How to decide whether an open backlog ticket still describes a real problem:
an evidence ladder, cheapest rung first, and the verdict it ends in. Read-only;
nothing here writes to a tracker.

**Boundary with `/sweep-for-complete`:** a started ticket whose own PR merged
is the sweep's (`${CLAUDE_PLUGIN_ROOT}/commands/sweep-for-complete.md`); this
ladder is for every other open ticket.

The most expensive mistake is a false `fixed`: the ticket closes and the bug
stays. So `unsure` is a full verdict, not a failure, and two rules hold on
every rung:

- **Never conclude `fixed` or `obsolete` from absence.** Code you cannot find
  is `unsure`. `obsolete` needs positive evidence: the PR or commit that
  removed the thing.
- **Never conclude `valid` where you could not find the code.** That is
  `unsure` too.

Paths below are under the plugin root. If `$CLAUDE_PLUGIN_ROOT` is unset and a
path doesn't resolve, Glob `**/commands/handlers/<file>`.

## 1. Resolve the repo

Take the first that names one: a repo or URL in the ticket body, the ticket's
component, its parent epic, or the code search itself (a distinctive string
that exists in exactly one candidate repo). No repo identifiable → `unsure`,
with "no repo" as the note. Every rung below runs against that repo's default
branch.

## 2. The ladder

Stop at the first conclusive rung. Record each rung you ran in `evidence`.

1. **The ticket's own PR.** Which PR counts as the ticket's own is defined,
   and resolved, elsewhere; use it, don't re-derive it:
   - Jira: the ownership note and §2–4 of
     `${CLAUDE_PLUGIN_ROOT}/commands/handlers/jira-sweep-complete.md`:
     `${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/jira-pr-sources.py`
     gathers the candidate PRs, and `linear-pr-resolve.py` (below) resolves
     them.
   - Linear: §3–4 of
     `${CLAUDE_PLUGIN_ROOT}/commands/handlers/linear-sweep-complete.md`, run
     through `${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-pr-resolve.py`.

     The resolver does not filter on state, so a backlog ticket's rows go
     through it unchanged; only the sweeps' issue queries are scoped to
     started tickets.
   - gh-issue: there is no sweep. The own PR carries `Closes #<n>` or comes
     from the claim branch (`${CLAUDE_PLUGIN_ROOT}/commands/handlers/gh-issue-claim.md`,
     **Branch name** and **PR**). Such a PR merged on an issue still open
     means the auto-close failed: apply the `fixed` rule below, and say in
     the note that `/complete-task` should close the issue.

   A `merged` PR into the default branch whose change is still present there
   (the touched `file:line` read at `origin/HEAD`, with the commit named, and
   not reverted) → `fixed`.
   A merged PR that touches other code is a lead for rung 3. `unresolved`
   means a probe failed, never "no PR": go on, and say so in the note.

2. **Search PRs and commits** for the ticket key and its distinctive terms:
   an error string, endpoint, flag or function name. For the **key**, run
   `python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/garden-checks.py" key-search --key <key> --repo <owner/name> --checkout <path>`
   rather than searching by hand: GitHub search splits `AB-12` into tokens and
   matches the bare `AB`, and git's extended regex has no `\b`, so both miss.
   It prints the PRs and the `origin/HEAD` commits carrying the key as a whole
   token; exit `4` means a search failed, which is not "no hit". For the
   **terms**, `gh search prs "<term>" --repo <repo>`, `git log --all -i --grep
   '<term>'`, and `git log -S '<string>'` for the commit that added or removed
   a string. A hit here is a
   lead, not proof: verify it on rung 3. The exception is `obsolete`: a commit
   that removed the feature the ticket is about, and is reachable from
   `origin/HEAD` (`git merge-base --is-ancestor <sha> origin/HEAD`), is the
   positive evidence that verdict needs. A removal found only on another
   branch stays a lead for rung 3.

3. **Read the code on the default branch.** Does the code path, flag,
   endpoint or error string still exist, and does it still behave as the
   ticket describes? Search `origin/HEAD` (after a fetch), not a stale
   checkout, and name each `file:line` read with the commit it was read at.
   Still there and still behaving so → `valid`. Changed so the described
   behaviour cannot occur, with the change traced to a PR or commit (`git
   blame`, `git log -L`) → `fixed`. Gone, with the removing commit from rung
   2 reachable from `origin/HEAD` (`git merge-base --is-ancestor <sha>
   origin/HEAD`) → `obsolete`. Not found, changed with no traceable cause, or
   removed only by a commit `origin/HEAD` does not contain → `unsure`.

4. **Production signal** (optional; bugs only; only when the calling command
   or the repo's config enables it). Datadog logs or a Sentry issue for the
   described error in a recent window. The error still occurring supports
   a rung-3 `valid`; on its own it leaves `unsure`. Its absence is absence: it never makes `fixed` on its own.

## 3. Duplicates

Use the handler's existing duplicate rule rather than a new one:

- Linear: **Dimension 4 — Duplicates / overlap** in
  `${CLAUDE_PLUGIN_ROOT}/commands/handlers/linear-reoptimize.md`.
- gh-issue: **Dimension 4 — Duplicates / overlap** in
  `${CLAUDE_PLUGIN_ROOT}/commands/handlers/gh-issue-reoptimize.md`.
- Jira: the `twg-jira` skill's `references/duplicates.md`, shipped with the
  `twg` skills, not this plugin.

A match that rule confirms → `duplicate`, with the other ticket's link as the
evidence. A suspected match it does not confirm is a note, not a verdict.

Run it after the ladder, and only when the ladder ended `valid` or `unsure`:
`fixed` and `obsolete` stand, and a confirmed `duplicate` takes precedence
over `valid` and `unsure`.

## 4. Verdicts

| Verdict     | Means                                                 | Needs                                                 |
| ----------- | ----------------------------------------------------- | ----------------------------------------------------- |
| `valid`     | The problem still exists in the current code.         | A `file:line` showing it, read on the default branch. |
| `fixed`     | The problem was solved, here or under another ticket. | The PR or commit, plus the `file:line` it changed.    |
| `duplicate` | Another ticket covers the same work.                  | A match the handler's duplicate rule confirms.        |
| `obsolete`  | The thing it refers to no longer exists.              | The PR or commit that removed it.                     |
| `unsure`    | The evidence does not settle it.                      | What was tried, and what would settle it.             |

`confidence` is `high` (the evidence settles it on its own), `medium` (it
needs one inference a reader can check), or `low`. A `fixed` that is not
`high` is reported as `unsure`, with the lead as evidence. Every `fixed` needs
evidence a human can check in under a minute: a link to the PR or commit and
the line it changed, not a narrative.

One object per ticket:

```json
{
  "ticket": "PROJ-123",
  "repo": "acme/widgets",
  "verdict": "fixed",
  "confidence": "high",
  "rung": 1,
  "evidence": [
    "https://github.com/acme/widgets/pull/412 (merged 2026-03-02)",
    "app/models/widget.rb:40 @ 9f3c2e1"
  ],
  "note": "PR #412 removes the nil branch the ticket reports."
}
```

`repo` is `null` when none was identifiable. `rung` is the ladder rung that
concluded, or `null` for `duplicate` and when none did.
