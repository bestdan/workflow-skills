# gh fixtures

Captured on the Mac mini on 2026-10-23 from the personal account's own open PRs,
in public `bestdan/*` repos only. No work data.

| File                        | Source                                                                                                                                                                                                                                                                                                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `search_page.json`          | Real. The GraphQL `search` for `is:pr is:open author:@me org:bestdan` (`first:50`), trimmed to four public-repo nodes taken from across its pages. `issueCount` and `pageInfo` are the first page's, as returned, so `hasNextPage` is `true`.                                                                                                                      |
| `rollup_checkrun_only.json` | Real. `gh pr view https://github.com/bestdan/workflow-skills/pull/1077 --json statusCheckRollup`: `CheckRun` entries only, two of them `SKIPPED`.                                                                                                                                                                                                                  |
| `rollup_mixed.json`         | **Hand-written.** None of the 32 open public-repo PRs carried a `StatusContext`, so there was no mixed rollup to capture. The `CheckRun` entries copy the real shape above; the `StatusContext` entry uses the fields `gh` selects for it (`context`, `state`, `startedAt`, `targetUrl`). The adapter reads only `__typename`, `status`, `conclusion` and `state`. |

The search fixture's other 147 nodes were dropped rather than kept because most
of them are in private repos.

The `fleet_*` files feed `github_fleet.py`. They were captured on 2026-10-23
from the public `bestdan/workflow-skills` only:

- `fleet_issues_search.json` is real: the GraphQL `search` for open
  `auto:human-review-needed` issues (`first:3`), with `hasNextPage` set to
  `false`.
- `fleet_review_search.json` is real: PRs #1114-#1117 from the search
  `is:pr repo:bestdan/workflow-skills`, with `headRefOid`,
  `closingIssuesReferences` and `body`, each
  body cut after its first `## What & why` paragraph, and `hasNextPage` set
  to `false`. None carries a `## Decision brief`, so the tests add one to a
  copied node.
- `fleet_designs_list.json` is real: the contents API listing of
  `dev_docs/designs`, cut to three entries.
- `fleet_design_proposed.json` and `fleet_design_accepted.json` are real
  contents API responses for one design at `status: proposed` and one at
  `status: accepted`. Each `content` was re-encoded to keep only the front
  matter and title, since only the front matter and the first `#` heading are parsed.
- `fleet_milestone_13.json` and `fleet_milestone_14.json` are real
  `repos/bestdan/workflow-skills/milestones/<n>` responses, the milestones
  those PRs' issues sit in. Neither description has an `approved: yes` line;
  the tests append one to a copy.
- `fleet_comments.json` is real: the comments query for PR #1118, which had
  none, with the `viewer` login it ran as. Co-review writes no stamp yet, so the tests add stamped comments to a
  copy.
- `fleet_closed_search.json` is real: the closed-PR search
  `is:pr is:closed author:@me closed:>=2026-10-20 repo:bestdan/workflow-skills`
  (`first:3`), with `hasNextPage` set to `false`. All three merged, so the
  tests clear `mergedAt` on a copy for a PR closed without merging.
