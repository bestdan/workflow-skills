---
created: 2026-10-23
---

# Can `acli` feed the task dashboard?

**Measured 2026-10-23** on the work laptop. This is a dated snapshot that tests
the unverified claims in the task dashboard's design, which the live doc
[`../task-dashboard.md`](../task-dashboard.md) has since replaced.
Setup: macOS (Darwin 27.0.0), acli 1.3.19-stable, gh 2.102.0, a Jira Cloud site
with OAuth login. The site, the GitHub org, the board id and project keys are
withheld, because this repo is public. Counts, timings and shapes are as
measured.

**Neither design-killer fired.** acli runs fine under `launchd`, and every field
the design wants is reachable. Most of them can't come from `search`, though.
Its field allowlist leaves out every date, link, parent and custom field, so the
sync has to search for keys, then fetch each issue with `view`.

## Verdicts

| # | Item                         | Verdict                                                                         |
| - | ---------------------------- | ------------------------------------------------------------------------------- |
| 1 | Fields come back from search | Works, with plan B: search returns keys, `view` per issue returns fields        |
| 2 | Pagination                   | Works (`--paginate`)                                                            |
| 3 | Time in status               | Plan B: `statuscategorychangedate` via `view`. acli can't read the changelog    |
| 4 | Auth with no terminal        | Works: both `launchd` and `env -i` succeed                                      |
| 5 | Scope queries                | Works. Absolute dates are read in the profile timezone, so use relative windows |
| 6 | `gh` against work GitHub     | Works, with plan B: `gh search prs` has no `headRefName`, so use GraphQL search |
| 7 | Board sprints                | Works: `acli jira board list-sprints --id <board> --state active --json`        |

## Custom field ids (tested site)

| Field   | Id                  | How found                                                                                 |
| ------- | ------------------- | ----------------------------------------------------------------------------------------- |
| Sprint  | `customfield_10303` | `view --fields '*all'`: the only array of objects carrying `boardId` and `endDate`        |
| Flagged | `customfield_10007` | `view` of an issue matched by `Flagged is not EMPTY`: an array of `{value: "Impediment"}` |
| Team    | `customfield_11300` | An object shaped `{id, name, title, avatarUrl, isShared, isVerified, isVisible}`          |

acli has no field-listing command: `acli jira field` offers only create,
update, delete and restore. `names` and `schema` come back empty in `view
--json`, so no output maps ids to names. These ids were identified by shape and
have to be configured per site. Another site needs its own ids, either by the
same shape-matching or from the REST `/field` endpoint.

## 1. Fields from search

`search --fields '*all'` fails with `field '*all' is not allowed`. Probing one
field at a time, `search` accepts only: `key`, `summary`, `status`,
`assignee`, `reporter`, `priority`, `labels`, `issuetype`, `description`,
`creator`. It rejects everything else tried: `duedate`, `parent`,
`issuelinks`, `created`, `updated`, `statuscategorychangedate`,
`resolutiondate`, `components`, `fixVersions`, `resolution`, `project`,
`watches` and every custom field. The rejection comes back in about 0.6s,
which suggests the allowlist is enforced in the client.
`statusCategory` is still reachable through search, at
`fields.status.statusCategory.key`.

**`view` accepts every field the design needs**, but takes one key per call. A
comma-separated list of keys fails with "Issue does not exist". A serial `view`
takes 0.74–1.36s, about 0.9s typically. 16 views at `xargs -P 8` took 2.4s.

Shapes from `view`:

```
status                   {id, name, …, statusCategory:{id, key, name, …}}
statuscategorychangedate "<iso8601>"   (offset form, e.g. …-0400)
created / updated        "<iso8601>"
duedate                  null | "<yyyy-mm-dd>"
parent                   {id, key, fields:{summary, status:{…statusCategory}, priority, issuetype:{…hierarchyLevel}}}
labels                   [<string>]
issuelinks               [{id, type:{name, inward, outward},
                           outwardIssue | inwardIssue: {key, fields:{summary, status:{…statusCategory:{key}}, priority, issuetype}}}]
<sprint field>           null | [{id, name, state:"active|closed|future", boardId, goal, startDate, endDate}]
<flagged field>          null | [{value:"Impediment", id, self}]
```

- **Each link carries the linked issue's key and its `statusCategory.key`
  inline**, so blockers resolve without a second fetch.
- **The sprint field lists every sprint the issue has ever been in**, not just
  the current one. Of 241 issues with one, the array lengths were 1 (181), 2
  (35), 3 (19), 4 (2), and one each of 6, 7, 8 and 14. Pick the element whose
  `state` is `active`.

## 2. Pagination

`search --json` returns one default page of 30. `--paginate` returned all 287
assigned issues (287 unique keys) in 5.4s. A `--limit` above 30 also pages.
`--count` prints only a human-readable line, even with `--json`. Parse
`Number of work items in the search: N` from it.

## 3. Time in status

