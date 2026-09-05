# What a cloud session gives a gh-issue batch, and what it withholds

**Measured 2026-09-05** inside a Claude Code cloud session, against
`bestdan/dotfiles`. This is a dated snapshot: it records what was true on that day
and is allowed to go stale. It is the companion to
[`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md), which
measured the same questions in a **routine**.

## Why this exists

`commands/do-tasks.md` §4 held `gh-issue.remote_batch` off by default on a premise it
named as unprobed: that a cloud session installs a plugin the repo declares in a
committed `.claude/settings.json`, so a dispatched session would find
`gh-issue-state.py` and a proxy-authenticated `gh`. Anthropic's documentation asserts
both. This plan had already been wrong twice by reading documentation about unattended
GitHub access, so the standing instruction was to probe it.

Probed. **Both halves of the documented premise are false here**, and the flag stays
off.

## Setup

- Consumer repo `bestdan/dotfiles` (private, `handler: gh-issue`) — deliberately **not**
  `workflow-skills`, whose assets sit in its own checkout and would prove nothing.
- Branch `bestdan/cloud-probe-settings`, HEAD `3393fcb`, whose `.claude/settings.json`
  declares `extraKnownMarketplaces` (`github` / `bestdan/workflow-skills`) and
  `enabledPlugins` (`workflow-skills@workflow-skills`). The session read the file back
  verbatim, so the declaration reached the VM.
- Session `session_01NjXjJLn92VsumHpC1FsdFo`, Claude Code launched from the CLI, model
  `claude-opus-5[1m]`, cwd `/home/user/dotfiles`, `claude --version` **2.1.261** read
  inside the session. Environment log: `Cloning repository bestdan/dotfiles` …
  `No setup script configured`.
- Scratch write target `bestdan/dotfiles#699`, carrying no `task-add` marker so the
  loop's label scope could not see it. Closed after the probe.

## Measurements

### The committed declaration installed nothing

```
$ claude plugin list
No plugins installed. Use `claude plugin install` to install a plugin.
$ claude plugin marketplace list
No marketplaces configured
$ cat ~/.claude/plugins/installed_plugins.json
{ "version": 2, "plugins": {} }
$ echo "CLAUDE_PLUGIN_ROOT=[$CLAUDE_PLUGIN_ROOT]"
CLAUDE_PLUGIN_ROOT=[]
$ find / -name 'gh-issue-state.py'     # no output, rc=1
```

`$CLAUDE_PLUGIN_ROOT` is **empty**, not merely pointing somewhere unexpected. There is
no assets directory anywhere on the box. The declaration was present and correct on the
cloned HEAD and was ignored.

### It is not a network or a scoping problem

The session reached the unattached public marketplace repo without help:

```
$ git ls-remote https://github.com/bestdan/workflow-skills HEAD
7319819da14dcf730dc3c5cf718d13730f6ebfb3        HEAD
```

And installing by hand, **inside the session**, worked end to end:

```
$ claude plugin marketplace add bestdan/workflow-skills
Cloning via HTTPS: https://github.com/bestdan/workflow-skills.git
Clone complete, validating marketplace…
√ Successfully added marketplace: workflow-skills (declared in user settings)
$ claude plugin marketplace list
Configured marketplaces:
> workflow-skills   Source: GitHub (bestdan/workflow-skills)
$ claude plugin install workflow-skills@workflow-skills
Installing plugin "workflow-skills@workflow-skills"...
√ Successfully installed plugin: workflow-skills@workflow-skills (scope: user)
$ claude plugin list
Installed plugins:
> workflow-skills@workflow-skills   Version: 2.24.2   Scope: user   Status: √ enabled
$ ls /root/.claude/plugins/cache/workflow-skills/workflow-skills/2.24.2/commands/handlers/assets
_labels.py  _secret_resolve.py  _shape.py  gh-issue-claim.py  gh-issue-state.py  …
```

So the failure is specifically the **auto-install from committed repo settings**, not
egress, not proxy repository scoping, and not the marketplace itself.

### Which half of the declaration was ignored

The committed file declares two things. They did not fail together.

Immediately after the manual install, `claude plugin list` showed the plugin **once**,
at `Scope: user`. On the next session start in the same VM — a fresh Claude Code
process, with the marketplace now present — it showed **twice**:

```
> workflow-skills@workflow-skills   Version: 2.24.2   Scope: user      Status: √ enabled
> workflow-skills@workflow-skills   Version: 2.24.2   Scope: project   Status: √ enabled
```

Nothing wrote a second user-scope entry in between, and `project` scope can only come
from the repo's committed `.claude/settings.json`. So the committed **`enabledPlugins`
was honoured**, once the marketplace it names was resolvable; what the cold session did
not act on was the committed **`extraKnownMarketplaces`** — at that point
`claude plugin marketplace list` said `No marketplaces configured`.

Read that as **observed sequence, not diagnosed cause**: this probe did not establish
why the marketplace was not configured on the cold start, and a second cold session was
not run to confirm the project entry now appears from a clean VM.

### `gh` is installed, and unusable

```
$ which gh; gh --version
/usr/bin/gh
gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)
$ echo "GH_TOKEN=[$GH_TOKEN] GITHUB_TOKEN=[$GITHUB_TOKEN]"
GH_TOKEN=[proxy-injected] GITHUB_TOKEN=[proxy-injected]
$ gh auth status
github.com
  X Failed to log in to github.com using token (GH_TOKEN)
  - The token in GH_TOKEN is invalid.
```

Every `gh api` call failed the same way — **the read as well as the write**, so this is
not a method restriction:

```
$ gh api repos/bestdan/dotfiles/issues/699 --jq '.labels[].name'
gh: GitHub access is not enabled for this session. An org admin must connect the
    Claude GitHub App for this organization. (HTTP 403)

$ gh api --method PATCH repos/bestdan/dotfiles/issues/699 -f 'labels[]=est:1'
    … same 403
```

### The MCP connector did the write that `gh` could not

```
mcp__github__issue_write(owner=bestdan, repo=dotfiles, issue_number=699,
                         method=update, labels=["est:1"])
  → {"id":"5359099860","url":"https://github.com/bestdan/dotfiles/issues/699"}

mcp__github__issue_read(method=get_labels, …)
  → {"labels":[{"name":"est:1", …}],"totalCount":1}
```

## What this settles

1. **A committed `.claude/settings.json` does not, on its own, install a plugin into a
   cloud session.** Measured at Claude Code 2.1.261, in a CLI-launched `--cloud`
   session. The `enabledPlugins` half works once the marketplace exists; the
   `extraKnownMarketplaces` half did not produce one. Every sentence in this repo that
   offers the declaration as the thing that makes `remote_batch: true` safe is wrong
   and must be corrected.
2. **`gh` exists in a cloud session but has no working credential**, and the barrier is
   at the account, not the endpoint: reads 403 alongside writes. So the 2026-08-24
   routine finding and this session's finding agree in effect — no usable `gh` — while
   disagreeing on the mechanism (a routine had no `gh` binary at all; a cloud session
   has the binary and a dead token).
3. **The GitHub MCP connector remains the credentialed channel**, in a cloud session as
   in a routine, and it **can** perform the `gh-issue` label write. It replaces the whole
   label set, matching the REST path — so validate-then-replace stays the rule on both.
4. **Nothing about the marketplace clone is blocked.** Proxy repository scoping does not
   stop a public unattached repo from being cloned over plain HTTPS.

## What this does NOT settle

- **Whether the `gh` 403 is policy or configuration.** The message names a missing
  Claude GitHub App connection for the organization. `bestdan/dotfiles` is a personal
  repo, so "organization" is this account. Connecting the app might make `gh` work; that
  was not tried. Read finding 2 as "not available on this account today", not as "the
  proxy forbids it".
- **Whether a setup script fixes the plugin gap.** The environment reported
  `No setup script configured`. Given the scope finding above, the narrow candidate is a
  setup script running `claude plugin marketplace add` — the committed `enabledPlugins`
  then does the rest. That was **not** measured as a session-start step, and a setup
  script is a per-environment setting rather than something a repo can commit.
- **Whether the plugin gap matters on its own.** It does not: closing it still leaves
  `gh`. Both must be solved before `remote_batch: true` dispatches anything that works.
- **Whether any of this differs on an organization-owned repo, or on a session started
  from the web rather than the CLI.** One session, one personal repo.

## The decision

`gh-issue.remote_batch` **stays `false` by default**, and the capability matrix stays
`opt`. The premise that would have justified flipping it is now measured false rather
than merely unprobed, which is a stronger reason for the same default.

The correction owed is to the prose, not the default: `commands/do-tasks.md` §4,
`commands/task-config.md` and `commands/handlers/gh-issue.md` all tell a reader that
committing the declaration is what makes `true` safe. It is not. A repo that wants
`remote_batch: true` needs the plugin present by some other route, and its dispatched
sessions need a credentialed channel that is currently the MCP connector rather than
`gh` — which is the same gap `claim-lock.md` already records, and the same reason the
handler still owes an MCP branch for its label writes.
