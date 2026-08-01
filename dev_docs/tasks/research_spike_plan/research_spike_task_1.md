---
title: "research-spike: script skeleton — CLI, tree discovery, block parser, test harness"
priority: high
size: 5
status: new
created: 2026-08-01
source_branch: claude/spike-research-plan-jz7ggg
related_files:
  - dev_docs/designs/research_spike_skill.md
  - scripts/research-spike.py
  - scripts/test-research-spike.sh
  - scripts/check.sh
  - scripts/task-scan.py
parent: research_spike
expires: 2026-08-31
tags: [research-spike, script, foundation]
---

[[research_spike_plan]]

## Context

First task of the plan — everything else extends what this lands. Read
`dev_docs/designs/research_spike_skill.md` §"Record formats and grammar",
§"On-disk structure" and §"Script/LLM split" before starting.

The instrument is one Python script, **stdlib only** (design §"How it works
mechanically"). No `uv` header, no lock file — invoked as
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/research-spike.py"`, matching
`commands/handlers/assets/*.py`. Note `dprint.json` registers the **ruff**
plugin, so `dprint check` formats and gates this file; run `just fmt` before
pushing.

The directory convention the script walks (owned by the skill, not
configurable — this is what replaces the reference implementation's per-repo
config for coverage files and heading patterns):

```
dev_docs/research/<project>/
  PROJECT.md            # charter
  decisions.md          # organizer-owned
  LEDGER.md             # generated roll-up, organizer-owned
  tracks/<track>/
    questions.md        # questions + answers + the track's stored ledger
    contracts/          # optional; preconditions register here
    obligations/        # stub and receipt cards
```

Ids are declared **bare** and qualified by the script: `project/track/id`, or
`project/id` for decisions. In-record references (`blocks:`, `blocking:`) name
decisions by bare id, resolved within the enclosing project.

`--root <dir>` is what makes the whole thing testable — every test builds a
fixture tree under `mktemp -d` and passes it, so no test ever reads the real
repository. That matters more than usual here: the central assertion is "this
path exists", and a test scanning the real tree would pass or fail on whatever
happens to be checked in.

## Task

**`scripts/research-spike.py`** — new file:

- CLI with `argparse`: subcommands `init`, `validate`, `ledger`,
  `write-ledger`, `status`, `suggest`, each registered but only dispatching to
  a stub in this task except as noted. Global `--root <dir>`, defaulting to
  **the current working directory**, matching `scripts/task-scan.py` and
  `scripts/claim-scan.sh` — and deliberately **not** `__file__`-anchored.

  > **Do not mirror `scripts/validate.py`'s `ROOT` here.** That script's
  > script-relative default exists so the plugin can validate **its own** tree;
  > consumers reach it only when `/doctor` passes an explicit dir. This script
  > ships to consumers and is invoked through `${CLAUDE_PLUGIN_ROOT}`, so
  > `__file__` is the **installed plugin**, and an `__file__`-anchored default
  > would scan the plugin checkout instead of the consumer repo. It would also
  > fail **silently green**: the plugin has no `dev_docs/research/` tree, and
  > the "no research dir is clean" rule below would report success. Worse,
  > `init` (task 2) would scaffold into the plugin install directory.
  > `dev_docs/deterministic-code-opportunity.md` §"Load-bearing decisions &
  > gotchas", item 1, documents this as the exact defect PRE-611 fixed and
  > warns against re-breaking it.
- Exit-code contract, applied by the dispatcher and asserted by tests:
  `0` clean, `1` violations, `2` usage error. **State the classification rule
  explicitly, because three separate PRs write fixtures against it and an
  unstated convention is how they end up inconsistent:** `1` is for
  **tree-content violations** found by a scan — a rule broken by what is in the
  tree. `2` is for **caller errors** — unparseable arguments, an unknown
  subcommand, a malformed or already-existing project named on the command
  line. Argparse's own usage exit is `2` and is **never overridden by a
  subcommand** (see task 8, whose scan is exit-0 by design but which does not
  get to suppress the dispatcher's `2`).
- **Tree discovery**: enumerate `<root>/dev_docs/research/*/` as projects and
  `tracks/*/` within each. A root with no `dev_docs/research/` is **clean, not
  an error** — exit 0 with no output.
- **Block parser** — the grammar, kept at reference-implementation simplicity:
  - Recognize fenced blocks with info strings `question`, `obligation`,
    `decision`, `card` in any `*.md` under the tree.
  - One `key: value` per line. Unknown keys are an **error, not ignored** (a
    `desination:` typo must not silently drop the constraint).
  - **The key/value split is on the _first_ colon only**; everything after it
    is the value, verbatim.
  - **No inline comment syntax** — a `#` is part of the value and will fail the
    relevant enum check. Do not add comment stripping.
  - List values are comma-separated.
  - **`none` is a reserved sentinel value.** When a value begins with `none`,
    the field is a **declaration**: everything after the sentinel's own colon is
    a free-text reason taken verbatim, and **the comma-list rule does not apply
    to it**. Without this exemption, `blocks: none: it gates nothing, and
    probably never will` splits on the comma into two "decision ids", and task
    5 then reports `and probably never will` as a dangling reference.
  - A bare `none: <reason>` block is a valid record shape (used by the coverage
    rule in task 4); parse it here, enforce it there.
- **Record model**: a dataclass per record type carrying its raw fields, source
  file, line number, enclosing project, and enclosing track (`None` for
  project-level files). Field _semantics_ (required/enum/referential) belong to
  tasks 3–5 — this task only parses and locates.
- **Error reporting shape**: collect `path:line: message` strings and print
  them sorted; separate `errors` (exit 1) from `warnings` (reported, exit
  unaffected), mirroring `scripts/validate.py`.
- Section discovery for `questions.md`: a question section starts at a
  `### Q<n>.` heading and ends at the next same-level heading. Expose it as a
  helper; task 4 consumes it.

**`scripts/test-research-spike.sh`** — new fixture harness, modelled on
`scripts/test-validate.sh`: `mktemp -d` per fixture, `ok`/`bad` counters,
`assert_contains` / `assert_not_contains`, `trap 'rm -rf' EXIT`. Copy that
file's git-env neutralization block verbatim — the same inherited-env hazards
apply. Cover in this task:

- an empty root (no `dev_docs/research/`) exits 0 silently;
- **invoked with no `--root` from inside a fixture tree, the scan finds that
  tree's projects** — the regression guard for the plugin-vs-consumer default;
- **invoked with no `--root` from a directory with no `dev_docs/research/`, it
  exits 0 without touching the plugin tree**;
- a two-project tree discovers both projects and their tracks;
- an unknown key in a block is an error naming the key;
- an inline `#` comment is **not** stripped (assert the value retains it);
- a comma-separated list value parses to multiple entries;
- a malformed fence (unterminated block) is an error, not a crash;
- an unknown subcommand exits `2`.

**`scripts/check.sh`** — add `run scripts/test-research-spike.sh` alongside the
other `test-*.sh` lines.

## Acceptance Criteria

**Code-enforced:**

- `bash scripts/test-research-spike.sh` passes and is invoked by
  `scripts/check.sh`.
- `bash scripts/check.sh` green, including `dprint check` over the new Python
  file (ruff plugin) and the new shell script's `lint-shell.sh` pass.
- `python3 scripts/research-spike.py --root "$(mktemp -d)" validate` exits `0`
  with no output; `python3 scripts/research-spike.py bogus` exits `2`.
- The script imports nothing outside the standard library (grep the import
  block in review).

**User-run:**

- `python3 scripts/research-spike.py --help` lists all six subcommands.
