# Changelog

All notable changes to this plugin. Sections are auto-generated from
[Conventional Commits](https://www.conventionalcommits.org/) on merge to
`main` by `.github/workflows/release.yml`.

## [1.16.0] - 2026-07-01

### Features

- [PRE-331] /task-config multi-project setup + scalar→list migration (#100) (dc9e7b4)

## [1.15.0] - 2026-06-30

### Features

- [PRE-330] projects-list schema + resolve-configured-projects helper (#99) (d596f79)

## [1.14.0] - 2026-06-30

### Features

- add Google Antigravity (agy) as a built-in reviewer (#98) (03d9e97)

## [1.13.1] - 2026-06-30

### Fixes

- close the Linear claim race with a token-comment lock (#96) (283c312)

## [1.13.0] - 2026-06-29

### Features

- add shared await-pr-review bot-watcher fixture (#90) (8cb11a3)

## [1.12.0] - 2026-06-28

### Features

- add /archive-tasks handler-dispatched verb (cadb084)

### Fixes

- apply co-review fixes (b56d009)

## [1.11.0] - 2026-06-25

### Features

- add GraphQL parent-rollup detection to promote handler (58ddd68)

### Fixes

- honor configured repo in parent-rollup GraphQL query (b62487a)

## [1.10.6] - 2026-06-25

### Fixes

- push claim label filter server-side (PRE-129) (514bbdb)

## [1.10.5] - 2026-06-24

### Fixes

- scope --no-claim pre-flight and harden title search (e2400f1)
- pre-flight in-flight PRs/branches and claim-then-verify at start of work (f17a685)

## [1.10.4] - 2026-06-24

### Fixes

- pre-flight in-flight PRs/branches and claim-then-verify at start of work (e0d3986)

## [1.10.3] - 2026-06-19

### Fixes

- retire gemini as a built-in reviewer (2ac0359)

## [1.10.2] - 2026-06-16

### Fixes

- skip dev_docs/tasks/ exclude for repo-pr handler (5facd65)
- make exclude appends idempotent and pass CI (8899bae)

## [1.10.1] - 2026-06-14

### Fixes

- prevent bare sibling issue IDs in PR bodies from auto-closing unrelated tasks (cc0e50d)

## [1.10.0] - 2026-06-14

### Features

- scope /promote-tasks to one project/epic/milestone by default (19f07c2)

### Fixes

- resolve pinned Linear project name for the scope report (b9f3663)

## [1.9.1] - 2026-06-11

### Fixes

- request Flagged field on direct-key claim lookup (252ff6a)
- exclude blocked issues from promote/claim candidates (2ceca5a)

## [1.9.0] - 2026-06-10

### Features

- add jira claim/execute split + pre-claim WIP gate (5352747)

### Fixes

- disambiguate claim transition when multiple In-Progress statuses exist (86bc792)
- correct WIP-gate JQL facts and gloss <base> in --no-claim (e0602e5)
- dprint formatting and invalid statusCategory JQL in jira WIP gate (8a55d49)

## [1.8.0] - 2026-06-10

### Features

- single /do-tasks execute path (jira-claim.md) (700b3fc)

### Fixes

- harden jira-claim race, base-branch, and bail-label steps (8169d95)

## [1.7.0] - 2026-06-08

### Features

- dynamic transition resolution + prompt-when-unset for /promote-tasks (bb8ca0b)

## [1.6.2] - 2026-06-08

### Fixes

- keep reviewer dispatch within documented matcher contract (ba0f27c)
- assemble reviewer input in one shell to avoid stale cross-sandbox temp reads (a6ab6e2)

## [1.6.1] - 2026-06-08

### Fixes

- scope linear WIP gate count by project (d75bc3e)

## [1.6.0] - 2026-06-08

### Features

- add /promote-tasks support against configured statuses (a02a4c5)

### Fixes

- resolve promote target status to a transition id (57847d3)
- correct self-defeating promote example and tighten docs (cf14483)

## [1.5.0] - 2026-06-08

### Features

- add claim/execute split + pre-claim WIP gate (b269cd4)

### Fixes

- resolve co-review findings on claim/execute split (cdde3e7)

## [1.4.0] - 2026-06-08

### Features

- add gh-issue single-execute path (9b22f67)

### Fixes

- make find/claim/branch steps implementable (de685b7)

## [1.3.0] - 2026-06-08

### Features

- add gh-issue promote handler (c2cb23e)

### Fixes

- exclude scored issues at query time (ddb4b0c)
