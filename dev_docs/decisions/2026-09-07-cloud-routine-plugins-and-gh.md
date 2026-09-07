# What a cloud routine gives a plugin, and why `gh` reaches GitHub for nothing

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

6. **`gh` REST refuses every repo-scoped path, attached or not — so the REST
   the GraphQL refusal names is not a way round it.** Both cases were measured,
   and they differ only in the message. Unattached (`bestdan/workflow-skills`
   before it was a source, run `cse_011MSb2bYVcG7QfzDb6RP33J`):

   > HTTP 403: GitHub access to this repository is not enabled for this session.
   > Use `add_repo` to request access.

   Attached (the same repo once the environment cloned it as a source, run
   `cse_01M9WzAbESA3hSwBZuYWczNJ`, `gh api repos/bestdan/workflow-skills/pulls/487`):

   > HTTP 403: GitHub access is not enabled for this session. An org admin must
   > connect the Claude GitHub App for this organization.

   The second refusal is **org-level**, so attachment is not what the 403 turns
   on and adding a source buys no `gh` access. `gh api user` succeeds in both
   sessions because it is not repo-scoped — which is why an earlier reading of
   "gh works" was too generous, and why `gh api user` is worthless as a health
   check.

   An earlier revision of this document had finding 6 the other way round: it
   read the unattached 403 as evidence of an attachment gate and inferred that
   sources double as GitHub access scope. The attached-repo measurement above
   falsifies that inference. It is recorded rather than deleted because the
   inference was load-bearing — it was the argument against dropping a source.

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
is not served and repo-scoped REST is refused at the org level. So both
documents agree on the operational rule, **`gh` is not a channel in the cloud**,
and the routine measurements say the credential is not the reason. Prefer this
doc for routines and the 2026-09-05 doc for `--cloud` sessions until someone
re-measures the latter.

The practical consequence for `/sweep-for-complete`: `mcp__github__*` is the
only working GitHub channel in a routine, and there is no second one to fall
back to. A run without those tools resolves nothing — which is what the handler
said before this document existed, though for a reason that turned out to be
wrong.

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
- **Whether connecting the Claude GitHub App for the org would make repo-scoped
  REST work.** The attached-repo 403 asks for exactly that, and nobody has
  tried it. It is the one action that might turn `gh` back into a channel.
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
