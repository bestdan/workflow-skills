# Auto-pilot: how to use it

Auto-pilot is a slash command you launch **inside an existing Claude Code
session**. It is not a separate `claude-auto-pilot` executable: a repository
search for that name has no entrypoint. Start a normal `claude` session, then
run:

```text
/auto-pilot <linear-project | plan-dir> [--until ...] [--reserve ...] [--profile less-claude] [--resume]
```

The interactive launch phase does the safety work while you are present. On a
go decision it writes run state and starts a detached `claude -p` orchestrator.
On macOS the detached supervisor uses `launchd`; it is not an in-session Agent.
See [the launch-runtime reference](../skills/auto-pilot/references/launch-runtime.md).

## Choose the source

Auto-pilot accepts one work source at a time.

### A Linear project

Pass a resolvable Linear project id, name, or slug:

```text
/auto-pilot Platform Reliability
```

The Linear adapter turns ready issues and dependency edges into the run state;
the run's effective handler is `linear`.

### A plan-with-docs directory

Pass the actual committed plan directory:

```text
/auto-pilot dev_docs/tasks/checkout_cleanup_plan/
```

The directory must exist and its plan files must already be committed, because
the detached process only sees what reaches its worktree. A plan source is
routed through the `repo-pr` handler even if the repository’s default handler
is Linear.

Only Linear projects and plan directories are supported today.

## What happens before you walk away

Launch is interactive and fail-closed. It does not start an overnight process
when a prerequisite is merely uncertain.

It creates the isolated run worktree and `auto-pilot/<run_id>` state branch;
checks the source is reachable; runs the read-only pre-flight; proves
credentials and selected coders can work non-interactively; checks base
freshness; resolves reviewer, coder, and verification choices; materializes the
task graph; and smoke-tests the same sandboxed environment the detached process
will use. It also asks you to make the human judgement that the machine will
remain awake.

`scripts/preflight.sh` reports parseable facts followed by one verdict:

```text
PREFLIGHT VERDICT: go
```

or:

```text
PREFLIGHT VERDICT: no-go — <reason>
```

A `no-go` means **do not launch tonight**. Fix the named prerequisite and run
the command again; it is not a warning the detached runner should work around.
The helper covers GitHub authentication, coder availability/authentication,
freshness, resolved executable paths, the task destination host, and a
confinement smoke test. The launch phase adds source, worktree, configuration,
and task-route checks around it. [[auto-pilot]] and
[the pre-flight helper](../scripts/preflight.sh) are the operational authority.

## Flags

### `--until <time>`

Use an absolute ISO-8601 timestamp or a relative `now+<duration>` value:

```text
/auto-pilot Platform Reliability --until now+8h
/auto-pilot dev_docs/tasks/checkout_cleanup_plan/ --until 2026-07-18T06:30:00-04:00
```

This is a **fail-soft** deadline. Before claiming the next task, the runner
checks whether the remaining time can cover that task’s computed minimum budget.
If not, it stops cleanly and leaves remaining work for an explicit resume; it
does not deliberately cut off a delivery mid-task. Omitting `--until` means no
wall-clock deadline. See [run budget](../skills/auto-pilot/references/run-budget.md).

### `--reserve <pct>`

Set the minimum Claude session-window headroom before Claude-heavy delivery
steps:

```text
/auto-pilot Platform Reliability --reserve 20
```

The accepted range is `0` through `100`; the default fixed floor is `15`. The
resolved value is written to `RUN.md` and reused on resume unless you provide a
new valid override. The runner may raise its effective reserve from observed
per-task usage, but it never lowers this configured floor.

### `--profile less-claude`

Use this when CAO is set up and you want the mechanical implementation leg to
consume fewer Claude tokens:

```text
/auto-pilot dev_docs/tasks/checkout_cleanup_plan/ --until now+8h --profile less-claude
```

The profile routes implementation through CAO’s named `cao-codex` or `cao-agy`
custom coder, constraining selection to the CAO-dispatchable Codex/Antigravity
fleet. It turns co-review off by default (or can use the `cheap-single` dial
chosen at launch) and sends integrated-diff judgment to Sonnet. It does **not**
skip the pinned shell verification command.

CAO must already be running: `cao`, `cao-run`, and `cao-server` must be on
`PATH`, the daemon must answer on `localhost:9889`, and the daemon must have
been started with `CAO_ENABLE_WORKING_DIRECTORY=true`. Auto-pilot does not start
or restart that service. See [CAO’s custom-coder contract](../skills/orchestrate-coders/SKILL.md).

### `--resume`

Resume a paused or crashed run with the same normalized source:

```text
/auto-pilot Platform Reliability --resume
```

Resume finds exactly one resumable run-state branch, refuses to race a live
orchestrator, rechecks prerequisites that can rot, reconciles incomplete tasks
against git and the tracker, then starts the detached loop again. The stored
profile is authoritative, so a previous `less-claude` run does not need the
profile flag again. See [resume](../skills/auto-pilot/references/resume.md).

## Two overnight paths

### Walk away overnight

1. In a normal Claude Code session, run `/auto-pilot` with a Linear project or
   committed plan directory.
2. Use `--until now+…` for an overnight wall-clock window; add
   `--profile less-claude` only after CAO is ready.
3. Resolve any `no-go` while you are present. A successful launch tells you the
   run branch, `.auto-pilot/` state files, and log location.
4. Leave the detached orchestrator to work. In the morning, read committed
   `REPORT.md` and `QUESTIONS.md`, then review PRs at `needs_review`.

Auto-pilot never merges PRs or completes tracker work unattended.

### Partially attended

Launch a bounded run, stay available for the first task or two, and inspect the
same run-state branch as it progresses. Answer items in `QUESTIONS.md`, inspect
the rolling `REPORT.md`, and use `--resume` after fixing a pause, crash, or
deadline stop. Your role is to resolve decisions and review hand-offs, not to
keep a foreground terminal alive.

## `car` is a different tool

`scripts/claude-auto-resume.sh` (usually aliased as `car`) keeps an
**interactive** Claude Code session alive across the five-hour usage wall. It
runs `claude`, detects a real usage-cap exit, waits for reset, and restarts the
same conversation with `claude --continue`—usually inside tmux.

```sh
alias car='<plugin-dir>/scripts/claude-auto-resume.sh'
car
```

Use `car` when an interactive session must survive overnight. It is not the
auto-pilot launcher and does not supervise auto-pilot’s detached `claude -p`
process. Auto-pilot has its own run-state-backed supervisor, pause/relaunch
path, heartbeat, and alarm machinery. Starting an interactive session with
`car` and then running `/auto-pilot` is fine, but the recovery loops remain
separate. See [the `car` script](../scripts/claude-auto-resume.sh) and
[README usage](../README.md#overnight-auto-resume-car).
