---
name: orchestrate-coders
description: Use when the user wants the current session to act as an orchestrator that farms coding work out to other coder agents — e.g. "delegate this to codex", "have opus implement these", "orchestrate/supervise coders", or /orchestrate-coders. The orchestrator decomposes the task into packets, dispatches each to a configured coder backend (opus subagent, codex, agy, devin, or a custom CLI), verifies the results, and integrates them. The coder backend and its model are selectable per run.
---

# orchestrate-coders — supervise a fleet of coder agents

The current session is the **orchestrator**: it plans, decomposes, dispatches,
verifies, and integrates — it does **not** write the feature code itself. Each
work packet is executed by a **coder backend**. The backend is generic: any
agentic coder that can take a task spec and produce a diff qualifies. One
backend (`opus`, a native Claude subagent) is special-cased because it needs no
external CLI; everything else is driven the same way through a shell contract.

Important distinction for Claude Code sessions: the `Agent` tool only reaches
registered Claude subagents. Do not treat that as the availability boundary for
this workflow. `codex`, `agy`, `devin`, and custom coders are valid delegates
when their CLIs are installed; dispatch them with `Bash` using the CLI contract
instead of trying to spawn them through `Agent`.

## Coder spec

A coder is named as `<backend>[:<model>]`:

- `opus` — native Claude subagent (default model `claude-opus-4-8`). The
  special case: dispatched with the Agent tool, no external process. Mechanics:
  [`backends/opus.md`](backends/opus.md).
- `codex`, `agy`, `devin` — known external coder CLIs, driven through the
  shared shell contract. Mechanics: [`backends/cli-coders.md`](backends/cli-coders.md).
- any other name — must come from config with an explicit `command:`
  (untrusted; see Safety).

The optional `:<model>` suffix pins the model **that backend** runs, passed
through in whatever form the backend understands (an Anthropic model ID for
`opus`, a `--model` value for a CLI coder). Examples: `opus:claude-sonnet-5`,
`agy:Gemini 3.1 Pro (High)`. A model suffix containing spaces must be quoted
on invocation — `--coder "agy:Gemini 3.1 Pro (High)"` — or the tail bleeds
into the task description.

## Invocation

```
/orchestrate-coders <task description> [--coder <spec>]... [-n N] [--plan <name>] [--non-interactive]
```

- `--coder <spec>` — which coder(s) to use. Repeatable: with several, assign
  packets round-robin unless a packet's nature clearly favors one (note the
  assignment in the report). A packet's "nature" here is the task profile
  `assess-task` produces (`skills/assess-task/SKILL.md`); the `select-coder`
  skill scores that profile against its capability matrix and produces exactly
  these per-packet overrides — use it when the assignment isn't obvious. Absent
  → config default → resolution flow below.
- `-n N` — max packets in flight at once (default 3).
- `--plan <name>` — skip decomposition and execute an existing
  `dev_docs/tasks/<name>_plan/` plan (from `/plan-with-docs`), one packet per
  dependency-ready task file.
- `--non-interactive` — never prompt on coder choice; see "Resolution when no
  `--coder` is passed" below.
- The task description is whatever remains; with `--plan` it may be empty.

## Config

`dev_docs/orchestrate-coders/.coders.yml` — **local** config; on first write,
keep it out of git the same way co-review does:
`git check-ignore -q dev_docs/orchestrate-coders/ || echo 'dev_docs/orchestrate-coders/' >> "$(git rev-parse --git-dir)/info/exclude"`.

```yaml
default_coder: codex # used when no --coder flag is passed
coders:
  - codex # known backend → built-in invocation
  - opus # native subagent
  - name: my-coder # custom backend → explicit invocation
    command: "my-coder run --task-file {SPEC} --workdir {WORKTREE}"
sandbox_workarounds: # project-specific; injected into each packet spec's constraints
  - "dprint: run `dprint fmt --incremental=false` (the incremental cache dir is not writable in-sandbox)"
```

This file is the **resolved-config shape**, defined once in
`skills/select-coder/SKILL.md` ("Resolved config (non-interactive callers)")
and shared here rather than redefined.

### CAO custom-coder template

