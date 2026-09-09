# What a cloud session gives a gh-issue batch, and what it withholds

**Measured 2026-09-05** inside a Claude Code cloud session, against
`bestdan/dotfiles`. This is a dated snapshot: it records what was true on that day
and is allowed to go stale. It is the companion to
[`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md), which
measured the same questions in a **routine**.

**Amended 2026-09-07 and 2026-09-08.** Routine probes on 09-07 corrected three claims
this file made. A second cloud-session probe on 09-08 corrected three more — the
"declaration was ignored" reading, "the barrier is at the account", and the connector
being unscoped — and added findings 6, 7 and 8. Finding 9 comes from a separate routine
run, not from that session. The 09-07 probes are recorded in
`2026-09-07-cloud-routine-plugins-and-gh.md`,
which lands in this directory with **PR #498** — open at the time of writing, so run ids
are cited directly and stay checkable whichever change merges first. The 09-08 probe is
session `session_01ERAh8hmMbqq4Xu2hfqbEcA` and is recorded below.

**The 2026-09-05 measurements are unchanged and still carry their date.** What changed
is what may be concluded from them. Each amendment says so where it sits and names the
earlier wording, so a reader who saw the first version can tell what moved.

## Why this exists

`commands/do-tasks.md` §4 held `gh-issue.remote_batch` off by default on a premise it
named as unprobed: that a cloud session installs a plugin the repo declares in a
committed `.claude/settings.json`, so a dispatched session would find
`gh-issue-state.py` and a usable `gh`. Anthropic's documentation is the source of the
plugin claim; the `gh` claim is documented only as far as "preinstalled and
proxy-authenticated", and the **usable** part of it was this repo's own assumption,
baked into §4 step 5's self-check. This plan had already been wrong twice by reading
documentation about unattended GitHub access, so the standing instruction was to probe
it.

Probed. **Both of the premise's claims — the plugin, and a `gh` that works — are false
here**, and the flag stays off.

## Setup

- Consumer repo `bestdan/dotfiles` (private, `handler: gh-issue`) — deliberately **not**
  `workflow-skills`, whose assets sit in its own checkout and would prove nothing.
- Branch `bestdan/cloud-probe-settings`, HEAD `3393fcb`, whose `.claude/settings.json`
  declares `extraKnownMarketplaces` (`github` / `bestdan/workflow-skills`) and
  `enabledPlugins` (`workflow-skills@workflow-skills`). The session read the file back
  verbatim, so the declaration reached the VM.
- Session `session_01NjXjJLn92VsumHpC1FsdFo`, created with `claude --cloud`, model
  `claude-opus-5[1m]`, cwd `/home/user/dotfiles`, `claude --version` **2.1.261** read
  inside the session. It runs as `root` with `$HOME=/root` while the repo is cloned under
  `/home/user`, which is why the transcripts below show both `~/.claude/...` and
  `/root/.claude/...` for the same paths. Environment log:
  `Cloning repository bestdan/dotfiles` … `No setup script configured`.
- Scratch write target `bestdan/dotfiles#699`, carrying no `task-add` marker so the
  loop's label scope could not see it. Closed after the probe.

## Measurements

### The committed plugin declaration installed nothing

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
cloned HEAD, and the **`extraKnownMarketplaces` and `enabledPlugins` keys** had no
effect.

**The rest of the same file did take effect**, which the 2026-09-08 probe established
and an earlier draft of this section obscured by saying the declaration "was ignored".
`dotfiles`' committed `hooks` block ran its `SessionStart` hook, and that hook is what
put `gh` on this box. Read this section as being about the plugin keys only.

### It is not a network or a scoping problem

The session reached the unattached public marketplace repo without help:

```
$ git ls-remote https://github.com/bestdan/workflow-skills HEAD
7319819da14dcf730dc3c5cf718d13730f6ebfb3        HEAD
```

And installing by hand, **inside the session**, put the plugin on disk and got the CLI
to report it installed:

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

**But a mid-session install does not make the plugin usable, and this file must not be
read as saying it does.** What the block above shows is files on disk and the CLI
reporting the plugin enabled. This session never invoked a skill from it. A routine that
did the same mid-session install found `ListPlugins` and `ListSkills` both empty while
`claude plugin list` reported the plugin enabled — the CLI and the harness disagreeing
about the same install (workflow-skills-ef, run `cse_016MBzxJfhs7w8pgwt1k2Hjd`). Take
`claude plugin list` as evidence about the disk, not about the running agent's tools.

### Which of the declaration's two keys was ignored — not settled

The committed file declares two keys, and there is a hint they did not fail together.

Immediately after the manual install, `claude plugin list` showed the plugin **once**,
at `Scope: user`. On the next session start in the same VM — a fresh Claude Code
process, with the marketplace now present — it showed **twice**:

```
> workflow-skills@workflow-skills   Version: 2.24.2   Scope: user      Status: √ enabled
> workflow-skills@workflow-skills   Version: 2.24.2   Scope: project   Status: √ enabled
```

A committed `.claude/settings.json` is the mechanism this probe knows of for a
`project`-scope entry, so the committed `enabledPlugins` looks to have been read at that
point — and at the cold start `claude plugin marketplace list` said
`No marketplaces configured`. That **points at** `extraKnownMarketplaces` as the ignored
key.

**"Can only" would be wrong here**, and an earlier draft of this file said it. Nothing
was done to rule out other routes to a project-scope entry, and at least one is
plausible without being tested: an install that requests that scope. A routine run
against `bestdan/workflow-skills` — a repo that tracks no `.claude/` files at all —
reported a `project`-scope entry, which no committed declaration can explain; its setup
script was cached, so its install invocation is not in any run log and the scope flag
cannot be read (workflow-skills-ef, run `cse_01M9WzAbESA3hSwBZuYWczNJ`).

**It does not establish it, and this belongs under what is not settled.** The sequence
ran in one warm VM in which a marketplace _and_ a user-scope install had both been added
by hand, so the project entry cannot be attributed to `enabledPlugins` against a clean
baseline. No second cold session was run, and nothing here explains **why** the
marketplace was absent at the cold start. Anyone acting on this — a setup script, say —
should retest from a clean VM rather than treat the split as measured.

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

### 2026-09-08: the credentialed-attach experiment, and why it did not run here

Session `session_01ERAh8hmMbqq4Xu2hfqbEcA`, `claude --cloud` from the CLI, Claude Code
2.1.263, source `bestdan/dotfiles` only, so `bestdan/workflow-skills` was **not** a
source. Every command captured its own exit code.

**`add_repo` does not exist in a cloud session.** Four spellings were searched
(`add_repo`, `mcp__Claude_Code_Remote__add_repo`, `mcp__claude-code-remote__add_repo`,
`mcp__github__add_repo`); all returned `No matching deferred tools found`. The MCP
denial hedges in the same direction: _"If the add_repo tool
(mcp__claude-code-remote__add_repo) is available in this session…"_. It **is** available
in a routine, where it succeeded — `{"access":"push","owner":"bestdan","repo":"dotfiles"}`
on the first call, no two-step dance (run `cse_014DT5cUvE7zGfVjix9fW7fC`).

So the attach half is proven and the effect half is not:

| question                                     | answer                 |
| -------------------------------------------- | ---------------------- |
| Does `add_repo` accept `access:"push"`?      | **Yes** — in a routine |
| Does it then make `gh` REST reach that repo? | **UNTESTED**           |

The routine that could attach had **no `gh`** to test with. The session that had `gh`
had **no `add_repo`**. Both halves have never held at once, and that — not a refusal —
is why the question is open.

**The two 403 bodies differ, and only the not-a-source one names the remedy.**
Source-clone (`dotfiles`): `GitHub access is not enabled for this session. An org admin
must connect the Claude GitHub App for this organization.` Not-a-source
(`workflow-skills`): `GitHub access to this repository is not enabled for this session.
Use add_repo to request access. If add_repo answers that read access is already
available and you need GitHub API or write access, call add_repo again with
access:"push" to attach the repository with credentials.`

## What this settles

1. **A committed `.claude/settings.json` does not, on its own, install a plugin into a
   cloud session.** Measured at Claude Code 2.1.261, in a CLI-launched `--cloud`
   session. Every sentence in this repo that offered the declaration as the thing that
   makes `remote_batch: true` safe was wrong; they were corrected in the same change as
   this file. **Which of the declaration's two keys was ignored is _not_ settled** — see
   "Which of the declaration's two keys was ignored" above.

   **The file itself is NOT ignored, and an earlier draft of this finding implied it
   was.** Measured 2026-09-08 in session `session_01ERAh8hmMbqq4Xu2hfqbEcA`: the same
   repo's committed `hooks` block **ran**. Its `SessionStart` hook apt-installed `gh`,
   `zsh`, `shellcheck` and `just` — `gh` is **not in the base image**, and that hook is
   where it comes from. So the correct statement is narrow: the **plugin-declaration
   keys** did not take effect; the **hooks** key did.

   **A committed hook is therefore one route to `gh`** — in this session it was the
   route, since `gh` was absent from the base image. Whether it is the **only** route is
   an absence claim this probe cannot support, and an earlier draft of this paragraph
   made it: it said "It is not the environment. It is whether the source repo commits a
   hook."

   **Read that against the companion record, which settles the same question the other
   way.** `2026-09-07-cloud-routine-plugins-and-gh.md` (PR #498) concludes that `gh` is a
   property of an environment at a point in time, citing `gh` 2.45.0 in every 09-07 run
   and rc 127 from the same environment id nine hours later. The source-repo reading fits
   the same runs — the `gh`-present ones sourced `dotfiles`, which commits the hook; the
   `gh`-absent one sourced `workflow-skills`, which tracks no `.claude/` files — but
   fitting is not discriminating, and nobody has compared the two runs' **sources** as
   the variable rather than their environment ids. **Treat both readings as live.** The
   cheap discriminator: run the same probe twice in one environment, once sourcing a repo
   with the hook and once without, and report `which gh` from each.
2. **`gh` was present in this session — see finding 1 for where from — and had no
   working credential in the session as provisioned**, reads 403 alongside writes. So the
   2026-08-24 routine finding and this session's finding agree in effect: no usable `gh`.
   They differ in how far they got — that routine had no `gh` binary to try, this session
   had the binary and a dead token. **Do not read that as a session-versus-routine
   property**; finding 1 says what actually varies, and says it is unsettled.
   **Where the barrier sits is _not_ settled** either, and the wording matters: an
   earlier draft of this file said "the barrier is at the account, not the endpoint" as
   though that followed from reads-403-too. It does not follow. See below.
3. **The GitHub MCP connector remains the credentialed channel**, in a cloud session as
   in a routine, and it **can** perform the `gh-issue` label write. It replaces the whole
   label set, matching the REST path — so validate-then-replace stays the rule on both.

   **But it is repo-scoped, and this file previously read as though it were not.**
   Measured 2026-09-08: `mcp__github__list_issues` succeeded on the attached source repo
   and was **refused** on one that was not attached — `Access denied: repository
   "bestdan/workflow-skills" is not configured for this session. Allowed repositories:
   bestdan/dotfiles.` So the connector is not a way around repository scoping. It is
   scoped the same way `gh` is; it differs by being credentialed **within** that scope.
4. **Closing the plugin gap alone would not have been enough in the environments
   probed** — `gh` was unusable in each of them. Whether it stays unusable after a
   credentialed attach is open; see "Where the `gh` 403 comes from" below. Read this as
   "both gaps were open in everything measured", **not** as "`gh` can never work".
5. **Nothing about the marketplace clone is blocked.** Proxy repository scoping does not
   stop a public unattached repo from being cloned over plain HTTPS.
6. **`git` is credentialed for the source repo where `gh` REST is not.** Measured
   2026-09-08 in the same session, on the same repo, seconds apart: `git push --dry-run`
   to `bestdan/dotfiles` reported `* [new branch] … Would set upstream`, while
   `gh api repos/bestdan/dotfiles` returned the org-App 403. Both tokens read
   `proxy-injected`; auth is injected by an agent proxy, and the two paths through it do
   not agree. A handler that reaches GitHub through `git` is in a different position
   from one that reaches it through `gh api` — and this one uses `gh api`.
7. **`gh auth status` reports failure and exits 0.** Confirmed a third time here, after a
   cloud session on 2026-09-05 and a routine on 2026-09-07. `gh api user` also succeeds
   on the same dead credential, because it is not repo-scoped. Neither is a usable health
   check; see `commands/do-tasks.md` §4 step 5.
8. **`gh pr list` is refused as GraphQL, not as REST.** Verbatim: `This GraphQL query
   (PullRequestList, sent by gh pr list) is not enabled for this session — only the
   pinned set of PR-review operations is served. Use REST via
   gh api repos/{owner}/{repo}/... instead.` So a GraphQL refusal and a REST 403 are
   different failures and must not be read as one.
9. **A cloud-environment setup script does install the plugin** — measured after this
   probe, and it is the route that works. The environment here reported
   `No setup script configured`; an environment that runs one (`claude plugin
   marketplace add` plus `claude plugin install` before the session starts) comes up with
   the plugin enabled (workflow-skills-ef, run `cse_018jEMrUEUUc4ACccCHfSQfy`: env log
   `Running setup script` → `Setup script completed` → `Starting Claude Code`, then
   `claude plugin list` reporting it). This also answers the sub-question this file
   originally left open: a setup script is **environment** configuration, not something a
   repo can commit — which is why looking for it in a repo finds nothing.

## What this does NOT settle

- **Where the `gh` 403 comes from.** Three live readings, and this probe distinguishes
  none of them. **Do not read this bullet as a two-way choice** — an earlier draft framed
  it as "policy or configuration", which omitted the third and likeliest one.
  1. **Org-level policy or a missing app.** The message names a missing Claude GitHub App
     connection for the organization; `bestdan/dotfiles` is a personal repo, so
     "organization" is this account. Connecting the app was not tried.
  2. **Repo provisioning.** `bestdan/dotfiles` was the session's **cloned source**, which
     is not the same thing as a repo attached **with credentials**. A source clone buys
     read access to the working tree; the GitHub API is a separate grant. So the 403 may
     simply be the un-provisioned state, with nothing about accounts or policy needing to
     be true. **The attach itself has been performed** — `add_repo` accepted
     `access:"push"` on the first call in routine `cse_014DT5cUvE7zGfVjix9fW7fC`. What
     no run has done is make a **repo-scoped `gh` call after** such an attach: that
     routine had no `gh` binary, and the session that had one had no `add_repo`. Every
     403 recorded in this file and in PR #498 was measured against a **cloned source or
     an unattached repo**, never against a credentialed attach.
  3. **Something else neither probe looked for.**

  So read finding 2 as "not available in the session as provisioned" — **not** as "the
  proxy forbids it", and **not** as "`gh` cannot work in a cloud session". The question is
  **unanswered, not answered no.**

- **Whether a credentialed attach fixes it — attempted twice, still open.** Calling
  `add_repo` with `access:"push"` and re-probing is what would settle reading 2, and it
  is the one result that could move `gh-issue.remote_batch` off `false` for a reason
  other than caution. **The obstacle is no longer that nobody has tried.** It is that
  the two halves live in different places: `add_repo` exists in a routine and not in a
  cloud session, while `gh` arrives from the **source repo's** committed `SessionStart`
  hook. A routine sourcing a repo with no such hook attaches successfully and has no
  `gh` to test with; a session sourcing a repo that installs `gh` has no `add_repo`.
  Whoever runs this next must satisfy both **in one run** — source a repo whose hook
  installs `gh`, in an environment where `add_repo` is available — and check `which gh`
  first rather than assuming. A probe design agreed jointly is in the "Reproducing"
  section of the record landing in PR #498. Its load-bearing rule: **capture the exit
  code alongside the body for every call.** Every `gh` finding across three probes
  turned on rc, and `gh auth status` reports failure while exiting 0 — a stdout-only
  probe reproduces exactly the blind spot that made it look like a working health check.
- **Whether any of this differs on an organization-owned repo, or on a session started
  from the web rather than the CLI.** One session, one personal repo.

## The decision

`gh-issue.remote_batch` **stays `false` by default**, and the capability matrix stays
`opt`. The premise that would have justified flipping it is now measured false rather
than merely unprobed, which is a stronger reason for the same default.

The correction was to the prose, not the default, and it landed in four files. The
declaration claim lived in `commands/do-tasks.md` §3 step 4 and §4,
`commands/task-config.md` and `commands/handlers/gh-issue.md`. The separate assumption
that a dispatched session's `gh` works lived in `commands/handlers/claim-lock.md` and in
§3's connector-availability note. All of it was corrected in the same change as this
file.

A repo that wants `remote_batch: true` needs the plugin present by some other route, and
its dispatched sessions need a credentialed channel that is currently the MCP connector
rather than `gh` — which is the same gap `claim-lock.md` already records, and the same
reason the handler still owes an MCP branch for its label writes.
