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
