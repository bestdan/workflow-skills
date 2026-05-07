# workflow-skills

A Claude Code plugin bundling general-purpose workflow skills. These are not analysis-specific — they're the day-to-day collaboration tools: reviewing PRs alongside other reviewers, and persisting non-trivial plans as markdown so they can be edited and worked through PR by PR.

## Install

```sh
/plugin marketplace add bestdan/workflow-skills
/plugin install workflow-skills@workflow-skills
```

## Skills

| Skill | Trigger | What it does |
|---|---|---|
| **co-review** | `/co-review` | Produce your own review of a PR, reconcile it against existing GitHub bot/human comments via an independent sub-agent, auto-fix high-confidence items, and surface judgment calls back to you. |
| **plan-with-docs** | `/plan-with-docs`, or after approving a plan in plan mode | Write a multi-step implementation plan as markdown files under `dev_docs/todo/<name>_plan/` (one file per PR-sized step) instead of printing it inline, then refine through clarifying questions. |

## Staying Up to Date

Third-party marketplaces have auto-update disabled by default. Enable auto-update in the `/plugin` UI (Marketplaces tab), or update manually:

```sh
/plugin marketplace update bestdan/workflow-skills
/reload-plugins
```

## License

MIT
