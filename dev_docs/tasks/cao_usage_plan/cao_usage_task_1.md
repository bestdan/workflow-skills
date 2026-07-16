---
title: CAO coder backend for orchestrate-coders (cao-run custom command)
priority: high
size: 3
status: new
created: 2026-07-15
source_branch: worktree-bestdan+cao-usage-adapter
related_files:
  - skills/orchestrate-coders/SKILL.md
  - skills/orchestrate-coders/backends/cli-coders.md
  - skills/select-coder/SKILL.md
is_blocked_by:
parent: cao_usage
tags: [orchestrate-coders, cao, coder-backend]
---

Part of [[cao_usage_plan]].

## Context

Depth 1 (resolved) reaches CAO **through the existing skills**: `/deliver-task` → `orchestrate-coders` → a coder backend. `orchestrate-coders` already supports a custom `command:` coder from `dev_docs/orchestrate-coders/.coders.yml` with `{SPEC}` and `{WORKTREE}` placeholders (`skills/orchestrate-coders/SKILL.md` "Config", `backends/cli-coders.md` "Custom `command:`"). So the only new artifact is a wrapper that turns that contract into a CAO worker launch — no changes to `/deliver-task` or the auto-pilot loop.

`cao-run <base-profile> <model> <worktree> <task|@file>` (already built, `~/.local/bin/cao-run`) clones a CAO profile, stamps the model, launches the worker headless in the given worktree, prints the diff, and cleans up. `orchestrate-coders` creates and owns `{WORKTREE}`; the wrapper must run the worker **in that worktree** and must not create its own.

## Task

- Add a thin wrapper (e.g. `~/.local/bin/cao-coder` or a repo `scripts/` entry) invoked as `cao-coder {SPEC} {WORKTREE} <backend:model>`:
  - Map the `orchestrate-coders` coder spec to a CAO base profile: `codex`→`dev-codex`, `agy`→`dev-antigravity`. Refuse `opus`/`devin` with a clear "not in the CAO fleet" error (so `orchestrate-coders` re-dispatches per its Safety rules).
  - Call `cao-run <profile> <model> {WORKTREE} @{SPEC}` against the **provided** worktree. **Resolve the worktree contract first, don't assume it:** verify against the current `~/.local/bin/cao-run` that it creates a worktree only when the target dir is absent and uses an existing dir as-is. Then either (a) cite that confirming code path here, or (b) if `cao-run` would ever run `git worktree add` on an existing dir, treat fixing that as a required **change to `cao-run` itself** (an existing-dir-only mode) — not something the wrapper can mask. The wrapper must provably never trigger `git worktree add`.
  - Surface the harvested diff/exit exactly as the custom-command contract expects; classify home-dir cache/permission errors as environmental (per `cli-coders.md` "Environmental failures").
- **Per-task backend/model comes from *named* entries, not a placeholder.** `orchestrate-coders`' custom `command:` substitutes only `{SPEC}`/`{WORKTREE}` — there is **no** model placeholder, so the `<backend:model>` is baked into each entry's command string. Register **one named coder per (backend, model) in use**, e.g. `coders: [{name: cao-codex, command: "cao-coder {SPEC} {WORKTREE} codex:gpt-5.6-terra"}, {name: cao-agy, command: "cao-coder {SPEC} {WORKTREE} agy:Gemini 3.5 Flash (High)"}]`. Per-task selection is choosing the matching named coder (task 2 maps `select-coder`'s `codex:`/`agy:` output → `cao-codex`/`cao-agy`). A single static entry pins one backend/model for the whole run — call that out so nobody expects per-task model variation from one entry. Each is an **untrusted custom command** (confirm-before-first-run / `--allow-command` under `--non-interactive`, per `orchestrate-coders` Safety).
- **Define the runtime prerequisite** this coder introduces: it needs the **`cao-server`** daemon (the local CAO session host started via `cao-server`, default `localhost:9889`) **running**, plus `cao`/`cao-run` on `PATH`. State the dependency here; the launch-time gate that blocks a run when it's absent lives in [[cao_usage_task_2]]'s pre-flight and is re-verified on resume by `auto-pilot`'s capability-join re-check (`skills/auto-pilot/references/resume.md`).
- Update `select-coder`'s mapping note so a `codex`/`agy` recommendation can be expressed as the matching named `cao-*` coder when the run is CAO-routed (a one-paragraph pointer, not new scoring logic).

## Acceptance Criteria

**Code-enforced**
- `cao-coder {SPEC} {WORKTREE} codex:gpt-5.6-terra` run against a pre-made worktree + spec file produces a diff in that worktree and exits 0; an `opus:`/`devin:` spec exits non-zero with the fleet error.
- The wrapper never runs `git worktree add` (it uses the caller's `{WORKTREE}`), verified by running it inside an existing worktree with no new worktree created; the `cao-run` existing-dir behavior it relies on is cited or fixed in `cao-run`.
- The `.coders.yml` template shows **named per-(backend:model) entries** (`cao-codex`, `cao-agy`), documents that one entry pins one backend/model, and carries the untrusted-command warning.
- The `cao-server`/`cao` runtime prerequisite is stated (definition + that it must be running).

**User-run**
- `/orchestrate-coders "<small task>" --coder cao-codex` (with the `.coders.yml` entry) implements the change via a CAO worker and integrates it — Claude never writes the feature code.
