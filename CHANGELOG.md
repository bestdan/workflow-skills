# Changelog

All notable changes to this plugin. Sections are auto-generated from
[Conventional Commits](https://www.conventionalcommits.org/) on merge to
`main` by `.github/workflows/release.yml`.

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
