---
name: local-review
description: Open a local GitHub-style split-diff UI so the USER reads a code change in their browser and leaves inline line comments plus an approve/request-changes verdict, which is written to a JSON file for the agent to act on. Local-only review of agent work before anything reaches GitHub. Point it at a PR, a branch or worktree diff, staged/unstaged changes, a commit range, or a patch file. Use when the user wants to eyeball a diff themselves and comment on it — "show me the diff", "let me look over these changes", "let me comment on specific lines". Not for agent-run review of a PR (that is co-review).
---

# local-review

local-review is a human-in-the-loop diff review surface: a local, stdlib-only
web server renders a GitHub-style split diff, the user leaves inline line
comments and an overall verdict in their browser, and on submit the round is
written to a JSON file the agent reads and acts on. Nothing touches GitHub
unless the diff is a PR and the user explicitly flags a comment for it.

Tool: `${CLAUDE_PLUGIN_ROOT}/scripts/local-review/server.py` (Python stdlib
only; vendored highlight.js in `scripts/local-review/vendor/`). If
`$CLAUDE_PLUGIN_ROOT` is unset and the path doesn't resolve, Glob
`**/scripts/local-review/server.py` and use what that finds. The server vendors
[CaseyDiff](https://github.com/caseycrogers) by Casey Rogers — see the
`NOTICE` beside it.

## 1. Produce the diff to review

The server takes either a PR number (it shells out to `gh pr diff`) or a
unified-diff patch via `--diff-file`. Pick the source from what the user is
reviewing:

- **A GitHub PR** — pass the number and `--repo <owner/repo>` (needs an
  authenticated `gh`).
- **Uncommitted work in a worktree** — `git -C <dir> diff HEAD > <scratch>/review.patch`
  (`HEAD`, not bare `git diff` — bare misses staged changes, `--staged` misses
  unstaged ones). Untracked files never appear in `git diff`; include them with
  `git -C <dir> diff --no-index /dev/null <file> >> <scratch>/review.patch` per
  new file (or `git add -N` them first), so the review covers the whole change.
- **A branch against its base** — `git -C <dir> diff <base>...<head> > <scratch>/review.patch`.
- **Any two refs or a commit range** — `git -C <dir> diff <A> <B> > …`. The
  parser handles standard `git diff` output, so any range works.
- **A patch the user already has** — pass its path to `--diff-file` directly.

Generated files (lockfiles, `*.g.dart`, …) render auto-collapsed; the user can
expand them.

## 2. Launch the server + open the browser

```bash
OUT=<scratch>/lr_comments.json; rm -f "$OUT"
nohup python3 "${CLAUDE_PLUGIN_ROOT}/scripts/local-review/server.py" \
  <PR [--repo o/r] | --diff-file PATCH> \
  --port <port> --out "$OUT" > <scratch>/lr_server.log 2>&1 &
echo $! > <scratch>/lr_server.pid
```

Pick a port unlikely to collide (e.g. 8765; if the log shows "Address already
in use", relaunch on another). Confirm startup by reading the log — it prints
`PR review UI: http://127.0.0.1:<port>` on success.

Then open `http://127.0.0.1:<port>` for the user:

- With browser tooling available (`mcp__claude-in-chrome__*`): create a tab
  with `tabs_create_mcp`, `navigate` to the URL, and confirm it rendered with
  `read_page` (look for the diff title and the **Submit review** button).
- Without browser tooling: print the URL and ask the user to open it. The tool
  is fully usable by hand.

The server keeps running after submit — once the round is collected, kill it
via the recorded PID: `kill "$(cat <scratch>/lr_server.pid)"`.

## 3. What the user does in the UI

Split diff by file, per-file and per-section collapse, syntax highlighting
(language auto-detected per file). The user:

- Clicks a line to add a comment; a saved comment has **✎ edit** and **×**
  delete controls.
- When the diff is a PR, the composer also shows a **Comment on GitHub**
  button. Comments made with it behave like any other comment but carry a
  **→ GitHub** mark; on **Submit** they go to the agent _and_ are posted as
  inline review comments on the PR (via `gh`). Nothing posts until submit. The
  button is absent for non-PR diffs.
- Taps **⊘** on a comment line to mark the whole contiguous comment run for
  removal.
- Clicks **Submit review** → a finish window with an optional overall comment
  and two buttons: **Submit review** (feedback to act on) or **Approve** (the
  change is good).
- If the diff **source** changes while the page is open — the PR on GitHub, or
  the patch file itself — a **↻ Refresh** button appears in the header. Edits
  to the worktree are NOT detected in `--diff-file` mode (the server re-reads
  the patch, not the repo): regenerate the patch file to make new changes
  visible. Refresh discards unsaved comments (with an inline confirm when
  comments are pending). Take one round at a time and re-open between rounds.

## 4. Collect the round

Watch `OUT`; it is written on submit:

```bash
for i in $(seq 1 2400); do [ -s "$OUT" ] && { cat "$OUT"; exit 0; }; sleep 3; done
```

Payload:

```
{ "meta": {...}, "summary": "<optional overall comment>", "approved": <bool>,
  "comments": [ {file, side, line, code, text}, ... ] }
```

A **dismiss-comments** entry carries `endLine` and `kind: "dismiss-comments"`;
act on it by deleting lines `line`–`endLine` on `side`. A comment flagged for
GitHub carries `github: true` (the server also posts it on the PR and returns
`github_posted` / `github_failed` counts). Every other comment carries the
anchored `code` so you can stay on the right line if numbers shifted. Remind
the user that comments live in the page until Submit — a refresh clears them.

## 5. Echo the feedback back — concise, before acting

The moment `OUT` fires, post the captured feedback back in the prompt window so
the user confirms it landed exactly as they meant it. One-line header, one line
per comment. No preamble, no restating the code, no per-comment rationale, no
closing summary.

```
Round <n> · <approved ✓ | changes requested> · <count> comment(s)
summary: <summary>                      ← omit this line entirely when summary is empty
1. <file>:<side><line> — <text>
2. <file>:<side><line>–<endLine> [dismiss block] — <text>
```

`<side>` is `L`/`R`. Add the `–<endLine>` range and the `[dismiss block]` tag
only for a `kind: "dismiss-comments"` entry. Keep each `<text>` verbatim from
the user, not paraphrased. Then act on the round.
