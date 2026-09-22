---
created: 2026-09-05
---

# What a cloud session gives a gh-issue batch, and what it withholds

**Measured 2026-09-05** inside a Claude Code cloud session, against
`bestdan/dotfiles`. This is a dated snapshot: it records what was true on that day
and is allowed to go stale. It is the companion to
[`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md), which
measured the same questions in a **routine**.

**Amended 2026-09-07, 2026-09-08 and 2026-09-09.** Routine probes on 09-07 corrected three claims
this file made. A second cloud-session probe on 09-08 corrected three more — the
"declaration was ignored" reading, "the barrier is at the account", and the connector
being unscoped — and added findings 6, 7 and 8. Finding 9 comes from a separate routine
run, not from that session. A controlled two-arm run on 09-09 then settled where the
`gh` binary comes from; finding 1 carries it. The 09-07 probes are recorded in
[`2026-09-07-cloud-routine-plugins-and-gh.md`](2026-09-07-cloud-routine-plugins-and-gh.md),
merged to `main` in PR #498. Run ids are cited directly throughout, so every claim stays
checkable against the run rather than against either record. The 09-08 probe is
session `session_01ERAh8hmMbqq4Xu2hfqbEcA` and is recorded below.

**Amended 2026-09-15, and this one overturns the file's headline conclusion.** A cloud
session read a GitHub **issue dependency edge** successfully — the capability every
version of this file, and the companion routine record, treated as unavailable. It did it
with plain `curl` and the ambient `$GH_TOKEN`. **So the conclusion that a cloud session
has no working GitHub API access is false.** One counterexample settles that much.

**Why `gh` failed on 09-05 stays open, and this amendment does not close it.** Four
things differ between the two observations — ten days, a different source repo, a
different client, and credential provisioning that was never inspected — so the pair
cannot isolate any one of them as the cause. An earlier draft of this amendment said
"the barrier measured on 09-05 was `gh`, not the environment and not the credential";
that was a causal claim this evidence does not support, and it contradicted this
amendment's own "What it does NOT establish" list one section down. See
[2026-09-15: the API is reachable without `gh`](#2026-09-15-the-api-is-reachable-without-gh)
before relying on anything below, and read finding 2 with that correction in hand.

**The 2026-09-05 measurements are unchanged and still carry their date.** What changed
is what may be concluded from them. Each amendment says so where it sits and names the
earlier wording, so a reader who saw the first version can tell what moved.

**Amended 2026-09-22.** A scheduled `/do-tasks` run sourced from this repo confirmed
finding 1 (`gh` absent here, present after `apt-get install`) and the `gh auth status`
false-negative still hold, and found that the GraphQL refusal blocks single-mode,
foreground `/do-tasks` too — not only dispatched batch sessions, which is what
`commands/do-tasks.md` §4 and `gh-issue-claim.md` had assumed was the only exposure.
See [2026-09-22: a scheduled `/do-tasks` run confirms both findings still
hold](#2026-09-22-a-scheduled-do-tasks-run-confirms-both-findings-still-hold-and-finds-the-blocker-is-not-batch-only).

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

### 2026-09-15: the API is reachable without `gh`

**A cloud session read an issue dependency edge, and got the right answer.** Asked for
`bestdan/workflow-skills#691`'s `blocked_by` set, it returned **#692** with its labels —
matching the live board, which is what makes this a read and not an artefact.

```
curl -sS -H "Authorization: Bearer $GH_TOKEN" \
  "https://api.github.com/repos/bestdan/workflow-skills/issues/691/dependencies/blocked_by?per_page=100"
→ [ { "number": 692, … } ]
```

**Two things in that session differ from the 09-05 one, and the second is the finding:**

- **There was no `gh` binary at all — and finding 1 predicts exactly that.** The source
  repo here was `bestdan/workflow-skills`, not `dotfiles`, and finding 1 established
  across eight runs that `gh` arrives from the `dotfiles` setup script: every
  `gh`-present run sourced `dotfiles`, every `gh`-absent run did not. So this is a ninth
  run agreeing with it, not a changed image. **It also means the 09-05 `gh` failure was
  never the general case** — it was measured in the one configuration that happens to
  install `gh`.
- **A request carrying `Authorization: Bearer $GH_TOKEN` was authenticated by
  `api.github.com`.** Stated that narrowly on purpose: whether the variable held a real
  credential, or still held `proxy-injected` with the egress proxy substituting one, was
  **not inspected** — see the last bullet of "What it does NOT establish" below. On 09-05
  the same variable read the literal string `proxy-injected` and `gh auth status` called
  it invalid.

**What this overturns.** This file's finding 2 says a cloud session has "no working
credential in the session as provisioned", reading 403 on reads as well as writes, and
the companion routine record reaches the same conclusion by a different route. **That is
now too strong.** What was measured on 09-05 is that **`gh` could not authenticate**. The
inference from there to "the environment has no GitHub API access" did not hold, and this
probe is the counterexample.

**What replaced that inference is smaller than it looks.** A path to the API exists in a
cloud session; which of the four differences above accounts for `gh`'s 09-05 failure is
not settled by this, and an earlier draft of this section overstated it as "the client
was the broken part."

**This was foreshadowed and not followed up.** Finding 6 already recorded that `git` is
credentialed for the source repo where `gh` REST is not — same session, same repo,
seconds apart. That was the same shape of result (a non-`gh` client succeeding where `gh`
fails) and it was read as a curiosity about `git` rather than as evidence about `gh`.

**What it does NOT establish**, kept deliberately narrow, because over-reading a single
probe is the specific error this amendment is correcting:

- **One session, one repo, one moment.** `bestdan/workflow-skills` was that session's own
  source repo. Nothing here says an unattached repo is reachable; the 09-08 scoping
  finding suggests it would not be.
- **Interactive cloud session ≠ scheduled routine.** ~~This file has drawn that line
  repeatedly and it still stands unmeasured.~~ **Answered 2026-09-16 — a routine can do
  it too.** See [2026-09-16: the same read, from a
  routine](#2026-09-16-the-same-read-from-a-routine).
- **Reads only.** No write was attempted through this path. `POST`/`DELETE` on the
  dependency endpoints is untested here, and the connector remains the measured write
  channel. **Still true after the 09-16 routine run**, which was also reads-only.
- **Where the token comes from is unresolved.** ~~Whether `$GH_TOKEN` now holds a real
  credential, or still holds `proxy-injected` with the egress proxy substituting one, was
  not checked.~~ **Answered 2026-09-16: it is the placeholder, and the proxy substitutes.**
  See the routine section below.

**Consequence for the `Blocked by:` footer.** `commands/push-plan.md` §5's preamble
(the "Its strongest reason is the routine channel" paragraph, **not** §5.5, which an
earlier draft cited) justifies the body footer on the grounds that a cloud routine
cannot read the edge in any form. **That premise is false in both environments as of
2026-09-16** — the session measured here, and the routine measured below. The footer now
has no measured limitation behind it. **Removing the footer is not thereby decided** —
that is a behaviour change with its own consequences, and this record's job is to say the
reason for it is gone, not to make the call.

> **Correcting an earlier draft of this paragraph, 2026-09-17.** It pointed at
> [#500](https://github.com/bestdan/workflow-skills/issues/500) as the open issue that
> owned footer removal. **#500 is CLOSED/COMPLETED and never owned that.** It was
> already delivered by [#444](https://github.com/bestdan/workflow-skills/pull/444)
> (merged 2026-09-02, v2.17.0) — five days before #500 was filed — and its closing note
> records that the `Blocked by:` footer was **kept alongside the native edge on
> purpose**, as that issue's own option 1. So `/push-plan` has written native edges since
> v2.17.0, and keeping the footer beside them was a deliberate choice, not an oversight
> awaiting a decision. **No open issue owns footer removal.** Anyone who wants it removed
> is reopening a settled call and should say so.

### 2026-09-16: the same read, from a routine

**A scheduled cloud routine read the dependency edge.** Routine
`trig_01KpwuLtYj74EJ954dPHVwpH`, run `cse_01Dg1yyyDLSTKykKZKSxpQ7v`, environment
`Linear Tidy Routine`, sources `bestdan/workflow-skills` + `bestdan/dotfiles`,
`claude-sonnet-5`, 74s, reads only.

```
curl HTTP 200
[ { "url": "https://api.github.com/repos/bestdan/workflow-skills/issues/692", … } ]
```

`#691`'s `blocked_by` returned **#692**, matching the live board. `probe-cloud-deps.sh`
agreed independently and exited 0.

**The token is the placeholder, and the read still worked. That is the finding.**

```
GH_TOKEN len=14 first4=prox
GITHUB_TOKEN len=14
```

Both hold the literal string `proxy-injected` — not a credential. So **the egress proxy
substitutes a real credential on outbound HTTPS**, and the capability belongs to the
proxy path rather than to anything the environment holds. Two consequences:

- "Does this session have a working token?" is the wrong question. It does not, and it
  reads the API anyway.
- The capability does **not** travel. Anything running off this proxy path — a local
  shell, a runner, a different egress — has to be measured separately.

**What did NOT happen: the controlled comparison.** `gh` was **absent**, so only one
client was tested and no gh-side result exists. The run was designed to settle whether
`gh` specifically is what fails, by running both clients in one session; it did not.
**So "the client is the barrier" remains unproven** — `curl` succeeding is a positive
result that needs no control, but it is not evidence about `gh`.

**An unexplained contradiction, recorded rather than guessed at.** `bestdan/dotfiles`
**was** a source and the setup script **did** run to completion, yet `gh` was absent.
Finding 1 above establishes across eight runs that `gh` arrives from the `dotfiles`
setup script. Either the `Linear Tidy Routine` environment supplies its own setup script
in place of the repo's, or finding 1 is narrower than it reads. **Not diagnosed** — no
second run was made, and the two readings are not distinguished by anything measured.

**What this does NOT establish**, same discipline as the session section above:

- **Reads only in this run — and the write probes have since been run.** Measured the
  same day, runs `cse_01R2HLXrknbeDW8NuK8bBjYP` and `cse_01EvGKZQfMFPGrfSSucrNkC3`:
  `DELETE` and `POST` on the ref path both return `403 Write access to this GitHub API
  path is not permitted through this proxy`, word for word what 2026-08-24 measured — so
  `claim-lock.md` stands. **But "writes are refused" is the wrong generalisation from
  that; see the next section.**

  **Note the two refusals are different in kind**, which is why neither settles the
  other. The write path fails with a **proxy policy** message; the read path, when
  unprovisioned, fails with the **org-App provisioning** message. A credentialed attach
  is a plausible answer to the second and would not touch the first.
- **One routine, one environment, one repo**, and that repo was a cloned source. Nothing
  here says an unattached repo is reachable.
- **`gh` untested**, per above.

### 2026-09-17: the proxy's write block is PATH-SCOPED — issue writes are permitted

**A routine PATCHed an issue's labels and it was allowed.** Run
`cse_012mi73fBwpCdckjPF3KKhts`, same environment as the read and ref-write probes.

```
PATCH /repos/bestdan/workflow-skills/issues/723   → HTTP 200 (full issue object)
labels before: ['prio:3', 'est:2']   labels after: ['prio:3', 'est:2']   state: closed
```

No policy error of any kind — against the same proxy, in the same environment, minutes
after the ref path was refused, carrying the same `proxy-injected` placeholder. **Not
"the same token":** the real credential is substituted by the proxy and was never
visible, so whether both requests carried the same one is unobserved. What was held
constant is the environment and the placeholder.

**So the refusal message meant what it said.** "Write access to **this GitHub API path**
is not permitted" is path-scoped. Reading it as "writes are blocked" would take a true
observation about one endpoint and generalise it into a false claim about the
environment — the same error this file has now made twice in the other direction.

| routine, plain `curl`, one environment  | result                |
| --------------------------------------- | --------------------- |
| `GET` issue dependencies (`blocked_by`) | `200`                 |
| `PATCH` an issue's labels               | **`200` — permitted** |
| `POST` a ref                            | `403` proxy policy    |
| `DELETE` a ref                          | `403` proxy policy    |

**The write was a no-op by construction** — the probe read #723's labels and sent back
exactly that set, so permission was tested without risking the board. Raw REST label
writes replace the whole set, which is why it was built to read-then-echo rather than to
send a hand-written list.

**What this changes, and what it does not:**

- **`claim-lock.md` is unaffected, and its reason is now sharper.** A routine cannot
  release a claim ref — not because routines cannot write, but because **the ref path
  specifically** is closed to them. The comment-token election stays correct.
- **The handler's label writes have a working unattended channel that is not the MCP
  connector.** That reframes "the handler owes an MCP branch for its label writes" from a
  prerequisite into one of two options.
- **It does NOT unblock `gh-issue.remote_batch`.** That gate needs **two** things
  (`commands/do-tasks.md` §4): a dispatched session with the **plugin** installed, and a
  working write channel. The plugin gap is untouched and remains binding — and the
  handler's writes go through `gh-issue-state.py` calling `gh api`, not `curl`, while
  `gh` is absent from a routine entirely.

**What it does NOT establish:**

- **Only the labels field, only a no-op.** A mutating PATCH was deliberately not
  attempted. The proxy decides by path and method rather than payload, so a real change
  is **expected** to behave the same — expected, not observed.
- **One repo, one environment, one moment**, and `bestdan/workflow-skills` was that
  session's own cloned source.
- **Which other paths are writable is unmapped.** Comments, milestones, sub-issues and
  the dependency endpoints were not tried. The only safe reading is per-path: measure the
  one you need, and do not extrapolate from either result here.

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

### 2026-09-22: a scheduled `/do-tasks` run confirms both findings still hold, and finds the blocker is not batch-only

Session `session_01JuqCTdBY7S72269nbfCMLG`, a scheduled (unattended) routine sourced
from `bestdan/workflow-skills` itself, attempting a plain `/do-tasks` single-issue claim
under the `gh-issue` handler — not a probe session, a real attempt at the task this file's
findings are about.

- **Finding 1 (no SessionStart `gh` install for this source repo) still holds.**
  `gh` was absent (`rc 127`) at session start, seven days after the last measurement.
  `apt-get install -y gh` succeeded and installed `2.45.0-1ubuntu0.3` — the same version
  the `dotfiles` hook installs, from the same `noble-updates` archive, confirming the
  package is reachable here too; this repo simply has no hook that runs it.
- **The `gh auth status` false-negative (§ "`gh` is installed, and unusable") still
  holds, verbatim.** `gh auth status` exited `0` and printed
  `The token in GH_TOKEN is invalid.` in the same breath. `gh api user` and a
  repo-scoped `gh api repos/bestdan/workflow-skills` both succeeded (`200`) against the
  same token in the same process — the exit-code check alone would have passed a dead
  credential, exactly the failure mode `commands/handlers/gh-issue-claim.md` §4 step 5
  already writes its self-check around.
- **New: the GraphQL refusal (§ 2026-09-07 finding 5) is not specific to routines or to
  batch-dispatched sessions — it blocks the single-issue, foreground `/do-tasks` path
  too.** `gh issue list`, `gh issue view`, and `gh pr list` each returned the identical
  `HTTP 403: GitHub GraphQL is not available from Claude Code sessions; use the REST
  API …` this file has recorded since 09-07, in a session with a *working* REST
  credential (not the unprovisioned case findings 2 and the 09-08 section describe).
  Plain `gh api repos/{owner}/{repo}/issues?state=open` (REST, unauthenticated-shape
  list) returned issue numbers normally.

  | call (this session, working REST credential) | result                        |
  | ---------------------------------------------- | ------------------------------ |
  | `gh api repos/<repo>` / `gh api user`          | `200`                          |
  | `gh api repos/<repo>/issues?state=open`        | `200`, REST                    |
  | `gh issue list --search '...' --json ...`      | `403` GraphQL refusal          |
  | `gh issue view <n> --json ...`                 | `403` GraphQL refusal          |
  | `gh pr list --json ...`                        | `403` GraphQL refusal          |

**What this changes.** The gh-issue handler's own docs (`commands/do-tasks.md` §4 step
5, `gh-issue-claim.md` line 5) frame the `gh`-availability risk as something that
attaches to **dispatched remote batch sessions**, with foreground/single-mode `/do-tasks`
implicitly assumed safe because it runs in "the current session." That assumption does
not hold when the current session is itself one of these environments: "Find
candidates" (`gh issue list`), the pre-flight in-flight check (`gh pr list`), and the
WIP gate's `count_wip` (`gh issue list` inside `gh-issue-claim.py`) are all
GraphQL-backed and all fail the same way here as in a dispatched or routine session —
so a scheduled `/do-tasks` invocation sourced from `bestdan/workflow-skills` cannot run
the gh-issue flow as written today, batch or not, until either this repo gains a
`SessionStart` hook equivalent to `dotfiles`' (closing only the "`gh` is absent"
half) **and** the GraphQL block is lifted or the handler's GraphQL-backed calls are
replaced with the REST equivalents the proxy's own error message names.

**What this does NOT establish.** `gh pr create` and `gh issue edit`/`gh issue
comment` — the mutating calls "Claim the issue", "PR", and "Bail" depend on — were not
attempted, to avoid claiming or writing to a live issue from an unattended run whose
claim-lock race safety this file's findings already call into question. Whether they are
REST-backed (and so would work) or also refused is unmeasured; `gh issue create` and
`gh pr create` are known in `gh`'s own source to mix both. `commands/handlers/claim-lock.md`'s
ref-based `acquire`/`release` calls `gh api` directly (REST), so those specifically are
expected to work per the 2026-09-17 finding above — but that too is inference from a
different endpoint, not a measurement taken in this session.

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

   **`gh` presence follows the SOURCE REPO, not the environment — settled 2026-09-09 by
   a controlled two-arm run.** Two routines, same `environment_id`
   (`env_01KURKZo3LcfRKBaEZWcbsrk`), same model, same Claude Code build (2.1.266), fired
   ~40 seconds apart, differing **only** in the source repo:

   |                             | A — source `dotfiles` | B — source `workflow-skills` |
   | --------------------------- | --------------------- | ---------------------------- |
   | `CLAUDE_CODE_REMOTE`        | `true`                | `true`                       |
   | `gh`                        | `/usr/bin/gh` 2.45.0  | absent, rc 127               |
   | `just` (hook payload)       | `/usr/local/bin/just` | absent                       |
   | `shellcheck` (hook payload) | `/usr/bin/shellcheck` | absent                       |
   | `mise` (**not** payload)    | absent                | absent                       |
   | `shfmt` (**not** payload)   | absent                | absent                       |

   Runs `cse_01QPUZsJtPAyEeUandvm3P7V` (A) and `cse_01R9taMQAkgGQAgyfVmHoNXs` (B). The
   controls carry the result: three hook-payload tools move with the source repo while
   two non-payload tools stay absent in both, so this is the hook's whole manifest
   arriving, not `gh` alone varying. A's environment log shows
   `SessionStart:startup hook success` with an apt install of `gh`, `shellcheck`, `zsh`
   and then `just.tar.gz: OK` — the payload item for item.

   **Two earlier readings are now dead, and both were stated as settled at the time.**
   This file once said "It is not the environment" on one session's evidence — true, but
   unsupported then. The companion record once concluded the opposite — that `gh` is a
   property of an environment at a point in time — from `gh` 2.45.0 in every 09-07 run
   and rc 127 from the same environment id nine hours later. **It had already reversed
   that before merging**, and as merged it reads "the evidence points at the source repo,
   which installs it", with the hook named. This run is what turns "points at" into a
   measurement: same environment, same minute, same build, opposite answers — no clock or
   image drift accounts for it. The two records agree; only this one has the controlled
   run.

   **A hook firing is not the discriminator; whose hook is.** Arm B also ran a
   `SessionStart` hook — a guidance plugin injecting sentinel text and installing
   nothing. A probe that only asks "did a hook run?" gets `yes` in both arms and
   concludes wrongly.

   The mechanism is committed code, read directly in `bestdan/dotfiles` at
   `.claude/hooks/session-start.sh`:

   ```
   if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
     exit 0
   fi
   APT_PACKAGES=(shellcheck zsh gh)
   ```

   It declines to run outside the cloud, then apt-installs `gh` by name, and installs a
   pinned `just` further down. Eight runs across two sessions now agree: every
   `gh`-present run sourced `dotfiles`, every `gh`-absent run did not.

   **The `CLAUDE_CODE_REMOTE` gate is why the two-arm run had to report it.** The hook
   exits 0 when that variable is unset, so a `gh`-absent arm with it unset would mean the
   hook correctly declined — not that the source repo is irrelevant. Both arms read
   `true`, so neither result is void. **Any future probe of this must report it too.**
2. **`gh` was present in this session — see finding 1 for where from — and had no
   working credential in the session as provisioned**, reads 403 alongside writes.

   > **Superseded in its consequence, 2026-09-15.** This finding is still true about
   > `gh`. What it was taken to imply — that the session has no GitHub API access — is
   > false: plain `curl` with the ambient `$GH_TOKEN` read a dependency edge correctly.
   > **Read every "no usable `gh`" sentence below as a statement about the client, not
   > about the environment.** See
   > [2026-09-15: the API is reachable without `gh`](#2026-09-15-the-api-is-reachable-without-gh).

   So the
   2026-08-24 routine finding and this session's finding agree in effect: no usable `gh`.
   They differ in how far they got — that routine had no `gh` binary to try, this session
   had the binary and a dead token. **Do not read that as a session-versus-routine
   property**; finding 1 says what actually varies, and settles it.
   **Where the barrier sits is _not_ settled**, and the wording matters: an
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
     403 recorded in this file and in the companion record was measured against a **cloned source or
     an unattached repo**, never against a credentialed attach.
  3. **Something else neither probe looked for.**

  So read finding 2 as "not available in the session as provisioned" — **not** as "the
  proxy forbids it", and **not** as "`gh` cannot work in a cloud session". The question is
  **unanswered, not answered no.**

  > **Partly answered 2026-09-15, and reading 3 was the right one.** The 403 was never
  > the last word, because the API is reachable in a cloud session **without `gh` at
  > all** — `curl` with `$GH_TOKEN` read a dependency edge on the session's source repo.
  > So whatever the 403's cause, it is specific to `gh`'s auth path and is not a wall
  > around the environment. The three readings above remain the open question **for
  > `gh` specifically**, which is now a much smaller question than it looked: nothing
  > needs `gh` if `curl` works.

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
  section of the companion record. Its load-bearing rule: **capture the exit
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
