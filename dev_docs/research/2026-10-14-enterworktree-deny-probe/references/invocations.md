# Probe invocations and checks

The exact commands behind the record, in the order they ran, with the output
each check printed at the time. `run.sh` and `setup.sh` ran from
`/tmp/claude/autopilot-2026-10-14/probe-1001/`. The committed `run.sh` differs
from the one that ran in one respect: `cd "$here/probe1001"` gained `|| exit 1`
afterwards, to satisfy the repo's ShellCheck gate. Phases A and B ran an
earlier `run.sh` that took no extra arguments; C is the only phase that passed
one.

## Setup

```bash
bash setup.sh
```

```
bb0ea1a init
?? .claude/
```

## Phase A — create the worktree

```bash
bash run.sh phase-a-create "Call the EnterWorktree tool exactly once with name: \"probe\" and no other arguments. Then call ExitWorktree with action \"keep\". Report each tool's result verbatim. Do nothing else."
```

Check:

```bash
git -C probe1001 worktree list
```

```
/tmp/claude/autopilot-2026-10-14/probe-1001/probe1001  bb0ea1a [main]
/home/dan/src/worktrees/probe1001/probe                bb0ea1a [bestdan/probe]
```

## Phase B — the probe

```bash
bash run.sh phase-b-probe "This is a harness probe; follow these steps exactly and do nothing else. Step 1: call EnterWorktree with path: \"/home/dan/src/worktrees/probe1001/probe\" and no other arguments. Step 2: if that call was refused, quote the full refusal text verbatim, then follow the route it gives exactly: call ExitWorktree with action \"keep\", then call EnterWorktree with name: \"probe\". Step 3: report every tool result verbatim. Do not use any other tool, and do not retry any call."
```

Check:

```bash
git -C probe1001 worktree list
ls -a probe1001/.claude
```

```
/tmp/claude/autopilot-2026-10-14/probe-1001/probe1001  bb0ea1a [main]
/home/dan/src/worktrees/probe1001/probe                bb0ea1a [bestdan/probe]
settings.json
```

## Marker, before B2

```bash
echo marker-before-b2 > /home/dan/src/worktrees/probe1001/probe/MARKER.txt
git -C /home/dan/src/worktrees/probe1001/probe -c core.hooksPath=/dev/null \
  -c user.name=probe -c user.email=probe@example.invalid \
  commit -q --allow-empty -m "probe: tip marker"
git -C /home/dan/src/worktrees/probe1001/probe rev-parse HEAD
```

```
44e4f5d46aa46bd4f2487aeba2a992c3dcd6d0b5
```

## Phase B2 — the probe again, and phase C — the control

These two ran in parallel. B2's prompt is B's, unchanged.

```bash
bash run.sh phase-b2-probe-rerun "<the phase B prompt above>"
bash run.sh phase-c-control-no-hook "This is a harness probe. Call EnterWorktree exactly once with path: \"/home/dan/src/worktrees/probe1001/probe\" and no other arguments, then report its result verbatim. Do not use any other tool except loading the tool schema, and do not retry." --setting-sources user
```

Check, after both:

```bash
git -C probe1001 worktree list
git -C /home/dan/src/worktrees/probe1001/probe log --oneline -2
ls -a /home/dan/src/worktrees/probe1001/probe probe1001/.claude
wc -l stub-hook-calls.jsonl
```

```
/tmp/claude/autopilot-2026-10-14/probe-1001/probe1001  bb0ea1a [main]
/home/dan/src/worktrees/probe1001/probe                44e4f5d [bestdan/probe]
44e4f5d probe: tip marker
bb0ea1a init
/home/dan/src/worktrees/probe1001/probe:
.git
MARKER.txt
README.md

probe1001/.claude:
settings.json
5
```

`probe1001/.claude/` holds only `settings.json`, so no `.claude/worktrees/` was
created. The stub's five log lines are A's `name` call, B's `path` and `name`
calls, and B2's `path` and `name` calls; none is from C.
