---
created: 2026-09-09
purpose: conventions for this directory
---

# Handoffs

Notes from one agent session to the next, for when a fresh session is what you
wanted.

A handoff is not a crash report. It exists because there was a natural place to
start over: the previous session's context was full, or a task finished and its
PR merged so the next piece should start clean off `main`, or any other reason
to `/clear` and begin again. Clearing wipes the session's memory of everything
that was not written down. A handoff is what survives that on purpose.

## What belongs here

Start with the aim: what the work is trying to achieve and why, not just where
it stopped. A session that knows the goal can re-derive a plan; one that only
knows the last step cannot tell a detour from the route.

Then whatever the next session cannot reconstruct from the repository: what was
already tried and rejected, which measurement is load-bearing, which constraint
is not obvious from the code, and the next concrete step. Anything that _is_
reconstructable (commit history, a PR, a tracker issue) goes in as a link rather
than a copy, because a copy of live state rots and nothing invalidates it.

## Local, transient workstreams

Handoffs are how a workstream runs without a ticket. Plenty of work is small
enough that a tracker issue would cost more than it returns, including a local
`dev_docs/tasks/<name>_plan/` that is deliberately never pushed. A handoff is
what carries that between sessions.

The state-locality principle (the design lives in dotfiles at
`dev_docs/designs/2026-08-01-state-locality-design.md`) still holds in general:
state belongs where its owner keeps it, and a handoff is a poor tracker for
anything that outlives a few sessions. Overriding that is allowed, and it works
because it is deliberate. Say in the file that you are doing it and why, so the
next session inherits a decision instead of guessing at an accident.

## Naming

`YYYY-MM-DD-<short-topic>.md`. A file that records a moment carries its date in
the name; that is what makes a stale handoff obviously stale rather than quietly
wrong.

## Front matter

Obsidian YAML:

```yaml
---
created: 2026-09-09
aim: "what this work is for, in a sentence"
branch: bestdan/<branch> # omit if none yet, or name the trunk branch to merge into
task: https://github.com/bestdan/workflow-skills/issues/<n> # omit for a local workstream
pr: https://github.com/bestdan/workflow-skills/pull/<n> # once one exists
expires: "when #713 merges"
---
```

**Quote any value containing `#`.** In unquoted YAML a `#` after whitespace
starts a comment, so `expires: when #713 merges` parses as `when` and silently
loses the condition, which bites exactly when the condition is an issue or PR
number.

`expires` is prose, not a date: "when #713 merges", "when a real run confirms
the claim lands". It is the sentence that tells the next session whether the
file still describes reality.

## Every handoff ends by deleting itself

Close each one with an explicit instruction to the reading agent, because an
agent that is not told to clean up will not:

> **When the work described above is done, delete this file.** It is a handoff,
> not a record. If what it says is worth keeping, it belongs in a commit
> message, a PR body, a design doc under `dev_docs/designs/`, or the tracker.
> Move it there first, then delete this file.

A directory of stale handoffs is worse than an empty one: nothing marks which of
them still describes reality, so the next session reads all of them and trusts
none.

## Finding them

Nothing surfaces these automatically. The directory is hidden, so `rg` and `fd`
skip it by default, and `.gitignore` keeps it out of `git status`. A session
that does not already know to look here will not stumble on the files. Check
this directory when you start work, and pass `-uu` (ripgrep) or `-HI` (fd) when
searching for one.

## Why this directory is ignored twice

`.gitignore` keeps handoffs out of `git status` and out of commits. `dprint.json`
excludes them separately. Measured 2026-09-09: dprint honours `.gitignore`, so
with the gitignore entry in place it never sees this directory, and without it
`dprint output-file-paths` lists every file here. The dprint entry is therefore
a backstop, not a duplicate: it keeps `just check` from failing on an
unformatted handoff if the gitignore entry is ever moved or lost, and it makes
the formatting protection visible where formatting is configured rather than an
invisible side effect of an ignore rule. Keep both.

## Why this one file is tracked

This README was added with `git add -f`, against the rule above, because the
conventions have to reach the next machine and the next session. Guidance that
exists only where it was written is not guidance. Git applies `.gitignore` only
to untracked files, so it stays tracked with no negation pattern.

Editing it later has one wrinkle. Measured 2026-09-09:
`git add dev_docs/.handoffs/README.md` **stages the change**, but because the
pathspec names an ignored directory git also prints the "ignored by one of your
.gitignore files, use -f" hint and **exits 1**. The stage happened; the exit
code says it did not. A `&&` chain or a script reads that as failure, so stage
this file with `git add -A` or `git commit -a`, which touch the tracked entry
without naming the ignored path and exit 0. Reach for `-f` only when adding a
genuinely new file here, which should be close to never.

Every other file here stays untracked, and that asymmetry is the point: the
conventions are shared, the handoffs are not.
