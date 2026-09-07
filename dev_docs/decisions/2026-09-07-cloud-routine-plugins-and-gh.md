# What a cloud routine gives a plugin, and what `gh` needs before it serves

**Measured 2026-09-07** inside scheduled **routines** (not `claude --cloud`
sessions), Claude Code 2.1.263, environment `Linear Tidy Routine`. A dated
snapshot: it records what was true that day and is allowed to go stale.

Companion to
[`2026-09-05-cloud-session-plugin-and-proxy.md`](2026-09-05-cloud-session-plugin-and-proxy.md)
(a `claude --cloud` session) and
[`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md)
(a routine). It **supersedes the 2026-09-05 doc for routines** and reframes why
`gh` fails there — see "What this changes" below. It does not correct that doc,
which measured a different environment and reached the same operational rule.

## Why this exists

`agents/routines/nightly-linear-tidy.md` (in `bestdan/dotfiles`) re-implements
`/find-false-closures`, `/sweep-for-complete` and `/archive-tasks` as prose,
because a routine had no way to invoke them. Three consecutive runs then
executed three materially different algorithms: 2026-09-02 ran the title-search
and branch fallbacks, 2026-09-06 never opened `linear-sweep-complete.md` and
resolved PRs from `links` attachments only, 2026-09-07 refused on a dead Linear
connector. The steps that never drifted are the two that call a **script**
(`linear-scan.py`, `linear-archive.py`); the step that drifted is the one
written as a clause of prose.

So: can a routine invoke the plugin instead of describing it?

## What this settles

1. **An environment setup script running `claude plugin marketplace add` +
   `claude plugin install` DOES make the plugin available to the session.**
   Both commands run non-interactively (exit 0, no TTY needed with
   `< /dev/null`), the env log orders them `Running setup script` → `Setup
   script completed` → `Starting Claude Code`, and the started session lists
   the plugin's skills. A later run logged `Setup script cached from previous
   run`, which suggests the install is not re-paid nightly — what that cache
   covers (script text, installed artifacts, or the filesystem layer) was not
   established.

2. **Skills are addressed by their prefixed name.** `Skill` with
   `orchestrate-coders` returns `Unknown skill: orchestrate-coders`; the skill
   exists as **`workflow-skills:orchestrate-coders`**. This is a naming rule,
   not an availability signal — an unprefixed name fails identically whether or
   not the plugin is loaded. A probe that tests availability with a bare name
   measures nothing, which is exactly the error the first two probes in this
   series made.

3. **A mid-session `claude plugin install` does NOT register with the running
   agent.** Same commands, same exit codes, files on disk — and `ListPlugins`
   and `ListSkills` both returned empty after the install. The install has to
   land before the session starts, which is what a setup script gives you.
   (That run's `Skill` call used the **bare** name, so per finding 2 it is not
   part of this evidence; the empty listings are.)

4. **`$CLAUDE_PLUGIN_ROOT` is empty in a Bash tool call**, before and after a
   successful install, in every run measured. So
   `python3 "$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/linear-scan.py"`
   fails with `can't open file '/commands/handlers/assets/linear-scan.py'` —
   the path collapses. Consuming a plugin asset from raw shell needs the
   documented Glob fallback, or the caller should invoke the skill and let it
   resolve its own assets. Whether the variable is populated inside a plugin's
   own execution context was **not** measured here.

5. **The GraphQL-backed `gh` subcommands cannot work in a routine, at all.**
   `gh pr list` is a GraphQL query and GraphQL is not served:

   > HTTP 403: This GraphQL query (PullRequestList, sent by `gh pr list`) is not
   > enabled for this session — only the pinned set of PR-review operations is
   > served. Use REST via `gh api repos/{owner}/{repo}/...` instead.

   This is a property of the query type, not of credentials, so no amount of
   auth fixes it. **`gh pr view` is refused the same way** — measured on
   `gh pr view <url> --json number,url,state,mergedAt` in run
   `cse_01M9WzAbESA3hSwBZuYWczNJ`, which matters because that is the merge
   verification `/sweep-for-complete` step 4 rests on. The error names its own
   replacement, REST — but see finding 6: the replacement is refused too.

6. **Cloning a repo as an environment source does not give `gh` API access to
   it — that is a separate, credentialed attach, and no run measured here ever
   performed one.** So the REST the GraphQL refusal names never worked in any
   of these runs. Both refusals below are real; what neither of them measures
   is a properly provisioned repo.

   Not a source (`bestdan/workflow-skills` before the environment cloned it,
   run `cse_011MSb2bYVcG7QfzDb6RP33J`) — quoted in full, because the second
   sentence is the one that matters:

   > HTTP 403: GitHub access to this repository is not enabled for this session.
   > Use `add_repo` to request access. If `add_repo` answers that read access is
   > already available and you need GitHub API or write access, call `add_repo`
   > again with `access:"push"` to attach the repository with credentials.

   A source (the same repo once the environment cloned it, run
   `cse_01M9WzAbESA3hSwBZuYWczNJ`, `gh api repos/bestdan/workflow-skills/pulls/487`):

   > HTTP 403: GitHub access is not enabled for this session. An org admin must
   > connect the Claude GitHub App for this organization.

   **The two bodies are genuinely different, and only the first carries the
   `add_repo` continuation.** The source-cloned body is the org-App sentence
   and its `documentation_url`, with no `access:"push"` guidance appended —
   verified against the raw tool result in `cse_01M9WzAbESA3hSwBZuYWczNJ`, not
   inferred from a summary. So the two cases are distinguished somewhere and
   answered differently, and a session that only ever sees the org-App form is
   not reading a truncated message. Corroborated across repos and session
   kinds: the 2026-09-05 `claude --cloud` session got a byte-identical body,
   `documentation_url` included, from `gh api repos/bestdan/dotfiles/issues/699`
   — a different repo, likewise a cloned source, likewise never attached with
   credentials.

   What follows is narrow. A source clone buys **read** access, while the
   GitHub API needs the repo attached **with credentials**, so being cloned
   into `/home/user/` is not evidence of being attached, and the org-App
   refusal is what an unprovisioned repo looks like rather than proof that
   provisioning would fail. What it is **not** is a reading of where the
   barrier sits: once a repo is a cloned source the message stops naming
   `add_repo` and names the org App instead, which is equally consistent with
   the remaining barrier being org-level and with the message simply being
   coarser. Both readings have been asserted in this file's history and neither
   was measured. `gh api user`
   succeeds throughout because it is not repo-scoped — which is why an earlier
   reading of "gh works" was too generous, and why `gh api user` is worthless
   as a health check.

   **What this does and does not license.** It licenses the operational rule
   below — as these environments are provisioned today, `gh` serves no
   repo-scoped call and `mcp__github__*` is the only working channel. It does
   **not** license "`gh` cannot work in the cloud". Nobody has run `add_repo`
   with `access:"push"`, and nobody has connected the App the second message
   asks for.

   **`gh auth status` is worse than worthless: it reports the failure and exits
   0.** Measured in `cse_016MBzxJfhs7w8pgwt1k2Hjd` as
   `gh auth status 2>&1; echo "exit:$?"`:

   ```
   github.com
     X Failed to log in to github.com using token (GH_TOKEN)
     - Active account: true
     - The token in GH_TOKEN is invalid.
   exit:0
   ```

   So a preflight gating on the exit code reads a pass while every repo-scoped
   call fails; one gating on the output does not. **Never gate on `gh`'s exit
   code in a cloud environment.** Independently reproduced in a `claude --cloud`
   session on 2026-09-05 (`session_01NjXjJLn92VsumHpC1FsdFo`) — same text,
   same rc — so this is not routine-specific. `GH_TOKEN` and `GITHUB_TOKEN`
   both read as the literal string `proxy-injected` there.

   That cross-check also carries the attached-repo case: the 2026-09-05 session
   got the identical org-level 403 from `gh api repos/bestdan/dotfiles/issues/699`
   on a repo that **was** its attached source, with the write refused the same
   way, while `mcp__github__issue_write` succeeded on the same issue. Two
   session kinds, two days apart, same wall — and in both, the repo was a
   cloned source that nobody had attached with credentials, so the two
   observations agree without either one settling what a credentialed attach
   would do.

   This finding has been wrong twice, in opposite directions, and both errors
   are recorded because the shape recurs. The first revision read the
   not-a-source 403 as an attachment gate and concluded sources double as
   GitHub access scope — the argument against dropping a source. The second
   read the source-clone 403 as proof that attachment is irrelevant. Both
   mistook **cloned as a source** for **attached with credentials**, which the
   first refusal says in as many words are different things. The rule the
   evidence actually supports is narrower than either: a source clone does not
   grant API access, and the credentialed path is untested.

7. **The GitHub MCP surface is present and is not a claude.ai connector.** The
   nightly tidy's `mcp_connections` lists only Google-Drive, Linear, Slack,
   Todoist and visualize, and it calls `mcp__github__*` successfully anyway. It
   comes from the GitHub App installed for claude.ai/code, so it never appears
   in a routine's connector list and there is nothing to attach.

## What this changes

The 2026-09-05 doc's finding 2 reads _"`gh` exists in a cloud session but has no
working credential ... reads 403 alongside writes."_ A routine reaches the same
practical place by a different route: `gh` **is** credentialed there — `gh api
user` answers — yet no repo-scoped call of any kind succeeds, because GraphQL
is not served and repo-scoped REST wants a repo attached with credentials that
neither session had. So both documents reach the same operational rule —
**`gh` is not a channel in the cloud as either environment was set up** — by
different routes, and the routine measurements say a bad credential is not the
reason. Prefer this doc for routines and the 2026-09-05 doc for `--cloud`
sessions until someone re-measures the latter.

The practical consequence for `/sweep-for-complete`: today, `mcp__github__*` is
the only working GitHub channel in a routine, and a run without those tools
resolves nothing — which is what the handler said before this document existed,
for a reason that has now been wrong twice. The rule is the same; what changed
is that it now rests on how these environments are provisioned, so provisioning
one properly is the thing that would change it.

## What this does NOT settle

- **Whether `enabled_plugins` / `account_plugins` / `account_skills` on the
  routine object do anything.** They are undocumented, absent from the public
  API reference, and empty in every routine inspected. Not relied on here.
- **Whether account-level "synced plugins" work for routines.** Documented as
  `<name>@synced`, untried. It is the one path that does not depend on a
  restart, which matters because of the next point.
- **Whether the documented `.claude/settings.json` path could ever work in a
  routine.** [anthropics/claude-code#63028](https://github.com/anthropics/claude-code/issues/63028)
  reports declared plugins inactive until a **second** session, with
  `/reload-plugins` unavailable in cloud. A routine only ever has a first
  session, so that workaround is structurally unavailable — but this was not
  measured here, only read.
- **Whether a properly provisioned repo serves `gh` REST at all.** This is the
  big one, and every measurement here is silent on it, because no run had a
  repo attached with credentials. Two untried actions, each named by one of the
  refusals: `add_repo` with `access:"push"`, and connecting the Claude GitHub
  App for the org. Until one is tried, "`gh` REST does not work in the cloud"
  is a statement about how these environments happen to be set up, not about
  the platform — and this document must not be cited for the stronger claim.
  Note the two refusals differ by case, so something is distinguishing them and
  answering each on its merits rather than blanket-blocking.

  **State it as unanswered, not as no.** "Can a dispatched session reach GitHub
  through `gh`?" has not been tested, because the one experiment that would
  answer it — `add_repo` with `access:"push"` against a repo in one of these
  environments — provisions credentials on a real account and nobody has run
  it. Everything above establishes only that `gh` fails without it. That
  experiment is also what would move `gh-issue.remote_batch` off `false` for a
  reason other than caution.
- **Whether `gh` is reliably present.** It answered `gh --version` in every run
  inspected here, but a second session reported it missing in three of five of
  its own runs. Not reproduced, and worth knowing before any handler leans on
  `gh` being installed.

## Reproducing

One-shot routine in the target environment. Run it **twice**, once with the
target repo excluded from `sources` and once with it included — the attached
and unattached cases return different 403s, and running only one of them is how
the first revision of finding 6 reached the wrong conclusion:

```
claude plugin list
test -n "$CLAUDE_PLUGIN_ROOT" && echo SET || echo EMPTY
Skill: workflow-skills:orchestrate-coders      # prefixed, or it proves nothing
gh api user                                    # succeeds either way — proves nothing
gh api repos/<owner>/<repo>                    # the repo-scoped read
gh api repos/<owner>/<repo>/pulls/<n>          # ditto, the merge-check shape
gh pr list -R <owner>/<repo> --limit 1
gh pr view <pr-url> --json number,state,mergedAt
```

`RemoteTrigger` is **not** available to a subagent — cloud-routine control is
main-session only, so this loop cannot be delegated. Reading a past run is
usually cheaper than commissioning a new one: `RemoteTrigger` `list_runs` on
the probe routine, then `get_run_log` on the session id, returns the verbatim
tool output. Every measurement in this document is quoted from such a log, and
the run ids are named at the findings that rest on them.

### The probe that would settle the open question

Designed jointly with the session that measured the 2026-09-05 `--cloud`
environment, so that whoever runs it first runs the same thing. It provisions
credentials on a real account, so it is the account owner's call, not an
agent's.

- **Both cases in one run** — a source-cloned repo and a not-a-source repo.
  Conflating the two is how finding 6 went wrong twice.
- **Both provisioning states for the same repo** — before and after `add_repo`
  with `access:"push"`. Without the before, a pass is not attributable to the
  attach.
- **Body _and_ exit code for every call**: `cmd 2>&1; echo "rc=$?"`. `rc=0` on
  a failure is the only behavioural finding this whole investigation produced;
  a probe capturing stdout alone rebuilds that blind spot.
- **Calls**: `gh auth status`, `gh api user`, a repo-scoped `gh api` read and a
  write, plus one `mcp__github__*` equivalent as the known-good control.
- **Verbatim bodies, never summaries.** Today the `documentation_url` and a
  second sentence each decided a finding.
- **Record the env header** — sources, and whether a setup script ran or was
  cached.

The generalisable lesson, and the reason two documents were corrected rather
than one: every error here was caught because someone handed over the raw text
and the run id instead of the conclusion drawn from it. Quote the bytes.
