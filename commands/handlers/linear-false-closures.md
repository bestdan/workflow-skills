# Linear handler — false-closures backstop

This is a **standalone backstop, not a `/slash` command flow.** No command in
this plugin invokes it automatically; it exists purely as
`commands/handlers/assets/linear-false-closures.py`, a runnable script you
point at a project and repo.

**Shared reference:** see `commands/handlers/linear-common.md` for connection
details and the config schema this backstop's project/repo scoping assumes.

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

A completed issue must be **owned** by a merged PR: the PR's head branch
embeds the issue's identifier (regex match — not equality on Linear's
suggested `branchName`, since the real branch is routinely a shortened form
of it), or one of the issue's attachment URLs is itself a merged PR. A PR that
only mentions the id in its body owns nothing. A completed issue with no
owning merged PR is a **false closure**.

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
