---
created: 2026-11-14
question: "Where does Claude Code substitute ${CLAUDE_PLUGIN_ROOT} in plugin text, and does a --plugin-dir run then read the checkout's handler files?"
feeds: "https://github.com/bestdan/workflow-skills/issues/1542"
---

# Plugin-root substitution under `--plugin-dir` (measured 2026-11-14)

**Claude Code replaces the braced `${CLAUDE_PLUGIN_ROOT}` with the running
plugin's absolute root in both command bodies and `SKILL.md` bodies. It leaves
the unbraced `$CLAUDE_PLUGIN_ROOT` alone.** A command that names its handler
through the braced root therefore reads the `--plugin-dir` checkout's copy
rather than the installed cache's. `scripts/validate.py` enforces that spelling
in command and skill bodies.

Measured on lindev (Linux) with plugin 2.217.0 installed from the marketplace,
so a cache copy of every handler existed for an agent to fall through to.

## Substitution probe

A throwaway plugin `substprobe` with one command and one skill, each asking the
model to echo a string verbatim, run as `claude -p --plugin-dir <probe>
"/substprobe:<name>"` from `/tmp`:

| Body                     | Text sent                                      | Echoed back                           |
| ------------------------ | ---------------------------------------------- | ------------------------------------- |
| `commands/probe.md`      | `${CLAUDE_PLUGIN_ROOT}/commands/handlers/x.md` | `<probe root>/commands/handlers/x.md` |
| `skills/probe2/SKILL.md` | `${CLAUDE_PLUGIN_ROOT}/x`                      | `<probe root>/x`                      |
| `skills/probe2/SKILL.md` | `$CLAUDE_PLUGIN_ROOT/y`                        | `$CLAUDE_PLUGIN_ROOT/y`, unchanged    |

Files the agent reads afterwards with the Read tool, such as
`commands/handlers/*.md` and `skills/*/references/*.md`, are file contents and
were not part of the probe. They reach the model as tool output rather than as
plugin text, so the change assumes, without having measured it, that nothing
substitutes into them, and leaves their relative paths alone.

## End-to-end runs

Both runs used a scratch git repo with no plugin code in it, so a bare
`commands/handlers/…` path could not resolve there.

- `/workflow-skills:list-tasks` with `handler: gh-issue` and
  `--plugin-dir <the #1542 worktree>`: the agent read
  `<worktree>/commands/handlers/gh-issue.md` directly, with no Glob, and ran
  that worktree's `commands/handlers/assets/` scripts.
- `/workflow-skills:add-task` with `handler: repo-pr`, the issue's acceptance
  check: the agent read `<worktree>/commands/handlers/repo-pr.md` directly, with
  no Glob, then `<worktree>/skills/task/SKILL.md`, and wrote the task file into
  the scratch repo.

Before the change, the same commands named the handler as
`commands/handlers/<handler>.md`. That path did not resolve from the scratch
repo, the agent Globbed, and on this machine the match was the installed
2.217.0 copy (#1541's verification comment shows that substitution).
