# Linear handler — /find-false-closures flow

Invoked from `/find-false-closures` when `handler: linear` is configured, and
also runnable **standalone** — the whole flow is packaged as
`commands/handlers/assets/linear-false-closures.py`, a script you can point at a
project and repo directly (see "Run it — the shipped script" below). The command
path just resolves scope + repo from config and runs that same asset.

**Shared reference:** see `commands/handlers/linear-common.md` for connection
details and the config schema this flow's project/repo scoping assumes.

## The bug it detects

This workspace's Linear/GitHub integration treats a **bare** issue id
(`PRE-123`) appearing anywhere in a merged PR's title or body as a closing
reference. A PR that merely name-drops a sibling issue therefore sweeps that
sibling to Done too, with no branch, no PR, and no code behind it — it has
done so repeatedly, which is why that integration got disabled.

`/reconcile-tasks` (`commands/handlers/linear-reconcile.md`) cannot repair
this: its rule table is deliberately **promote/complete-only and never
demotes** (see its "Bounded-rule-set doctrine" note), so a falsely-completed
issue is invisible to it — demoting a completed issue back to Todo is exactly
the kind of rule that table excludes by design. `/sweep-for-complete`
(`commands/handlers/linear-sweep-complete.md`) is immune to the bug itself
(it never parses issue ids out of PR text) but doesn't detect _pre-existing_
false closures either. This script fills that specific gap — a detect-and-
optionally-restore pass — without changing either command's rule tables.

## Detection rule

A completed issue must be **owned** by delivered work, by any of four signals:
the PR's head branch embeds the issue's identifier (regex match — not equality
on Linear's suggested `branchName`, since the real branch is routinely a
shortened form of it); one of the issue's attachment URLs points at a merged PR
(compared on canonical `owner/repo/pull/<n>` identity, so a trailing slash,
`?src=linear` query, or `/files` tab still matches); the PR title/body
**closes** the issue with a keyword (`closes PRE-123`); or the issue is a
**parent** whose sub-issues are themselves completed (a rollup shell carries no
PR of its own — its children did the work). A completed issue that matches none
of these — a PR merely name-drops the id, and no completed children — is a
**false closure**.

The closing-keyword signal covers cloud/hosted runs, where the PR head branch
frequently does not embed the Linear id and branch matching alone would miss
delivered work. The sub-issue signal covers parents that were closed once their
child slices delivered (with no branch or PR of their own).

**Archived issues are out of scope, and the reason is ordering — not review.**
The query does not pass `includeArchived`, so the scan sees only live completed
issues. What makes that safe is that a false closure is scanned **while it is
still live**, before anything archives it. It is not that archival implies
anyone looked: archival is a pure age threshold on both paths that reach it —
Linear's own team setting (a minimum of one month, unreviewed) and
`/archive-tasks --older-than N`, which retires terminal-state issues by
`completedAt` alone and asserts nothing about their correctness.

So the omission is sound only under a caller that runs detection **before**
archive, with a detection window wider than the archive threshold.
`/sweep-for-archive` guarantees exactly that by construction — it chains
`/find-false-closures` → `/sweep-for-complete` → `/archive-tasks` and carries
the verified id set between them. A scheduled pipeline must reproduce the same
ordering.

**The residual gap is any archive that runs without detection first** — a bare
`/archive-tasks` sweep, a standalone archive cron, or a night detection was
skipped while archive still ran. An issue falsely closed and then archived that
way is invisible here, and cannot be repaired even once found: the `--apply`
path has no `issueUnarchive` step. That window is narrow rather than
theoretical, and it is the thing to close if this backstop ever needs widening.

## `--prs-file` — the path for a cloud routine

`merged_prs()` gets its merged-PR list from `gh api --paginate
repos/{repo}/pulls?state=closed`. In a Claude Code **cloud routine** that call
is impossible: the session proxy refuses every repo-scoped GitHub REST call
with `HTTP 403 GitHub access is not enabled for this session`, `gh pr list` is
refused separately as GraphQL, and `gh` itself is not reliably even installed.
GitHub reads there are only available through the `mcp__github__*` MCP tools,
which the script cannot call — only the agent can.

`--prs-file PATH` is the alternative: the agent fetches the merged-PR list
itself, with `mcp__github__list_pull_requests` (state `closed`, filtered to
entries with a non-null `merged_at`), and hands it to the script as a JSON
file instead of a repo name. `--repo` and `--prs-file` are mutually
exclusive — pass exactly one. `mcp__github__list_pull_requests` is deferred in
a routine — call `ToolSearch` with `select:mcp__github__list_pull_requests`
before the first use, per `linear-sweep-complete.md`'s "claude-web
environment" note.