To dispatch a packet through the local CAO fleet, add one named custom command
per pinned backend/model pair. The wrapper ships with this repository at
`scripts/cao-coder.sh`:

```yaml
coders:
  - name: cao-codex
    command: "scripts/cao-coder.sh {SPEC} {WORKTREE} codex:gpt-5.6-terra"
  - name: cao-agy
    command: "scripts/cao-coder.sh {SPEC} {WORKTREE} 'agy:Gemini 3.5 Flash (High)'"
```

`command:` substitutes only `{SPEC}` and `{WORKTREE}`; it has no model
placeholder. One named entry therefore pins one backend/model for the whole
run. Per-packet model selection means choosing the matching `cao-*` entry,
not expecting a single static entry to vary its model. Quote a model containing
spaces as shown.

These are **untrusted custom commands**: show the exact command and confirm
before its first run. Under `--non-interactive`, pass the exact command through
the caller's `--allow-command` approval mechanism; otherwise it must be
skipped.

Runtime prerequisite: `cao-server`, the local CAO session host (normally on
`localhost:9889`, started with `cao-server`), must already be running **with
`CAO_ENABLE_WORKING_DIRECTORY=true` exported in the daemon's environment**, and
both `cao` and `cao-run` must be on `PATH`. The env var is load-bearing: without
it the worker ignores the caller-owned worktree and edits elsewhere, so
`cao-run`'s diff harvest — and therefore this wrapper — comes back empty even on
a successful task (confirmed live). The launch gate belongs to the CAO run
profile pre-flight and is re-checked when auto-pilot resumes; this wrapper does
not start the daemon.

`scripts/cao-coder.sh` accepts only an already-existing caller-owned worktree, so
it cannot invoke CAO's worktree-creation path. Its `cao-run` dependency has
the same existing-directory contract: `~/.local/bin/cao-run:46-50` calls
`git worktree add` only inside `if [ ! -d "$wt" ]`, then uses the existing
directory at line 51. `cao-run` prints the harvested diff and the wrapper
forwards its exit status unchanged. Home-directory cache/permission failures
reported by a worker remain environmental failures under the CLI-coder policy,
not content failures.

**Resolution when no `--coder` is passed:**

- File absent → probe `PATH` (`command -v`) for `codex`, `agy`, `devin`; `opus`
  is always available. Ask the user which to use and what the default should
  be, then write the config so it isn't asked again.
