# Handler parity follow-ups

The handler capability matrix surfaced by the task-loop improvements work (the `/task-config` verbs × handlers matrix) stays jagged even after the planned parity tasks land: the Linear promote path and the gh-issue/jira list path fill some cells, but several `(handler, verb)` cells remain unsupported with no planned task behind them. This plan captures each of those end-state holes as a backlog todo so they aren't silently forgotten.

Each item below is a **triage/decision** todo, not committed implementation work. Several of these gaps are likely **deliberate non-goals** (the plan intentionally scoped tracker execution to Linear, and trackers manage their own batch/promotion through their own UIs). The outcome of each task is to decide whether to build the cell **or** explicitly record it as a non-goal in the matrix/plan.

- [[parity_gh_issue_promote]] — gh-issue `promote` (`/promote-tasks`)
- [[parity_gh_issue_execute]] — gh-issue `do` / execute single (`/do-tasks`)
- [[parity_gh_issue_batch]] — gh-issue `process` / batch (`/do-tasks --all`)
- [[parity_jira_promote]] — jira `promote` (`/promote-tasks`)
- [[parity_jira_execute]] — jira `do` / execute single (`/do-tasks`)
- [[parity_jira_batch]] — jira `process` / batch (`/do-tasks --all`)
- [[parity_linear_batch]] — linear `process` / batch (`/do-tasks --all`)
