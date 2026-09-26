---
created: 2026-09-25
---

# jira-promote — skip parent rollups

Proposal for the jira `/promote-tasks` flow (`commands/handlers/jira-promote.md`)
to skip **parent rollups**: issues `/break-down-task` has decomposed into
children. `linear-promote.md` step 5 already skips them; the jira flow does
not, so it scores and transitions a parent alongside its own children.

## What JQL can and cannot express

Unlike gh-issue, whose sub-issue link is not bulk-filterable
(`gh-issue-promote.md` step 3), Jira exposes the parent relationship to JQL, so
most of the skip can run server-side:

- `parent IS EMPTY` / `parent IS NOT EMPTY`, `parent = <KEY>`, and
  `parent IN (…)` are **native** JQL. On modern Jira Cloud the unified `parent`
  field covers both sub-tasks and epic children, and
  `issuetype IN subTaskIssueTypes()` is a native function for the sub-task
  issue types.
- There is **no native "has subtasks" predicate**
  ([JRACLOUD-67108](https://jira.atlassian.com/browse/JRACLOUD-67108)).
  `hasSubtasks()`, `subtasksOf()`, `parentsOf()` and `linkedIssuesOf()` are
  ScriptRunner `issueFunction` add-on functions and must **not** be assumed
  present.

## Proposed skip

One bulk sweep, with an exact check as its truncation fallback:

1. After the step-3 candidate query, run one extra
   `searchJiraIssuesUsingJql` over the project for children:
   `project = "<project>" AND parent IS NOT EMPTY`, `fields: ["parent"]`,
   `maxResults: 100`.
2. Collect the distinct `fields.parent.key` values into a parent-key set, and
   skip any step-3 candidate whose `key` is in it (reason `parent rollup`).
3. If the sweep returns exactly 100, the page may be truncated. Fall back to a
   per-candidate `parent = "<KEY>"` check (`maxResults: 1`) for any candidate
   not yet confirmed a parent.

The sweep is server-side filtered; only the set-membership test is
client-side.

This differs on purpose from `linear-promote.md` step 5, which runs an exact
per-candidate check (`parentId`, `limit: 1`) and no bulk sweep. Jira can
answer the whole project's parent set in one query, which saves one call per
candidate.
