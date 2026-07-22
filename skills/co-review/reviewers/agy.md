# co-review reviewer — `agy` (Google Antigravity CLI)

`agy` is a built-in default reviewer. Unlike `codex`, it talks to a cloud backend, so it **requires an Antigravity login** and **network access** — its invocation cannot run inside a restrictive Bash sandbox. If `agy` errors with `not logged into Antigravity` or a network/permission failure, treat it like any missing reviewer: note it, skip it, never fatal.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths, and the shared **probe warnings** (never wrap a probe in `timeout`; disambiguate `127`) — before using the probe and invocation below.

## Auth has a storage split that bites headless/SSH sessions — probe before dispatching

`agy` keeps its OAuth token in **two different places depending on the session**: an interactive GUI login writes it to the **macOS Keychain**, but when it detects an **SSH session** (any of `SSH_TTY`/`SSH_CLIENT`/`SSH_CONNECTION` set — which is how the Claude Code Bash tool commonly runs, e.g. tmux-over-SSH) it switches to a **file-based token store** at `~/.gemini/antigravity-cli/antigravity-oauth-token`. GUI logins only refresh the Keychain, so the file store goes stale/absent, `agy` decides it's "not logged in", and in `-p` print mode it launches an **interactive OAuth flow with a 30s timeout** that can never complete non-interactively — dying with `Error: authentication failed or timed out` after burning 30s **on every dispatch**. (Unsetting the SSH vars doesn't help: `agy` then tries the Keychain and macOS refuses non-interactive access from an SSH session — `exit status 36`, `errSecInteractionNotAllowed`.)

- **Pre-flight probe (before every `agy` dispatch).** Run bare `agy models` (no `timeout` wrapper — see the shared probe warnings in SKILL.md) — **rc 0** in <1s when authenticated (it also lists models, confirming the pinned one exists); **rc≠0** with `Please sign in to view available models` in ~0.8s when not. If it fails, **skip `agy` immediately** (note it, never fatal) rather than dispatch into the 30s hang.
- **Reseed fix (one-time, run by the user in a _GUI_ terminal on the machine — not over SSH).** `SSH_TTY=1 agy` forces file-storage mode while the browser + Keychain are reachable, so its login writes a **fresh `~/.gemini/antigravity-cli/antigravity-oauth-token`**; after that, `agy` over SSH reads that file and self-refreshes it from the stored refresh token. If the probe later starts failing again, that refresh token was rotated/revoked — reseed the same way. (The session may still print a timeout message even when the token was written; verify by re-running the `agy models` probe.)

## `agy` does not read stdin — hand it the input file's **path**

**This is the single most important rule on this page.** In print mode (`-p`/`--print`), `agy` (verified on **1.1.2**) **ignores stdin entirely** — both `cat "<INPUT>" | agy …` and `agy … < "<INPUT>"` deliver nothing. The pipe is not "empty"; it is never read.

The failure is silent and looks like a co-review success. Given a stdin-worded pointer, `agy` receives only the pointer — no rubric, no diff — dutifully hits its `If there is no input … output exactly NO INPUT` clause, and prints **`NO INPUT`** even when `<INPUT>` is 62KB and the `[ -s "<INPUT>" ]` guard passed. Without that clause it does something worse: it goes looking for the input it was told to expect, wandering its own conversation SQLite store and the filesystem for a diff that was never handed to it, then dies on `Error: timeout waiting for response` (observed 2026-07-13). The old "empty stdin makes `agy` fabricate from a prior conversation" lore was a **misdiagnosis of this same bug** — stdin was never empty, it was never read.

**Fix: `agy` is the one reviewer whose pointer names the input _file_** and lets `agy` read it (it is agentic; under `--sandbox` that read is read-only). This is the same shape `devin` already uses via `--prompt-file "<INPUT>"` — file-handoff is the house pattern here, not a workaround.

**Do not inline the input into `-p` instead** (`-p "<POINTER>\n\n$(cat "<INPUT>")"`). It looks like the obvious fix and it is a trap, for two independent reasons:

- **It cannot be approved.** The command would contain a `$(…)` substitution, and **command substitution inside a Bash tool call is rejected by the permission matcher even when every subcommand is allowlisted** (see SKILL.md → step 2, and the `--local` note). So the approve-once exact-match rule can't hold. Worse, under `--non-interactive` an unapprovable command isn't merely a re-prompt — it is **denied**, so `agy` silently drops out of exactly the unattended `/deliver-task` flow where a second opinion matters most.
- **It doesn't fit.** The whole diff would ride in a single `argv` string, capped at **128KiB** on Linux (`MAX_ARG_STRLEN`, 32 pages — independent of the much larger `ARG_MAX`) and 1MB on macOS. Overflow fails outright with `argument list too long` (observed on a 1.4MB diff). The file-pointer form has no such ceiling.

The trade is worth naming: `agy` performs the read itself, so you lose the hard guarantee that it saw exactly the bytes you assembled. `--sandbox` bounds that read to read-only, and the pointer forbids reading anything else — which beats a transport that can't run at all.

- **Chain the assembly with `&&`, not `;`.** The `[ -s "<INPUT>" ]` guard **cannot** catch a failed `gh pr diff`: the rubric is written to `<INPUT>` first, so the file is non-empty whether or not the diff landed, and `;` dispatches anyway on a non-zero `gh`. That yields a confident, successful-looking review of a rubric with **no diff attached** — the same silent-success class of bug this page exists to kill. `&&` between every assembly step is what actually gates the dispatch on the diff arriving.
- **Never place `<INPUT>` under `.git/`.** In a git **worktree** `.git` is a _file_ (a gitdir pointer), not a directory, so a redirect into `.git/…` fails and `<INPUT>` never gets written. Use a real directory.

## Headless `-p` gates `read_file` — trust the input dir with `--add-dir` (agy ≥ 1.1.5)

Because agy reads `<INPUT>` itself (previous section), it invokes its `read_file` tool — and starting in **1.1.5**, headless `-p`/`--print` runs that tool through a permission gate **no prompt can answer**, so it auto-denies and prints:

> jetski: no output produced — a tool required the "read_file" permission that headless mode cannot prompt for, so it was auto-denied.

The cause: a headless run executes under agy's **default "CLI Project" context**, which does **not** treat the cwd (or `trustedWorkspaces` in `settings.json`) as trusted, so the read is gated. Interactive `agy` in the same directory auto-allows the read because it resolves that directory as a trusted workspace — which is why this never reproduces by hand and looks like an auth problem when it isn't.

**Fix: pass `--add-dir "<INPUT-DIR>"`** — the directory that contains `<INPUT>`. That adds it to the run's workspace, so agy's read of `<INPUT>` auto-allows. Verified on **1.1.5**: with `--add-dir` the review runs, and **`write_file` stays gated** — a write attempt still auto-denies and creates no file — so agy remains effectively **read-only**. `<INPUT-DIR>` is the parent of the fixed `<INPUT>` path, so it is itself fixed: adding it keeps the command invariant and the approve-once exact-match rule intact.

Two tempting non-fixes, both rejected:

- **`--dangerously-skip-permissions`** makes the read work but is **not read-only**: `--sandbox` restricts only the _terminal_, not the `write_file` tool, so under skip-permissions agy will write files (verified — it created a file on request). Never use it for a reviewer, especially in `--local` mode with uncommitted changes in flight.
- **`permission.allow` / `toolPermission` in `settings.json`** are **not honored in headless mode** (the CLI logs `permissions=<nil>, toolPermission=request-review` no matter what you set). Runtime grants live in agy-managed `~/.gemini/config/config.json`, which agy rewrites on every run, so a hand-authored grant doesn't stick.

## `agy` is a stateful, memory-backed agent — not a stateless review function like `codex exec`

It persists every session to a per-conversation SQLite store and keeps cross-session memory (`~/.gemini/antigravity-cli/{conversations,brain,knowledge}`). This changes how you must drive it as a reviewer:

- **Always start a fresh conversation.** Never pass `--continue` / `--conversation` for a review — each review must depend only on the current diff, never on accumulated memory.
- **Treat `agy` as advisory-only, always reconciled.** It emits confidently-wrong findings on substance (in testing it invented a non-existent `BashUnsandboxed` permission prefix), and being agentic it explores/writes state even under `--sandbox -p`. Never let it be the sole reviewer; its output must always pass through the reconciler.
- **Pin `--model`** for a reproducible reviewer identity (the unpinned default drifts between runs) **and** to exploit agy's real edge — a non-Claude voice. The built-in invocation defaults to `"Gemini 3.5 Flash (High)"`: vendor diversity at low quota cost, and in testing it caught the highest-value finding in ~14s. Reserve the heavier `"Gemini 3.1 Pro (High)"` for deep or high-stakes reviews — it consumes quota faster.

## Invocation (assemble + dispatch in one shell call)

**GitHub mode, with requests** — note the `&&` between every step: dispatch happens only if the diff actually landed.

```sh
cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>" && gh pr diff <n> >> "<INPUT>" && agy --sandbox --add-dir "<INPUT-DIR>" --model "Gemini 3.5 Flash (High)" -p "<AGY-POINTER>"
```

`--sandbox` enables `agy`'s read-only terminal restrictions. `--add-dir "<INPUT-DIR>"` (the directory holding `<INPUT>`) trusts the input's workspace so headless `read_file` auto-allows while `write_file` stays gated — **omit it and every headless dispatch auto-denies with "no output produced"** (see the read_file-gate section above). `--model "Gemini 3.5 Flash (High)"` pins a non-Claude model for genuine reviewer diversity at low quota cost — swap in `"Gemini 3.1 Pro (High)"` for a deeper (higher-consumption) review, but doing so changes the command string and re-prompts the approval. `agy` needs network + an Antigravity login, so this line must run **unsandboxed** in the Bash tool — it cannot run under a restrictive sandbox.

- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument from the assembling `cat`; the dispatch tail is unchanged.
- **`--local` mode** → swap `gh pr diff <n>` for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md — keeping the `&&` chain.

`<AGY-POINTER>` is agy's own pointer — the shared `<POINTER>` from SKILL.md retargeted from stdin to the input **file**, since agy has no stdin (a pointer that claims otherwise is what produced the spurious `NO INPUT`):

> Your entire input is the file at `<INPUT>` (a review rubric followed by a diff). Read that file and review ONLY it. Do NOT explore any other file, run commands, or retrieve any prior conversation or memory. If that file is missing or empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.

**Substitute `<INPUT>` with the real fixed absolute path** (and `<INPUT-DIR>` with its containing directory) — in the command _and_ in the matching allow-rule below. The path is inside the approved prompt string, so the two must agree byte-for-byte or the rule won't match. (Both stay _fixed_ per-agent paths, so the command is still invariant run-to-run — that's what keeps the approve-once rule honest.)

## Permission allow-rules (exact-match, approve once)

Merge into the `permissions.allow` array (see SKILL.md → Permissions). The first is the reviewer command; the second is the pre-flight auth probe (a read-only status query with no varying arguments). Replace `<INPUT>` with your real fixed absolute path — and `<INPUT-DIR>` with its containing directory — in the first rule:

```json
"Bash(agy --sandbox --add-dir \"<INPUT-DIR>\" --model \"Gemini 3.5 Flash (High)\" -p \"Your entire input is the file at <INPUT> (a review rubric followed by a diff). Read that file and review ONLY it. Do NOT explore any other file, run commands, or retrieve any prior conversation or memory. If that file is missing or empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")",
"Bash(agy models)"
```

Nothing that varies per PR appears in the command — the rubric and diff live in `<INPUT>`, and both `<INPUT>` and `<INPUT-DIR>` are fixed — so this is a genuine **exact-match, approve-once** rule with no command substitution to trip the matcher. The pointer, flags, and paths must match **byte-for-byte** between the invocation and the rule. If you pin a different `agy --model` or change the `<INPUT>` path, update both.

> These are the same per-coder auth probes the `select-coder` skill's [`scripts/probe-coders.sh`](../../../scripts/probe-coders.sh) runs to populate its availability cache (`agy models`). co-review keeps its own **live** rc-gate rather than reading that cache — the cache is allowed to be 30 days stale, and a stale `agy: logged_in: true` would reintroduce the 30s hang. If you change the probe command, update it in both places so they don't drift.