The MCP response is REST-shaped, not `gh`-shaped, so map fields when writing
the file: `mergedAt` from `merged_at` (snake_case there), `headRefName` from
`head.ref`, `url` from `html_url`. Call the tool with `state: closed`,
`sort: updated`, `direction: desc`; keep fetching pages until a page contains
an entry whose `updated_at` is earlier than your intended cutoff; set
`complete_since` to that cutoff; keep filtering to entries with a non-null
`merged_at`. A merge updates the PR, so `updated_at` is at or after
`merged_at` for every merged PR, which makes "first entry older than the
cutoff" a sound stopping point — stopping on `merged_at` instead is wrong,
because an old merged PR that later took a comment surfaces near the top with
an old `merged_at` and would end the scan early.

The file is a JSON **object**, not a bare list:

```json
{
  "complete_since": "2026-08-08T00:00:00Z",
  "pull_requests": [
    {
      "number": 472,
      "headRefName": "dpegan/pre-645-...",
      "url": "https://github.com/bestdan/workflow-skills/pull/472",
      "title": "...",
      "body": "...",
      "mergedAt": "2026-09-05T01:22:11Z"
    }
  ]
}
```

Each `pull_requests` entry carries the same keys `merged_prs()` itself
produces, so the rest of the script consumes them unchanged. Entries with a
null or missing `mergedAt` are dropped — only merged PRs establish ownership.

`complete_since` is the caller's assertion: **this list contains every PR
merged in this repo at or after this instant.** Set it to the oldest instant
your `mcp__github__list_pull_requests` fetch actually covers. A **later**
`complete_since` than you strictly need is the safe direction — it only costs
a few issues going unclassified. An **earlier** one is the dangerous
direction: the guard classifies an issue whenever its anchor is at or after
`complete_since`, so moving `complete_since` earlier widens the classified set
and claims coverage you may not actually have — it can hide a real owning PR
outside the window and let `--apply` un-complete delivered work.

