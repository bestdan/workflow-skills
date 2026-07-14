# co-review reviewer — `agy` (Google Antigravity CLI)

`agy` is a built-in default reviewer. Unlike `codex`, it talks to a cloud backend, so it **requires an Antigravity login** and **network access** — its invocation cannot run inside a restrictive Bash sandbox. If `agy` errors with `not logged into Antigravity` or a network/permission failure, treat it like any missing reviewer: note it, skip it, never fatal.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths, and the shared **probe warnings** (never wrap a probe in `timeout`; disambiguate `127`) — before using the probe and invocation below.

## Auth has a storage split that bites headless/SSH sessions — probe before dispatching

`agy` keeps its OAuth token in **two different places depending on the session**: an interactive GUI login writes it to the **macOS Keychain**, but when it detects an **SSH session** (any of `SSH_TTY`/`SSH_CLIENT`/`SSH_CONNECTION` set — which is how the Claude Code Bash tool commonly runs, e.g. tmux-over-SSH) it switches to a **file-based token store** at `~/.gemini/antigravity-cli/antigravity-oauth-token`. GUI logins only refresh the Keychain, so the file store goes stale/absent, `agy` decides it's "not logged in", and in `-p` print mode it launches an **interactive OAuth flow with a 30s timeout** that can never complete non-interactively — dying with `Error: authentication failed or timed out` after burning 30s **on every dispatch**. (Unsetting the SSH vars doesn't help: `agy` then tries the Keychain and macOS refuses non-interactive access from an SSH session — `exit status 36`, `errSecInteractionNotAllowed`.)

- **Pre-flight probe (before every `agy` dispatch).** Run bare `agy models` (no `timeout` wrapper — see the shared probe warnings in SKILL.md) — **rc 0** in <1s when authenticated (it also lists models, confirming the pinned one exists); **rc≠0** with `Please sign in to view available models` in ~0.8s when not. If it fails, **skip `agy` immediately** (note it, never fatal) rather than dispatch into the 30s hang.
- **Reseed fix (one-time, run by the user in a _GUI_ terminal on the machine — not over SSH).** `SSH_TTY=1 agy` forces file-storage mode while the browser + Keychain are reachable, so its login writes a **fresh `~/.gemini/antigravity-cli/antigravity-oauth-token`**; after that, `agy` over SSH reads that file and self-refreshes it from the stored refresh token. If the probe later starts failing again, that refresh token was rotated/revoked — reseed the same way. (The session may still print a timeout message even when the token was written; verify by re-running the `agy models` probe.)

## `agy` does not read stdin — the input must be inlined into the `-p` argument

**This is the single most important rule on this page.** In print mode (`-p`/`--print`), `agy` (verified on **1.1.2**) **ignores stdin entirely** — both `cat "<INPUT>" | agy …` and `agy … < "<INPUT>"` deliver nothing. The pipe is not "empty"; it is never read.

The failure is silent and looks like a co-review success. With the pointer below, `agy` receives only the pointer — no rubric, no diff — dutifully hits the pointer's own `If there is no input … output exactly NO INPUT` clause, and prints **`NO INPUT`** even when `<INPUT>` is 62KB and the `[ -s "<INPUT>" ]` guard passed. Without that clause it does something worse: it goes looking for the input it was told to expect, wandering its own conversation SQLite store and the filesystem for a diff that was never handed to it, then dies on `Error: timeout waiting for response` (observed 2026-07-13). The old "empty stdin makes `agy` fabricate from a prior conversation" lore was a **misdiagnosis of this same bug** — stdin was never empty, it was never read.

**Fix: pass the rubric+diff as part of the `-p` argument** (`-p "<POINTER>\n\n$(cat "<INPUT>")"`), per the invocation below. Verified end-to-end: a 62KB input inlined this way reaches the model intact, including a token buried on its final line.

- **Mind the argument-size ceiling — it is reachable.** The input now travels in `argv`, bounded by `ARG_MAX` (**1 MB** on macOS — `getconf ARG_MAX`). A typical PR diff is fine (62KB verified); a large one is not, and overflowing it fails the dispatch outright with `argument list too long` (observed on a 1.4MB diff). **If `<INPUT>` exceeds 500KB, switch to the oversize form below** — do not skip `agy`, and do not truncate the diff. Large PRs are where a second opinion is worth most.
- **Keep the `[ -s "<INPUT>" ]` guard anyway.** It is cheap, and it still catches a genuinely failed assembly (a broken `gh pr diff`) before you spend a cloud call reviewing a rubric with no diff attached.
- **Never place `<INPUT>` under `.git/`.** In a git **worktree** `.git` is a _file_ (a gitdir pointer), not a directory, so a redirect into `.git/…` fails and `<INPUT>` never gets written. Use a real directory.

## `agy` is a stateful, memory-backed agent — not a stateless review function like `codex exec`

It persists every session to a per-conversation SQLite store and keeps cross-session memory (`~/.gemini/antigravity-cli/{conversations,brain,knowledge}`). This changes how you must drive it as a reviewer:

- **Always start a fresh conversation.** Never pass `--continue` / `--conversation` for a review — each review must depend only on the current diff, never on accumulated memory.
- **Treat `agy` as advisory-only, always reconciled.** It emits confidently-wrong findings on substance (in testing it invented a non-existent `BashUnsandboxed` permission prefix), and being agentic it explores/writes state even under `--sandbox -p`. Never let it be the sole reviewer; its output must always pass through the reconciler.
- **Pin `--model`** for a reproducible reviewer identity (the unpinned default drifts between runs) **and** to exploit agy's real edge — a non-Claude voice. The built-in invocation defaults to `"Gemini 3.5 Flash (High)"`: vendor diversity at low quota cost, and in testing it caught the highest-value finding in ~14s. Reserve the heavier `"Gemini 3.1 Pro (High)"` for deep or high-stakes reviews — it consumes quota faster.

## Invocation (assemble + dispatch in one shell call)

`agy` is the one reviewer that takes its input **in the `-p` argument**, not on stdin (see the stdin rule above). The `$(cat "<INPUT>")` substitution is what carries the rubric and diff into the prompt:

**GitHub mode, with requests** — the newline between `<AGY-POINTER>` and the `$(cat …)` is part of the prompt, not a command separator:

```sh
cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && agy --sandbox --model "Gemini 3.5 Flash (High)" -p "<AGY-POINTER>

$(cat "<INPUT>")"
```

`--sandbox` enables `agy`'s read-only terminal restrictions. The leading `[ -s "<INPUT>" ] &&` is an **agy guard** (a harmless shell-builtin test): it skips the call when assembly produced nothing. `--model "Gemini 3.5 Flash (High)"` pins a non-Claude model for genuine reviewer diversity at low quota cost — swap in `"Gemini 3.1 Pro (High)"` for a deeper (higher-consumption) review, but doing so changes the command string and re-prompts the approval. `agy` needs network + an Antigravity login, so this line must run **unsandboxed** in the Bash tool — it cannot run under a restrictive sandbox.

- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument from the assembling `cat`; the dispatch tail is unchanged.
- **`--local` mode** → swap `gh pr diff <n>` for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

**Oversize mode (`<INPUT>` > 500KB).** The diff cannot fit in `argv`, so hand `agy` the **path** instead and let it read the file itself (it is agentic; under `--sandbox` that read is read-only). Replace the dispatch tail with `[ -s "<INPUT>" ] && agy --sandbox --model "Gemini 3.5 Flash (High)" -p "<AGY-FILE-POINTER>"`, where `<AGY-FILE-POINTER>` is exactly:

> Your entire input is the file at `<INPUT>` (a review rubric followed by a diff). Read that file and review ONLY it. Do NOT explore any other file, run commands, or retrieve any prior conversation or memory. Output findings as file:line, the issue, and a suggested fix. Read only.

This is the **fallback, not the default** — it trades the inline form's hard guarantee that `agy` saw exactly the bytes you assembled for a filesystem read it performs itself. Verified on a 1.4MB input: it read the file and returned a normal `file:line` review. `<INPUT>` is a fixed per-agent path, so this command is invariant too — it just needs its own allow-rule (below).

`<AGY-POINTER>` is the shared `<POINTER>` from SKILL.md with its two `stdin` references retargeted — `agy` has no stdin, so a pointer that talks about one is what produced the spurious `NO INPUT`:

> Review ONLY the rubric and diff below. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If there is no rubric or diff below, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.

## Permission allow-rules (exact-match, approve once)

Merge into the `permissions.allow` array (see SKILL.md → Permissions). The first is the reviewer command, the second its oversize form (add only if you review PRs big enough to need it), the third the pre-flight auth probe (a read-only status query with no varying arguments):

```json
"Bash(agy --sandbox --model \"Gemini 3.5 Flash (High)\" -p \"Review ONLY the rubric and diff below. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If there is no rubric or diff below, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\n\n$(cat \\\"<INPUT>\\\")\")",
"Bash(agy --sandbox --model \"Gemini 3.5 Flash (High)\" -p \"Your entire input is the file at <INPUT> (a review rubric followed by a diff). Read that file and review ONLY it. Do NOT explore any other file, run commands, or retrieve any prior conversation or memory. Output findings as file:line, the issue, and a suggested fix. Read only.\")",
"Bash(agy models)"
```

The rule stays **exact-match** even though the review content varies, because permission matching sees the command **before** expansion: the diff never appears in the command text — only the literal `$(cat "<INPUT>")` does, and `<INPUT>` is a fixed per-agent path. The pointer, flags, and that substitution must match **byte-for-byte** between the invocation and the rule. If your Claude Code build re-prompts anyway on the command substitution, approve it — it is the same fixed command each run.

> These are the same per-coder auth probes the `select-coder` skill's [`scripts/probe-coders.sh`](../../../scripts/probe-coders.sh) runs to populate its availability cache (`agy models`). co-review keeps its own **live** rc-gate rather than reading that cache — the cache is allowed to be 30 days stale, and a stale `agy: logged_in: true` would reintroduce the 30s hang. If you change the probe command, update it in both places so they don't drift.
