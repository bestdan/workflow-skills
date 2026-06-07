# Contributing

## Local dev loop

Everything CI runs is also runnable locally through one entrypoint, so you never
discover a failure only after pushing.

```sh
just check     # the full deterministic gate (what CI runs on every PR)
just fmt       # auto-format everything with dprint
just validate  # only the repo-native structural/consistency validator
just eval      # gate + behavioral skill-triggering evals (opt-in, needs auth)
```

`just check` is just a thin wrapper around `scripts/check.sh` — the script is the
source of truth, and CI calls it directly.

## The gate (`just check`)

Three deterministic, blocking checks:

1. **`dprint check`** — formatting (config in `dprint.json`).
2. **`claude plugin validate . --strict`** — official manifest/frontmatter
   validation.
3. **`uv run scripts/validate.py`** — repo-specific rules the above don't cover:
   - skill/command/agent frontmatter shape; skill `name` (if set) matches its
     directory and the `^[a-z0-9-]+$` slug rules; `description` non-empty and
     ≤1024 chars; SKILL.md body ≤500 lines;
   - `plugin.json` and `marketplace.json` versions are present and **equal**;
   - the README "N skills, M commands, K subagent" sentence matches reality.

`scripts/validate.py` is dev/CI-only tooling (never shipped to plugin
consumers); its one dependency is hash-locked in `scripts/validate.py.lock`.

## Adding a skill

1. Create `skills/<name>/SKILL.md` with valid frontmatter (`description`
   required; `name`, if set, must equal `<name>`). Keep the body ≤500 lines —
   move detail into sibling reference files.
2. Update the component count in `README.md` ("What's in the box").
3. Add a trigger eval: `evals/prompts/<name>.txt` (a realistic prompt that
   **doesn't name the skill**) plus a row in `evals/manifest.tsv`. Skip this only
   for `user-invocable: false` skills.
4. Run `just check` (must pass) and, if you can, `just eval` to confirm the new
   skill auto-triggers.

## Adding a handler

When you add a task handler or teach an existing one a new verb (capture, list, promote, do, process), update the **handler capability matrix** in `commands/task-config.md` in the same PR — it's the single source of truth that `/task-config` reads to warn users about capability gaps, and it drifts silently if you don't.

## Versioning

Versions are **bumped automatically on merge to `main`** by the **Release**
workflow (`.github/workflows/release.yml`), driven by
[Conventional Commits](https://www.conventionalcommits.org/):

| Commit type on a merged commit         | Bump    |
| -------------------------------------- | ------- |
| `feat:`                                | `minor` |
| `fix:` / `perf:`                       | `patch` |
| `BREAKING CHANGE` in body, or `type!:` | `major` |
| `chore:`, `docs:`, `ci:`, `test:`, …   | none    |

A merge that contains only no-bump types ships **no release** — that's the
"meaningful changes only" filter. When a bump is due, the workflow updates
`plugin.json` **and** the matching `marketplace.json` entry together (kept in
sync as plain `X.Y.Z` — `validate.py` rejects a `v` prefix), prepends a grouped
`CHANGELOG.md` section, pushes a `chore: release vX.Y.Z [skip ci]` commit, tags
`vX.Y.Z`, and publishes a GitHub Release.

Preview what the next merge would do, locally and without writing anything:

```sh
just bump-preview   # python3 scripts/bump-version.py
```

The version/changelog math lives in `scripts/bump-version.py`; the workflow only
commits, tags, and publishes. To force a specific version, you can still bump
both manifest fields by hand in a normal PR — just keep them equal.

**Two setup caveats** (one-time):

- **Branch protection.** The workflow pushes the release commit straight to
  `main` using the built-in `GITHUB_TOKEN`. If `main` requires PRs or status
  checks, allow the `github-actions` bot to bypass them (or swap in a PAT /
  GitHub App token with bypass rights), otherwise the push is rejected.
- **First run seeds a baseline.** With no `v*` tag yet, the first qualifying
  merge only creates a `v<current-version>` tag (no bump); every merge after
  that has a boundary to diff against and bumps normally.

## Behavioral evals

`just eval` (and the manual **Evals** GitHub workflow) check that Claude
auto-invokes each skill from its naive prompt. They cost API tokens and are
nondeterministic, so they are **opt-in and never block a PR**. See
[`evals/README.md`](evals/README.md).
