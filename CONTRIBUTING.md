# Contributing

New to the repo? Read [`AGENTS.md`](AGENTS.md) first — it's the short map of
what lives where and which conventions are load-bearing. This file is the
how-to: setting up, running the gate, and adding things.

## Local dev loop

The gate's tools — `dprint`, `shellcheck`, `shfmt` — are pinned in `mise.toml`,
which is the single source CI and your laptop both install from. Get `mise`
itself from <https://mise.jdx.dev/getting-started.html>, then:

```sh
mise trust && mise install
```

Don't install them any other way — a separately-installed `dprint` will disagree
with the gate the moment the pin changes. The pins are hand-bumped; edit
`mise.toml` and run `just check` before pushing.

The Bats suite lives in git submodules under `test/vendor/`, so a clone made
without `--recursive` fails the gate's shell steps with `bats submodules
missing`. CI checks out recursively; on your laptop, run:

```sh
git submodule update --init --recursive
```

(`scripts/check.sh` calls `scripts/ensure-bats.sh` first and will recover a
fresh `git worktree add` on its own — but a plain clone is faster to fix here.)

Those submodules also mean you can't tear a worktree down with a plain
`git worktree remove <path>` once you're done with it — git refuses with
`fatal: working trees containing submodules cannot be moved or removed`.
Before adding `--force` to get past that, confirm both the worktree itself
_and_ its submodules are clean — `--force` skips git's own uncommitted-changes
check, deletes gitignored paths right along with everything else, and a
top-level status check doesn't look inside a submodule:

```sh
git -C "<path>" status --porcelain --ignored
git -C "<path>" submodule foreach 'git status --porcelain --ignored'
```

Only once both come back empty:

```sh
git worktree remove --force "<path>"
```

See [`dev_docs/worktree-cleanup.md`](dev_docs/worktree-cleanup.md) if
`remove --force` itself fails — a locked worktree needs different handling
than any other failure does, and reaching for `rm -rf` is a last resort, not
the default.

Everything CI runs is also runnable locally through one entrypoint, so you never
discover a failure only after pushing.

```sh
just check       # the full deterministic gate (what CI runs on every PR)
just check-fast  # edit loop: skips the long suites, lints only what you touched
just fmt         # auto-format everything with dprint + shfmt
just validate    # only the repo-native structural/consistency validator
just eval        # gate + behavioral skill-triggering evals (opt-in, needs auth)
just verify-fix "<description>"  # the standard post-fix verification bundle (below)
```

`just check` is a thin wrapper around `scripts/check.sh` — the script is the
source of truth, and CI calls it directly.

Once a fix is ready to verify, use `just verify-fix "<description>"`
(`scripts/verify-fix.sh`) instead of hand-composing the check sequence —
it bundles the full run, a multi-round stability check, a process/orphan
watch, the confinement smoke (macOS-only, skipped elsewhere), and a final
gate check into one invocation, and always dumps diagnostics on failure.

`just check-fast` is for the edit loop: ~8s against the full gate's ~41s. It
skips the two long test suites and narrows the shell lint to the files your
branch has touched — that is skipped coverage, not sharded or sampled, so
`just check` still has to pass before you push and CI always runs the gate with
no flags. It announces exactly what it dropped, on entry and again at the end.
(The lint narrowing is per-file, so a branch that adds several long shell files
pays their lint cost on every `--fast` run — that is the narrowing working, not
a regression.)
Why those particular things are slow, and what it would take to make `just check`
itself faster, is in [`dev_docs/gate-performance.md`](dev_docs/gate-performance.md)
— read it before adding concurrency anywhere in the gate.

## The gate (`just check`)

Four deterministic, blocking checks, plus the shell lint and Bats suites:

1. **`dprint check`** — formatting (config in `dprint.json`).
2. **`claude plugin validate . --strict`** — official manifest/frontmatter
   validation.
3. **`uv run scripts/validate.py`** — repo-specific rules the above don't cover:
   - skill/command/agent frontmatter shape; skill `name` (if set) matches its
     directory and the `^[a-z0-9-]+$` slug rules; `description` non-empty and
     ≤1024 chars; SKILL.md body ≤500 lines;
   - `plugin.json` and `marketplace.json` versions are present and **equal**;
   - the README "N skills, M commands, K subagent" sentence matches reality.
4. **`scripts/typecheck.sh`** — mypy over the repo's Python, at a version pinned
   in the script (fetched by `uvx`, so it needs network on the first run of a
   given pin). Two tiers, split by annotation coverage rather than by what
   ships: `--strict` on `scripts/research-spike.py`, default settings on the
   other `scripts/` entrypoints and the handler assets. Both tiers always run
   and the exit code is their OR, so one failing never hides the other's
   findings. The script's header carries the full rationale, including why not
   `ty`.

`scripts/validate.py` is dev/CI-only tooling (never shipped to plugin
consumers); its one dependency is hash-locked in `scripts/validate.py.lock`.

## Adding a skill

1. Create `skills/<name>/SKILL.md` with valid frontmatter (`description`
   required; `name`, if set, must equal `<name>`). Keep the body ≤500 lines —
   move detail into sibling reference files.
2. Update the component count in `README.md` ("What's in the box") and add its
   row to the matching workflow table.
3. Add a trigger eval: `evals/prompts/<name>.txt` (a realistic prompt that
   **doesn't name the skill**) plus a row in `evals/manifest.tsv`. Skip this only
   for `user-invocable: false` skills.
4. Run `just check` (must pass) and, if you can, `just eval` to confirm the new
   skill auto-triggers.

## What loads at runtime vs. contributor-only

Skill files split into two tiers with different audiences, and confusing them
silently drops behavior:

- **Runtime-loaded** — `SKILL.md` itself, in full, every time the skill
  triggers (its frontmatter `description` loads even earlier, at session
  start, as the trigger surface). No other file is read on invocation.
- **Contributor-only / loaded on demand** — sibling `references/` files and
  everything under `dev_docs/`. The agent opens a `references/` file only if
  `SKILL.md`'s own text tells it to and it chooses to; `dev_docs/` is
  essentially never pulled into a running skill's context at all.

**Any operational or behavioral rule the agent must act on unconditionally
belongs in `SKILL.md` itself, never only in a `references/` file.** A fix that
spans both and only lands in the reference file is invisible to the agent at
runtime — that gap has already shipped a silent bug once, past a first
co-review pass, before a second pass caught it. The same split applies to
`commands/<name>.md` (loaded when the command runs),
`commands/handlers/<handler>.md` (loaded when a task command dispatches into
it), and `agents/<name>.md` (loaded when the subagent spawns): their bodies are
runtime prompts, not documentation.

## Adding a command

Add `commands/<name>.md` with valid frontmatter, then update the component count
and the matching workflow table in `README.md`. `validate.py` fails the build if
the count drifts.

## Adding a handler

When you add a task handler or teach an existing one a new verb (capture, list,
promote, do, process), update the **handler capability matrix** in
`commands/task-config.md` in the same PR — it's the single source of truth that
`/task-config` reads to warn users about capability gaps, and it drifts silently
if you don't.

## Releasing

Versions bump automatically on merge to `main`, driven by the Conventional
Commit type in the **PR title**. That, the `RELEASE_TOKEN` setup, and the
procedure for landing a stack of PRs are all in
[`dev_docs/releasing.md`](dev_docs/releasing.md).

## Behavioral evals

`just eval` (and the manual **Evals** GitHub workflow) check that Claude
auto-invokes each skill from its naive prompt. They cost API tokens and are
nondeterministic, so they are **opt-in and never block a PR**. See
[`evals/README.md`](evals/README.md).
