# co-review reviewer — `agy` (Google Antigravity CLI)

`agy` is a built-in default reviewer. Unlike `codex`, it talks to a cloud backend, so it **requires an Antigravity login** and **network access** — its invocation cannot run inside a restrictive Bash sandbox. If `agy` errors with `not logged into Antigravity` or a network/permission failure, treat it like any missing reviewer: note it, skip it, never fatal.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths, and the shared **probe warnings** (never wrap a probe in `timeout`; disambiguate `127`) — before using the probe and invocation below.

## Auth has a storage split that bites headless/SSH sessions — probe before dispatching

`agy` keeps its OAuth token in **two different places depending on the session**: an interactive GUI login writes it to the **macOS Keychain**, but when it detects an **SSH session** (any of `SSH_TTY`/`SSH_CLIENT`/`SSH_CONNECTION` set — which is how the Claude Code Bash tool commonly runs, e.g. tmux-over-SSH) it switches to a **file-based token store** at `~/.gemini/antigravity-cli/antigravity-oauth-token`. GUI logins only refresh the Keychain, so the file store goes stale/absent, `agy` decides it's "not logged in", and in `-p` print mode it launches an **interactive OAuth flow with a 30s timeout** that can never complete non-interactively — dying with `Error: authentication failed or timed out` after burning 30s **on every dispatch**. (Unsetting the SSH vars doesn't help: `agy` then tries the Keychain and macOS refuses non-interactive access from an SSH session — `exit status 36`, `errSecInteractionNotAllowed`.)

- **Pre-flight probe (before every `agy` dispatch).** Run bare `agy models` (no `timeout` wrapper — see the shared probe warnings in SKILL.md) — **rc 0** in <1s when authenticated (it also lists models, confirming the pinned one exists); **rc≠0** with `Please sign in to view available models` in ~0.8s when not. If it fails, **skip `agy` immediately** (note it, never fatal) rather than dispatch into the 30s hang.
- **Reseed fix (one-time, run by the user in a _GUI_ terminal on the machine — not over SSH).** `SSH_TTY=1 agy` forces file-storage mode while the browser + Keychain are reachable, so its login writes a **fresh `~/.gemini/antigravity-cli/antigravity-oauth-token`**; after that, `agy` over SSH reads that file and self-refreshes it from the stored refresh token. If the probe later starts failing again, that refresh token was rotated/revoked — reseed the same way. (The session may still print a timeout message even when the token was written; verify by re-running the `agy models` probe.)

## `agy` is a stateful, memory-backed agent — not a stateless review function like `codex exec`

It persists every session to a per-conversation SQLite store and keeps cross-session memory (`~/.gemini/antigravity-cli/{conversations,brain,knowledge}`). This changes how you must drive it as a reviewer:

- **Validate `<INPUT>` is non-empty before piping to `agy`.** On empty/failed stdin `agy` does **not** error — it silently retrieves a **prior conversation** and reviews stale, unrelated code with full confidence. Guard the dispatch: `[ -s "<INPUT>" ] && cat "<INPUT>" | agy …` (or check the byte count), and if `<INPUT>` is empty, skip `agy` rather than run it blind.
- **Never place `<INPUT>` under `.git/`.** In a git **worktree** `.git` is a _file_ (a gitdir pointer), not a directory, so a redirect into `.git/…` fails — which is exactly what triggers the empty-stdin fabrication above. Use a real directory.
- **Always start a fresh conversation.** Never pass `--continue` / `--conversation` for a review — each review must depend only on the current diff, never on accumulated memory.
- **Treat `agy` as advisory-only, always reconciled.** It emits confidently-wrong findings on substance (in testing it invented a non-existent `BashUnsandboxed` permission prefix), and being agentic it explores/writes state even under `--sandbox -p`. Never let it be the sole reviewer; its output must always pass through the reconciler.
- **Pin `--model`** for a reproducible reviewer identity (the unpinned default drifts between runs) **and** to exploit agy's real edge — a non-Claude voice. The built-in invocation defaults to `"Gemini 3.5 Flash (High)"`: vendor diversity at low quota cost, and in testing it caught the highest-value finding in ~14s. Pinning a model also makes empty-input degrade gracefully rather than fabricate. Reserve the heavier `"Gemini 3.1 Pro (High)"` for deep or high-stakes reviews — it consumes quota faster.

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && cat "<INPUT>" | agy --sandbox --model "Gemini 3.5 Flash (High)" -p "<POINTER>"` — `agy` reads the diff/rubric on stdin and merges it with the `-p` pointer; `--sandbox` enables its read-only terminal restrictions. The leading `[ -s "<INPUT>" ] &&` is an **agy guard** (a harmless shell-builtin test): it skips the call when `<INPUT>` is empty, defending against the stale-conversation fabrication (the pointer's `NO INPUT` clause is the second layer). `--model "Gemini 3.5 Flash (High)"` pins a non-Claude model for genuine reviewer diversity at low quota cost — swap in `"Gemini 3.1 Pro (High)"` for a deeper (higher-consumption) review, but doing so changes the command string and re-prompts the exact-match approval. `agy` needs network + an Antigravity login, so this line must run **unsandboxed** in the Bash tool — it cannot run under a restrictive sandbox.
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && cat "<INPUT>" | agy --sandbox --model "Gemini 3.5 Flash (High)" -p "<POINTER>"`
- **`--local` mode** → swap `gh pr diff <n>` for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

`agy` reads `<INPUT>` from the `cat "<INPUT>" |` pipe, so the path stays out of the command string.

## Permission allow-rules (exact-match, approve once)

Merge into the `permissions.allow` array (see SKILL.md → Permissions). The first is the reviewer command; the second is the pre-flight auth probe (a read-only status query with no varying arguments):

```json
"Bash(agy --sandbox --model \"Gemini 3.5 Flash (High)\" -p \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")",
"Bash(agy models)"
```

The pointer string must match **byte-for-byte** between the command and the rule. If you pin a different `agy --model` or your version uses a different read-only flag, update both.

> These are the same per-coder auth probes the `select-coder` skill's [`scripts/probe-coders.sh`](../../../scripts/probe-coders.sh) runs to populate its availability cache (`agy models`). co-review keeps its own **live** rc-gate rather than reading that cache — the cache is allowed to be 30 days stale, and a stale `agy: logged_in: true` would reintroduce the 30s hang. If you change the probe command, update it in both places so they don't drift.