- File present **with `default_coder`** → use it. A file lacking
  `default_coder` (e.g. holding only select-coder's `availability:` cache) →
  treat as absent for this flow: probe/ask as above, then merge the new keys
  into the existing file. A `--coder` flag always overrides.
- A named coder that isn't in `coders:` and isn't a known backend → stop and
  ask rather than guess.
- **Non-interactive callers** — `--non-interactive`, or a caller that already
  supplies a resolved config — never hit the two "ask" branches above. Missing
  `default_coder` falls back to the first available known backend in
  probe-order `codex`, `agy`, `devin`, `opus`; a named coder that isn't in
  `coders:` and isn't a known backend is skipped, not asked about. Custom
  `command:` backends are only ever used if they already appear in the
  supplied `coders:` list — this flow never invents one. Every automatic
  choice made this way is logged in the report (step 7), not silently taken.
  `/auto-pilot`'s launch phase is the caller expected to supply this resolved
  config for unattended runs.

## Steps

1. **Parse invocation** — coder spec(s), `-n`, `--plan`, task description.

2. **Resolve coders** per the config flow above. For each resolved CLI backend,
   verify it's actually runnable (`command -v`); a missing coder is reported
   and skipped, and if **no** coder remains, fall back to `opus` and say so.
   "Not registered as an Agent-tool subagent" is not a reason to skip a CLI
   backend. For network-bound CLIs, request the required Bash sandbox escape at
   dispatch time; a sandbox/network denial is an environmental dispatch issue,
   not evidence that the coder is unavailable.

3. **Decompose into packets.** Each packet must be independently implementable
   and verifiable — roughly PR-sized or smaller. Write each packet spec as a
   self-contained brief: goal, files in scope, constraints (relevant
   CLAUDE.md/AGENTS.md rules the coder won't otherwise see), and the exact
   verification command (the project's own gate — e.g. `just check`, `npm test`,
   `pytest`).
   With `--plan`, the plan's task files are the packets — don't re-slice them.
   If the task doesn't decompose (one coherent change), it's a single packet;
   that's fine — orchestration still buys verification and isolation.
   If decomposition itself is the hard part and spans >3 steps, offer
   `/plan-with-docs` first instead of improvising a plan inline.

4. **Dispatch packets** — up to `N` in flight, independent packets in
   parallel, dependent ones in dependency order. Every coder that edits files
   works in **its own git worktree** on **its own branch** (branch names
   prefixed `orchestrate/`), never in the user's working tree — two coders in one
   tree, or a coder in the tree the orchestrator is watching, is how work gets
   clobbered. Per-backend dispatch mechanics are in the backend reference
   files; the orchestrator only ever sees the packet's resulting diff. Expect a
   mixed notification model in one wave: CLI coders return via background
   `Bash`, opus via the Agent tool — two different latency/notification
   signals, not a bug.

   The packet spec is the delegated coder's initial prompt. It must explicitly
   say that the recipient is a delegated implementation agent, name its
   workspace root, require edits only under that root, and ask for a short final
   report with files changed and verification status. CLI coders receive that
   prompt through `Bash` (usually stdin or `--prompt-file`), while `opus`
   receives the same content as the Agent prompt. The orchestrator remains the
   only actor that decides whether the diff is acceptable.

5. **Verify each packet as it lands.** First, **check containment**: run
   `git status --porcelain` in the orchestrator's own main checkout (no `-uno`
   — an escaped coder can create untracked files there too, and coder output
   is defined as diff **plus** untracked files) — a CLI coder (notably agy)
   can land correct edits there instead of its worktree. If the main checkout
   is dirty on the packet's files, recover **scoped to the packet's files
   only**: extract their diff including staged edits
   (`git diff HEAD -- <packet files> > patch`), apply it to the worktree
   (`git apply --3way`), move any packet-scoped untracked files into the
   worktree, then reset just those paths in the checkout
   (`git restore --staged --worktree --source=HEAD -- <packet files>`). Never
   diff or restore paths outside the packet's scope — they may be the user's
   own uncommitted work. **Do this
   before judging an empty worktree diff** as failure. Then read the diff yourself (the orchestrator is the reviewer of
   record — never merge a coder's diff unread) and run the packet's
   verification command in that worktree. Classify failures:
   - **Content failure** (edits wrong on a clean re-run) → back to the **same**
     coder **once** with the failure output appended to the spec; a second
     failure parks the packet as `failed`.
   - **Environmental failure** (the coder's sandbox, e.g. codex's home-dir
     cache/permission errors) → the orchestrator's re-run outside the sandbox
     is authoritative; do **not** count it as a content failure. Empty
     diff / error / timeout → re-dispatch to a **different** coder once (Safety).

   Don't loop past one retry in either class.

6. **Integrate.** Merge verified packet branches together (or sequence them
   into one branch), resolve conflicts yourself — conflict resolution is
   orchestrator work, not a new packet — and run the full check suite once on
   the integrated result. Clean up worktrees. Do not commit to `main` and do
   not open a PR unless asked (then ask draft vs ready).

7. **Report.** Per packet: coder used (backend + model), one-line outcome,
   verification result. Then the integrated state: branch name, overall check
   result, and any `failed` packets with the failure output verbatim.

## Safety

- **Custom `command:` entries are untrusted** — the config lives in the repo
  under work. Never run one silently: print the coder name and the exact
  command and get explicit confirmation before the first run.
- **CLI coders get real autonomy flags only inside their own worktree.** A
  full-auto coder in the user's checkout can destroy uncommitted work; the
  worktree boundary is the containment, not the coder's own sandbox.
- **Network-bound coders (`agy`, `devin`) run unsandboxed** in the Bash tool —
  a network failure there is the sandbox, not the coder; don't retry inside it.
- A coder that errors, times out, or produces an empty diff is reported and its
  packet re-dispatched to a different coder at most once — never fatal to the
  run, never silently dropped.
