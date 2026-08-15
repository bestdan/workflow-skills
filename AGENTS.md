# AGENTS.md

Guidance for anyone — human or agent — working **on** this repository. It is
the map and the short list of rules that are load-bearing here; everything
deeper is one link away.

| You want to…                                                   | Go to                                                          |
| -------------------------------------------------------------- | -------------------------------------------------------------- |
| Install and use the plugin                                     | [`README.md`](README.md)                                       |
| Set up the dev loop, run the gate, add a skill/command/handler | [`CONTRIBUTING.md`](CONTRIBUTING.md)                           |
| Cut a release, fix a failed one, land a stack of PRs           | [`dev_docs/releasing.md`](dev_docs/releasing.md)               |
| Interpret a skipped test, or add a host-dependent one          | [`dev_docs/testing.md`](dev_docs/testing.md)                   |
| Speed the gate up, or add concurrency to it                    | [`dev_docs/gate-performance.md`](dev_docs/gate-performance.md) |
| Call codex/agy/devin from a workflow                           | [`dev_docs/external-agents.md`](dev_docs/external-agents.md)   |
| Understand secret/API-key resolution                           | [`dev_docs/auth_key_access.md`](dev_docs/auth_key_access.md)   |
| Read a design decision                                         | `dev_docs/designs/`, `dev_docs/decisions/`                     |

## What this repo is

A **Claude Code plugin**. The product is prompt text, not a program:

- **`skills/<name>/SKILL.md`** — auto-triggering skills. Frontmatter
  `description` is what decides whether the skill fires, so it is interface,
  not documentation. Body ≤500 lines (enforced); overflow goes in sibling
  `references/` files the skill links to.
- **`commands/<name>.md`** — slash commands. `commands/handlers/` holds the
  per-tracker (`linear`, `gh-issue`, `jira`, `repo-pr`) implementations the task
  commands dispatch into.
- **`agents/<name>.md`** — bundled subagents.
- **`scripts/`** — dev/CI tooling _and_ runtime helpers the skills shell out to
  (`research-spike.py`, `task-scan.py`, `spawn-orchestrator.sh`, …). Not all of
  it is dev-only; check who calls a script before treating it as disposable.
- **`evals/`** — behavioral checks that each skill auto-triggers from a naive
  prompt. Opt-in, never blocking.
- **`dev_docs/`** — durable design docs and runbooks.

The practical consequence: **editing a skill or command body changes agent
behavior at runtime for every installed user.** Treat those files with the care
you'd give production code, and treat their length as a cost — every line is
tokens in someone's context window.

## Progressive disclosure is the house style

It applies to the docs _and_ to the skills, for the same reason: an agent pays
for what it loads.

- A top-level file states the rule and links to the detail. It does not inline
  the detail.
- A SKILL.md body carries the judgment; procedures, tables, and worked examples
  go in `references/` and are loaded only when needed.
- One fact, one home. If something is already true in `commands/task-config.md`,
  `dev_docs/releasing.md`, or a skill's own reference file, **link to it** — a
  second copy rots silently and there is no invalidation.

Before adding a section to `README.md`, `CONTRIBUTING.md`, or this file, ask
whether it belongs one level down instead. The answer is usually yes.

## Rules that bite here

- **Run `just check` before pushing.** It is exactly what CI runs
  (`scripts/check.sh`), it runs everything concurrently, and it reports every
  failure rather than stopping at the first. Don't discover formatting drift in
  CI — `just fmt` fixes it.
- **Never commit to `main`.** It's protected, and every qualifying merge
  auto-bumps the version. Branch (`bestdan/...` for this repo's owner),
  PR, squash-merge.
- **The PR title is the release lever.** Merges are squashed, so the title's
  [Conventional Commit](https://www.conventionalcommits.org/) type
  (`feat` / `fix` / `chore` / …) is what decides whether a version ships and how
  big the bump is. Getting it wrong ships a wrong release, not a wrong label.
- **Adding a skill or command means editing `README.md` in the same PR.**
  `validate.py` fails the build if the "N skills, M commands, and K subagent"
  sentence drifts from reality.
- **Adding or extending a handler means editing the capability matrix in
  `commands/task-config.md` in the same PR.** That table is the single source of
  truth `/task-config` reads to warn users about gaps; nothing detects its drift.
- **`plugin.json` and `marketplace.json` versions must stay equal**, as plain
  `X.Y.Z` — `validate.py` rejects a `v` prefix. The Release workflow normally
  owns both; if you bump by hand, bump both.
- **`dev_docs/tasks/` is ignored except `.task-config.yml`.** Plan scaffolding
  there is ephemeral in-flight state — durable wisdom graduates to a top-level
  `dev_docs/<name>.md`. See the comment block in `.gitignore` before trying to
  force-track anything under it.
- **Don't commit state another system owns.** Linear, GitHub, and the changelog
  already know their own facts; write a link, not a copy. A file that records a
  moment carries its date in the name.

## Working style

- Read a file before modifying it. Read `origin/main`'s `.gitignore` and a
  file's history before untracking or deleting it — several things here are
  tracked on purpose.
- Don't over-engineer: no error handling for impossible cases, no feature flags,
  no backwards-compat shims. Three similar lines beat a premature abstraction.
- Don't refactor surrounding code, add comments/docstrings/type annotations to
  code you didn't change, or add unasked-for functionality.
- Don't disable or delete tests to make the gate pass.
- Don't add dependencies without discussing it first. `scripts/validate.py`'s
  single dependency is hash-locked in `scripts/validate.py.lock`.
- **Resolve uncertainty by running something.** A question about behavior, an
  API, or an assumption is usually cheaper to settle with a one-liner or a
  targeted test than with another round of speculation.
- **Verify before asserting a diagnosis — especially an absence.** "There's no
  X", "it doesn't support Y", "that branch is protected" are claims about the
  state of the world; run the one cheap check that would falsify it first.
  Inference can't tell "not there" from "not where I looked".
- **Verify before claiming success.** Run the gate, name the smallest command
  that shows the change working, and report what it actually printed.
- **Determine merge status from PR state, not commit ancestry.** This repo
  squash-merges, so `git merge-base --is-ancestor` reports every landed branch
  as unmerged. Use `gh pr list --head <branch> --state all --json state`.

## Shell and script conventions

- Shell is formatted with `shfmt -i 2 -ci -bn` and linted with ShellCheck via
  `scripts/lint-shell.sh`; both run in `just check`.
- macOS ships Bash 3.2 and `scripts/test-shell.sh` deliberately exercises that
  floor — no associative arrays, no `declare -A`. Prefer indexed arrays and
  plain loops. CI's macOS job runs that suite under `/bin/bash` 3.2, so a
  violation fails the PR rather than only a maintainer's laptop — see
  [`dev_docs/testing.md`](dev_docs/testing.md) for why that holds incidentally
  rather than by contract.
- Test harnesses that build git fixtures must pin `GIT_CONFIG_GLOBAL=/dev/null`.
  A `git init` fixture otherwise inherits the developer's global config — a
  machine-wide `core.hooksPath` with a `pre-commit` that refuses commits to
  `main` is exactly what a fresh fixture does, and the suite then passes in CI
  while failing on a laptop.
- Never redirect a failable command into a tracked file. `cmd > real_file`
  truncates the destination even when `cmd` fails; write to a temp path and
  `mv` on success.
