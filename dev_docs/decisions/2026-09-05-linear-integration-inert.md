# 2026-09-05 — the over-close incident, and why this workspace's Linear integration never fired

A snapshot of one workspace on one date. The handler docs used to carry this
account inline, which made a workspace-specific finding read as a universal
fact to every consumer of the plugin. It lives here instead; the handlers link
to it.

## What the docs used to claim

Across nine files, the Linear handler docs asserted that Linear's GitHub
integration treats a bare `TEAM-NNN` id anywhere in a PR title or body as a
closing link, that it over-closed sibling issues as a result, and that it was
**disabled** because of that.

The completion architecture — the reconciler verbs, the `links` attachment, the
bare-id discipline in `linear-claim.md` — was built on top of that story.

## What was actually found

**Verified** on 2026-09-05, by query:

- `bestdan/finplan` and `bestdan/workflow-skills` each return `0` webhooks
  (`gh api repos/<owner>/<name>/hooks`). Re-checked 2026-09-07: still `0`.
- No issue in the PreThink workspace carries a Linear-created linkback.
- `bestdan/finplan#997` merged with its head branch
  `dan/pre-381-add-a-favicon-to-the-marketing-site` embedding an issue id —
  the one link form Linear documents as closing on merge — and `PRE-381`
  remained in `In Review` with `completedAt: null`.

Together these establish that the integration **never fired on these
repositories**. They do not establish why.

**Inferred, not verified:** that the cause is an identity split — Linear's
GitHub App installed under the `dan@prethink.io` GitHub identity, which cannot
see `bestdan/*` repositories. `GET /user/installations` returns 403 without an
app-authorized token, so the installation itself was never inspected. This
rests on the account owner's description, not on a query. Treat it as the
leading hypothesis, not a finding.

**Verified:** the over-closing was real, and came from somewhere else.
`bestdan/finplan` carried `.github/workflows/linear-close-on-merge.yml` from
2026-05-27 to 2026-09-03. On every merge to `main` it ran:

```sh
grep -oE '\bPRE-[0-9]+\b'
```

over the PR title and body, and moved **every** match to the team's `Done`
state. A PR citing an issue for context closed it. `bestdan/finplan#1157`
deleted the workflow.

## Linear's documented link behaviour

Sourced from Linear's public documentation (`linear.app/docs/github`), fetched
2026-09-05. **Not tested against this workspace** — the integration is inert
here, so there is nothing to test it against. Treat as documentation, not as an
observation:

- Head branch name contains the issue id — links, and closes on merge.
- Magic word + id in the PR title or description — links; a closing magic word
  closes on merge, a non-closing one does not.
- Issue id alone in the PR title — links, and does not close on merge.
- A bare id in the PR body is not documented as forming a link at all.

The last point is what the old story got backwards. It is also why the
bare-id discipline in `linear-claim.md` is kept as insurance against an
untested path rather than as a guard against observed behaviour.

## Why the distinction is load-bearing

The false version localises the danger inside Linear, where it is a vendor
problem and already handled. The true version says any repository can rebuild
the same bug in its own CI, which is what `bestdan/workflow-skills#462`
proposes to detect. The wrong attribution is what kept that gap invisible.

## Related

- `bestdan/workflow-skills#509` — the correction across the handler docs.
- `bestdan/workflow-skills#460` — `/find-false-closures` cannot see archived
  issues, so it cannot measure the damage this workflow did before 7-day
  auto-archive swept it out of scope.
- `bestdan/workflow-skills#462` — a `/doctor` check for consumer repos that
  have built their own merge-triggered close automation.
