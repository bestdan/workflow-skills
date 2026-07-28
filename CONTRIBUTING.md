# Contributing

## Local dev loop

The gate's tools — `dprint`, `shellcheck`, `shfmt` — are pinned in `mise.toml`,
which is the single source CI and your laptop both install from. Get `mise`
itself from <https://mise.jdx.dev/getting-started.html>, then:

```sh
mise trust && mise install
```

Don't install them any other way. The pins are owned by `bestdan/dotfiles`'
`scripts/update-mise-deps.sh`, which opens a PR here when one moves; a
separately-installed `dprint` will disagree with the gate the moment the pin
changes.

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

### One-time setup: release token (required — `main` is protected)

`main` is a protected branch, and the built-in `GITHUB_TOKEN` **cannot** be added
to a branch bypass list. So the workflow pushes the release commit with a
fine-grained PAT exposed as the `RELEASE_TOKEN` secret. Without it the release
push is rejected and the workflow fails. Set it up once:

1. **Create the PAT** — GitHub → **Settings → Developer settings → Personal
   access tokens → Fine-grained tokens → Generate new token**:
   - **Resource owner** `bestdan`; **Repository access → Only select
     repositories → `workflow-skills`**.
   - **Permissions → Repository permissions → Contents: Read and write** (add
     **Workflows: Read and write** only if a release ever needs to modify files
     under `.github/workflows/`). Nothing else.
   - Pick an expiry, **Generate**, and copy the value.
2. **Store it as a secret** — repo **Settings → Secrets and variables → Actions
   → New repository secret**: name `RELEASE_TOKEN`, paste the value.
3. **Put the token's owner on the bypass list** so its pushes skip the PR/status
   requirements on `main`:
   - **Rulesets:** Settings → **Rules → Rulesets** → open the `main` ruleset →
     **Bypass list → Add bypass → Repository admin** (or add your user) → Save.
   - **Classic branch protection:** edit the `main` rule → under _Require a pull
     request before merging_ enable **Allow specified actors to bypass required
     pull requests** and add yourself; leave **Allow administrators to bypass**
     on so required status checks don't block the push.

The token acts as you (an admin), so admin bypass covers it. Renew the secret
before the PAT expires, or releases start failing. Note PAT pushes **do**
re-trigger workflows — that's why the release commit carries `[skip ci]` and the
job's `if:` guard exists; both are load-bearing here.

### One-time: the first run seeds a baseline

With no `v*` tag yet, the first qualifying merge only creates a
`v<current-version>` tag (no bump); every merge after that has a boundary to diff
against and bumps normally.

## Landing a stack of PRs

When you have a stack — PR **B** branched off PR **A**'s branch, **C** off **B** — land them **bottom-up**, one at a time, rebasing each survivor onto the new `main` after every merge. `main` is protected and every qualifying merge **auto-bumps the version** (see [Versioning](#versioning)): the Release workflow pushes a `chore: release vX.Y.Z [skip ci]` commit, so the `main` tip **moves after each landing**. A child still based on the pre-merge tip runs CI against the wrong tree and merges a stale base.

> **First, record each child's fork point.** Before you rewrite anything, capture where each child branched off its parent — step 4 needs this exact boundary, and rebasing the parent in step 2 moves the parent's branch ref out from under it: `git merge-base <branch-A> <branch-B>` (save the SHA as `<fork-B>`), one per parent→child edge.

1. **Retarget the bottom PR to `main`.** If it was opened against an intermediate branch, point it at `main`: `gh pr edit <A> --base main`.
2. **Rebase it onto current `main` and force-push**, so CI runs against the real base:
   ```sh
   git fetch origin && git checkout <branch-A> && git rebase origin/main && git push --force-with-lease
   ```
3. **Squash-merge once green:** `gh pr merge <A> --squash`. The PR title's Conventional-Commit type drives the version bump, so keep it accurate. The Release workflow then pushes a `chore: release … [skip ci]` commit, advancing `main` — **but it runs asynchronously, so wait for it before step 4.** `gh pr merge` returns as soon as the squash lands; fetching immediately grabs the _pre-release_ tip (the stale base this section warns about) and, if `main` requires up-to-date branches, bounces you into a redundant second rebase. Watch the run with `gh run watch $(gh run list --workflow=Release --branch=main --limit=1 --json databaseId --jq '.[0].databaseId')`, or just confirm the bump landed: `git fetch origin && git log origin/main -1 --oneline` should show the `chore: release …` commit.
4. **Rebase the next child onto the new `main` tip** — replaying _only its own_ commits — retarget it to `main`, force-push, and repeat from step 1 for the rest of the stack:
   ```sh
   git fetch origin && git checkout <branch-B> && git rebase --onto origin/main <fork-B> <branch-B> && git push --force-with-lease && gh pr edit <B> --base main
   ```
   A bare `git rebase origin/main` here is **wrong** once the parent was squash-merged from more than one commit: the squash commit's patch-id doesn't match the parent's individual commits, so Git replays them on top of the child — duplicating the parent's diff or forcing you to re-resolve conflicts you already settled. Rebasing `--onto` the recorded `<fork-B>` replays just the child's commits. Don't substitute the live `<branch-A>` ref for `<fork-B>`: step 2 rewrote it, so its merge-base is now the wrong (older) boundary. (Rebasing the whole stack from one checkout with `git rebase --update-refs` is an equivalent stack-aware alternative.)

Always use `--force-with-lease` (never a bare `--force`) so a concurrent push isn't clobbered. Conflicts that were already resolved in the parent usually drop out once the parent is merged and you rebase the child onto the updated `main`.

## Behavioral evals

`just eval` (and the manual **Evals** GitHub workflow) check that Claude
auto-invokes each skill from its naive prompt. They cost API tokens and are
nondeterministic, so they are **opt-in and never block a PR**. See
[`evals/README.md`](evals/README.md).
