# What a cloud routine gives a plugin, and what `gh` will and won't serve

**Measured 2026-09-07** inside scheduled **routines** (not `claude --cloud`
sessions), Claude Code 2.1.263, environment `Linear Tidy Routine`. A dated
snapshot: it records what was true that day and is allowed to go stale.

Companion to
[`2026-09-05-cloud-session-plugin-and-proxy.md`](2026-09-05-cloud-session-plugin-and-proxy.md)
(a `claude --cloud` session) and
[`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md)
(a routine). It **corrects finding 2 of the 2026-09-05 doc** — see "What this
changes" below.

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
   run`, so the install is not re-paid nightly.

2. **Skills are addressed by their prefixed name.** `Skill` with
   `orchestrate-coders` returns `Unknown skill: orchestrate-coders`; the skill
   exists as **`workflow-skills:orchestrate-coders`**. This is a naming rule,
   not an availability signal — an unprefixed name fails identically whether or
   not the plugin is loaded. A probe that tests availability with a bare name
   measures nothing, which is exactly the error the first two probes in this
   series made.

3. **A mid-session `claude plugin install` does NOT register with the running
   agent.** Same commands, same exit codes, files on disk — and
   `Skill: workflow-skills:*` unavailable. The install has to land before the
   session starts, which is what a setup script gives you.

4. **`$CLAUDE_PLUGIN_ROOT` is empty in a Bash tool call**, before and after a
   successful install, in every run measured. So
   `python3 "$CLAUDE_PLUGIN_ROOT/commands/handlers/assets/linear-scan.py"`
   fails with `can't open file '/commands/handlers/assets/linear-scan.py'` —
   the path collapses. Consuming a plugin asset from raw shell needs the
   documented Glob fallback, or the caller should invoke the skill and let it
   resolve its own assets. Whether the variable is populated inside a plugin's
   own execution context was **not** measured here.

5. **`gh pr list` cannot work in a routine, at all.** It is a GraphQL query and
   GraphQL is not served:

   > HTTP 403: This GraphQL query (PullRequestList, sent by `gh pr list`) is not
   > enabled for this session — only the pinned set of PR-review operations is
   > served. Use REST via `gh api repos/{owner}/{repo}/...` instead.

   This is a property of the query type, not of credentials, so no amount of
   auth fixes it. The error names its own replacement.

6. **`gh` REST is scoped to the repositories attached to the session.** A repo
   that is not a source returns:

   > HTTP 403: GitHub access to this repository is not enabled for this session.
   > Use `add_repo` to request access.

   `gh api user` succeeds in the same session, because it is not repo-scoped —
   which is why an earlier reading of "gh works" was too generous. **Sources
   therefore double as GitHub access scope**, and dropping one to avoid a
   duplicate checkout also removes that repo from `gh`'s reach.

7. **The GitHub MCP surface is present and is not a claude.ai connector.** The
   nightly tidy's `mcp_connections` lists only Google-Drive, Linear, Slack,
   Todoist and visualize, and it calls `mcp__github__*` successfully anyway. It
   comes from the GitHub App installed for claude.ai/code, so it never appears
   in a routine's connector list and there is nothing to attach.

## What this changes

The 2026-09-05 doc's finding 2 reads _"`gh` exists in a cloud session but has no
working credential ... reads 403 alongside writes."_ In a **routine** the shape
is different and more specific: `gh` is credentialed, but REST is limited to
attached repos and GraphQL-backed subcommands are refused outright. Both docs
agree `gh pr list` is unusable; they disagree on why, and the why decides the
fix. Prefer this doc for routines and the 2026-09-05 doc for `--cloud` sessions
until someone re-measures the latter.

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
- **Whether `gh api repos/...` REST calls succeed for an attached repo.** Only
  the unattached 403 and the non-repo-scoped `gh api user` were exercised.

## Reproducing

One-shot routine in the target environment, `sources` deliberately excluding
the plugin repo so an install cannot be confused with a source checkout:

```
claude plugin list
test -n "$CLAUDE_PLUGIN_ROOT" && echo SET || echo EMPTY
Skill: workflow-skills:orchestrate-coders      # prefixed, or it proves nothing
gh api repos/<owner>/<unattached-repo>
gh pr list -R <owner>/<repo> --limit 1
```

`RemoteTrigger` is **not** available to a subagent — cloud-routine control is
main-session only, so this loop cannot be delegated.
