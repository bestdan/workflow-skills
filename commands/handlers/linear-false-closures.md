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

A completed issue must be **owned** by a merged PR, by any of three signals:
the PR's head branch embeds the issue's identifier (regex match — not equality
on Linear's suggested `branchName`, since the real branch is routinely a
shortened form of it); one of the issue's attachment URLs points at a merged PR
(compared on canonical `owner/repo/pull/<n>` identity, so a trailing slash,
`?src=linear` query, or `/files` tab still matches); or the PR title/body
**closes** the issue with a keyword (`closes PRE-123`). A PR that merely
name-drops the id — no branch, no attachment, no closing keyword — owns
nothing. A completed issue with no owning merged PR is a **false closure**.

The closing-keyword signal is what covers cloud/hosted runs, where the PR head
branch frequently does not embed the Linear id and branch matching alone would
miss delivered work.

## Invoked from `/find-false-closures`

The command is a thin wrapper over the asset. Resolve two things from config,
then run the script once per project:

1. **Scope (projects).** If the caller passed `--project <uuid>`, use only that.
   Otherwise resolve the configured `linear.projects` via `linear-common.md`
   "Resolve configured projects" and run the asset once per project id. This
   flow is **project-scoped** (the asset queries `project(id:)`), so if no
   projects are configured and none was passed, stop and tell the user to
   configure `linear.projects` or pass `--project`.
2. **Repo.** If the caller passed `--repo owner/name`, use it. Otherwise default
   to the current repo's `origin`:

   ```bash
   gh repo view --json nameWithOwner --jq .nameWithOwner
   ```

   (One repo per run — a Linear project whose work spans several repos needs a
   run per repo, or the widest repo whose merged PRs cover it. Passing `--repo`
   overrides the default.)

Then, per resolved project, run the asset (dry-run unless the caller passed
`--apply`), reading the API key exactly as the standalone path does:

```bash
python3 commands/handlers/assets/linear-false-closures.py \
  --project "<project-id>" --repo "<owner/name>" [--apply]
```

Fold each project's `ok`/`skip`/`FALSE CLOSURES` output into the command's
combined report. The op-in-agent-shell key gotcha below applies here too — the
asset resolves the key itself, so run the command from an `op`-authorized shell
(or a headless one with `$OP_SERVICE_ACCOUNT_TOKEN`).

## Security boundary + the op-in-agent-shell gotcha

Same as `linear-archive.md`: the script needs a Linear **personal API
key** — a full-account bearer token — which must never enter a claude.ai/
Claude Code cloud sandbox. It reads the key from `$LINEAR_API_KEY`, else
`op read "$LINEAR_API_KEY_REF"`. `op` only unlocks in an authorized terminal
under 1Password desktop-app integration, not in an agent's tool-spawned
subshell — see `linear-archive.md`'s "Gotcha" note for the full explanation
and the interactive/headless fallbacks. Run this script from your own
terminal, or headless with `$OP_SERVICE_ACCOUNT_TOKEN` set.

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

# Restore false closures to their team's Todo state:
python3 commands/handlers/assets/linear-false-closures.py --project <uuid> --repo owner/name --apply
```

`--project` is the Linear project UUID (see "Resolve configured projects" in
`linear-common.md` for where that id comes from); `--repo` is the
`owner/name` GitHub repo whose merged PRs are checked for ownership.