`statuscategorychangedate` is rejected by `search` but returned by `view`. It
was present on 515 of 529 issues in the union pull; the other 14 are older
issues. acli has no way to read the changelog: `view --fields changelog`
returns nothing, and there is no expand flag. So "stuck for N days" is cheap
at the category level, because the plan-B `view` already fetches it. A move
between two statuses in the same category, such as In Progress to In Review,
does not reset it. Time in a specific status would need the REST changelog,
not acli.

## 4. Auth with no terminal

- **Where the credential lives:** the macOS login keychain, as a generic
  password with service `acli`. `~/.config/acli/jira_config.yaml` (mode 0600)
  holds only the profile: site, cloud id, account id, email and
  `auth_type: oauth`. No token.
- **Test:** a throwaway LaunchAgent with `RunAtLoad`, bootstrapped into
  `gui/<uid>`, ran a `search` with absolute binary paths.

| Run                                        | exit | items | secs | env vars |
| ------------------------------------------ | ---- | ----- | ---- | -------- |
| `launchd` (gui domain)                     | 0    | 2     | 4    | 15       |
| `env -i HOME="$HOME" /bin/sh … </dev/null` | 0    | 2     | 1    | 4        |

An unattended run needs only `HOME` and an unlocked login keychain. A gui-domain
LaunchAgent starts only after login, so the keychain is unlocked. **Not tested:**
whether the OAuth refresh token expires over several days. If it does, every
sync fails until `acli jira auth login` is run interactively.

## 5. Scope queries

| Scope                                 | Count | Time  |
| ------------------------------------- | ----- | ----- |
| Assigned                              | 287   | 1.49s |
| Sprint (`openSprints()`, one project) | 50    | 1.23s |
| Reported/watched                      | 587   | 1.65s |
| Reported/watched, done-window applied | 282   | 1.28s |

`watcher = currentUser()` is accepted on this site.

**Absolute JQL dates are read in the Jira profile timezone.** The newest
assigned update was `16:54:43-0400` (20:54Z):

| `updated >=`                      | Count |
| --------------------------------- | ----- |
| `"<date> 20:50"` (UTC)            | 0     |
| `"<date> 16:50"` (local)          | 4     |
| `-2405m` (same instant, relative) | 4     |

Relative windows agree with the local-time form. `updated` can't come back
from `search` either, so the sync can't take a high-water mark from the
results. That leaves a relative window as the only straightforward cursor.

**Full pull of the union**, with every item-1 field:

| Step                           | Result                                                                                     |
| ------------------------------ | ------------------------------------------------------------------------------------------ |
| Issues in the union            | 529 (667 without the done window)                                                          |
| `search --paginate`, keys only | 10.4s                                                                                      |
| 529 × `view` at `xargs -P 8`   | 90.3s, 5 failures, all fixed by a serial retry                                             |
| Raw JSON                       | 5.1 MB                                                                                     |
| Spread                         | 12 project keys. 273 issues have links (562 link records, 6 link types). 488 have a parent |

The failures printed `failed to fetch work item details`: transient, about 1%
of calls at 8-way concurrency.

## 6. `gh` against work GitHub

`gh` uses one personal account, which reaches both personal repos and the work
org on github.com.

- `gh search prs` has no `headRefName` field, so it gives no branch names.
- `--author @me` returned 220 open PRs: 141 personal, 58 in the work org and 1
  elsewhere. A dashboard for work has to filter by org.
- `--owner <work org>` returned 68 PRs in 3.2s.
- `--review-requested @me` returned 32 PRs (26 in the work org) in 3.1s.
- `gh pr view` returns `reviewDecision` and `statusCheckRollup`. The rollup
  mixes `CheckRun` and `StatusContext` entries; the sample had 33.

**Plan B for branch names:** one paginated GraphQL `search(type: ISSUE)`
returned `title headRefName isDraft reviewDecision` for all 68 work-org PRs in
5.7s. Adding `commits(last:1){…statusCheckRollup{state}}` at `first:100` made
GitHub return an HTML error page after 12s. Fetch CI state per PR, or keep the
page size small.

**Joining PRs to Jira keys** on `[A-Z][A-Z0-9]+-\d+`, across the 68 work-org
PRs:

- The title matched 20.
- The branch matched 1 case-sensitively and 11 case-insensitively, because
  branches carry lowercase keys.
- A case-insensitive pattern also matches strings like `v2-1`, so it needs an
  allowlist of known project keys.

65 of the 68 are drafts.

## Other findings

- `view --json` with stdout sent to `/dev/null` fails (`failed to output
  command result in JSON format`, exit 1). Sending it to a file or a pipe works.
- Every acli call makes network calls to `auth.atlassian.com`,
  `as.atlassian.com` and `api.atlassian.com`, `--version` included. Inside the
  Claude Code sandbox each one needs the escape; under `launchd` that doesn't
  apply.
- The `search` JSON wraps each issue in a full envelope (`changelog`,
  `editmeta`, `names`, …, all null or empty). Parse only `key` and `fields`.
- **Plan C**, if the `view` fan-out proves too slow or flaky: REST
  `/rest/api/3/search/jql` returns any fields in one paginated call. It needs
  its own API token, because acli's OAuth token is stored in the keychain for
  acli alone.