**The coverage guard.** `--repo` paginates the _whole_ closed-PR history (see
`merged_prs()`'s own docstring), so every completed issue can be safely
classified. A `--prs-file` list is inherently a window, so that guarantee has
to be re-established explicitly: an issue is classified only if the window
provably covers its whole life. Concretely, the script compares
`complete_since` against `issue.createdAt`; an owning PR cannot predate the
issue it delivers, so if the issue was created before the window opened, an
owning PR could have merged earlier than the file covers, and the script
refuses to call it either a false closure or `ok` and reports it instead:

```
skip  PRE-123  (merged-PR window starts 2026-08-08T00:00:00Z — not classified)
```

Those issues are excluded from `FALSE CLOSURES` and, therefore, from anything
`--apply` would restore. This guard never applies under `--repo`, which
already has full history.

## Invoked from `/find-false-closures`

The command is a thin wrapper over the asset. Resolve two things from config,
then run the script once per project:

1. **Scope (projects).** If the caller passed `--project <uuid>`, use only that.
   Otherwise resolve the configured `linear.projects` via `linear-common.md`
   "Resolve configured projects" and run the asset once per project id. This
   flow is **project-scoped** (the asset queries `project(id:)`), so if no
   projects are configured and none was passed, stop and tell the user to
   configure `linear.projects` or pass `--project`.
2. **Repo, unless the caller passed `--prs-file`.** The script rejects
   passing both `--repo` and `--prs-file` (and rejects passing neither), so:

   - **Caller passed `--prs-file <path>`.** Skip repo resolution entirely —
     do not resolve or pass `--repo`.
   - **Otherwise**, resolve the repo in this order: the caller's
     `--repo owner/name`; else the project's own `repo:` under
     `linear.projects` (each configured project may name its repo, since the
     workspace spans more than one — see `linear-common.md`); else the
     current repo's `origin`:

     ```bash
     gh repo view --json nameWithOwner --jq .nameWithOwner
     ```

     (One repo per run — a Linear project whose work spans several repos
     needs a run per repo, or the widest repo whose merged PRs cover it.
     `--repo` overrides everything; the per-project `repo:` is what makes a
     multi-project sweep resolve the right repo for each project.)

Then, per resolved project, run the asset (dry-run unless the caller passed
`--apply`), reading the API key exactly as the standalone path does. With a
resolved repo:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-false-closures.py" \
  --project "<project-id>" --repo "<owner/name>" [--since 48h] [--apply] [--only PRE-1,PRE-2]
```

Or, when the caller passed `--prs-file`:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/linear-false-closures.py" \
  --project "<project-id>" --prs-file "<path>" [--since 48h] [--apply] [--only PRE-1,PRE-2]
```

If `$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob `**/handlers/assets/linear-false-closures.py`.

Pass `--since` through when the caller gave one (`48h`/`2d` shorthand, an ISO
datetime, or a Linear duration) to limit the scan to recently-closed issues —
what a scheduled run wants. Pass `--only` through with `--apply` to restore just
the named ids (they must be among the detected false closures) rather than the
whole flagged set. Each `FALSE CLOSURES` line names the merged PR that most
likely tripped the close (the one bare-mentioning the id, merged just before the
completion instant), so the report is actionable without hand-tracing history.

Fold each project's `ok`/`skip`/`FALSE CLOSURES` output into the command's
combined report. The key note below applies here too — the asset resolves the key
itself, so make sure an `op` session exists (`op signin` in your own terminal) or
run headless with `$OP_SERVICE_ACCOUNT_TOKEN`.

## Security boundary + the op session requirement

Same as `linear-archive.md`: the script needs a Linear **personal API
key** — a full-account bearer token — which must never enter a claude.ai/
Claude Code cloud sandbox. It reads the key from `$LINEAR_API_KEY`, else resolves
`$LINEAR_API_KEY_REF` with the program named by `$LINEAR_API_KEY_RESOLVER` —
`op` by default, meaning `op read <ref>` (full contract:
`dev_docs/auth_key_access.md`). The default needs an authorized `op` **session**,
not a particular shell: `op signin` in your own terminal establishes one the
agent's subshell can use too (it lapses after ~30 min idle) — see
`linear-archive.md`'s "Gotcha" note for the full explanation and the headless
`$OP_SERVICE_ACCOUNT_TOKEN` fallback.

Unlike the read fast paths, this command has **no MCP floor** — the key is
required, not an optimization — so it does not run behind `linear-common.md`'s
gate and does **not** inherit that section's "Key resolution" step. Export
`$LINEAR_API_KEY_REF` (plus `$LINEAR_API_KEY_RESOLVER` if you use a non-default
resolver), or `$LINEAR_API_KEY`, yourself before invoking. An unresolvable key is
**fatal** here, not a fallback: report the script's reason category and stop.

## Dry-run-default posture

Read-only by default: lists false closures and changes nothing. Pass
`--apply` to restore each false closure to its **own team's** Todo/unstarted
state (resolved per issue, since a project can span teams — not resolved
once from the first false closure and reused for all).

Exit codes: read-only mode returns `1` if any false closure was found (`0`
otherwise, so it composes into CI); `--apply` returns `0` if every restore
succeeded, non-zero only if one failed.

## Run it — the shipped script

**`commands/handlers/assets/linear-false-closures.py`** (Glob
`**/handlers/assets/linear-false-closures.py` if the relative path doesn't
resolve).

```bash
# Dry run (lists false closures, changes nothing):
python3 commands/handlers/assets/linear-false-closures.py --project <uuid> --repo owner/name

# Only issues completed in the last 48h (48h / 2d / ISO / -P2D):
python3 commands/handlers/assets/linear-false-closures.py --project <uuid> --repo owner/name --since 48h

# Restore false closures to their team's Todo state:
python3 commands/handlers/assets/linear-false-closures.py --project <uuid> --repo owner/name --apply

# Restore only specific flagged ids (must be among those detected):
python3 commands/handlers/assets/linear-false-closures.py --project <uuid> --repo owner/name --apply --only PRE-1,PRE-2

# Cloud routine (no gh): pass a pre-fetched merged-PR list instead of --repo.
python3 commands/handlers/assets/linear-false-closures.py --project <uuid> --prs-file merged_prs.json
```

`--project` is the Linear project UUID (see "Resolve configured projects" in
`linear-common.md` for where that id comes from). Exactly one of `--repo`
(the `owner/name` GitHub repo whose merged PRs are checked for ownership) or
`--prs-file` (a pre-fetched merged-PR list — see "`--prs-file` — the path for
a cloud routine" above) is required.
