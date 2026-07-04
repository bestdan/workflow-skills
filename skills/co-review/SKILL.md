---
name: co-review
description: Use when the user wants a collaborative review of a PR — their own read reconciled against existing bot/reviewer comments, with high-confidence fixes applied and judgment calls surfaced — typically via /co-review or asking for a "co-review". Flags — --local reviews the uncommitted working tree (no PR); --remote skips local reviewer agents; --post reviews someone else's PR and posts vetted findings to GitHub instead of editing files.
---

# co-review — collaborative PR review

Combines a fresh review of the PR with whatever comments are already on GitHub — plus, optionally, reviews from other local agents you have installed — then splits the result into "auto-fix" and "ask the user."

## Independence note

The main agent writes the review _and_ applies fixes, but does **not** judge whether its own findings are correct. That job is delegated to a sub-agent (the reconciler) which sees the main agent's review, any local-agent reviews, and the GitHub comments side-by-side without knowing which came from "you." This isn't perfect independence — the main agent still chooses what to flag and writes the prose the reconciler reads — but it prevents the obvious failure mode (an agent grading its own homework).

## Modes

Three mode choices:

- **GitHub vs local** — by default co-review operates on a PR (fetches the diff and comments from GitHub). With `--local` it operates on your working tree instead: no PR required, no GitHub calls.
- **Which reviewers** — the main agent always reviews. Other local agents (codex, agy, devin, …) join the pool if configured. `--remote` forces them off for one run.
- **What happens to the findings** — by default co-review assumes the PR is **yours**: it auto-fixes high-confidence items in your working tree. With `--post` it assumes you're reviewing **someone else's** PR: it never touches the code and instead posts the vetted findings back to GitHub as a PR review.

### Flags

- `--local` — review local changes instead of a PR. Diff comes from `git diff <base>`: your working tree (committed **and** uncommitted changes) compared against `<base>`, **plus** any untracked files (`git ls-files --others --exclude-standard`), which `git diff` does not show — read those so brand-new files aren't silently skipped. No `gh` calls are made and no PR is required. Caveat: `git diff <base>` compares against `<base>`'s current tip, so if `<base>` has advanced since you branched it will also surface those upstream commits as reversed changes — diff against the merge-base instead (compute it in a separate call; don't use `$(...)`, per step 2).
- `--base <branch>` — base to diff against in `--local` mode. Defaults to `main`.
- `--remote` — skip local agents for this run: the main agent reviews and folds in GitHub comments as usual, but codex is not probed, asked about, or dispatched, and the config is left untouched. Useful for a quick "just the normal PR review" without spinning up extra agents. Mutually exclusive with `--local` (which drops GitHub entirely); if both are passed, stop and ask which the user meant.
- `--post` — review someone else's PR and post the vetted findings **back to the PR** instead of editing local files. The review and reconciliation are identical to the default flow, but the auto-fix step is replaced: nothing in the working tree is ever changed, and high/medium findings (after you vet them) are submitted as a single GitHub PR review with inline comments. Requires a PR — mutually exclusive with `--local`; if both are passed, stop and ask which the user meant. Composes with `--remote` (post a Claude-only review) and with local reviewers (post a reconciled multi-agent review).

## Local reviewers

Other local agents can act as extra reviewers. Resolution mirrors the task system's config pattern.

**Config file (local):** `dev_docs/co-review/.co-review.yml`. Treat this as **local** config — the setup step below adds the entire `dev_docs/co-review/` folder to the repo's `.gitignore` so it stays out of `git status` and never lands in an accidental commit. (If someone _has_ committed a `.co-review.yml` to a repo you're reviewing, the untrusted-config rules below still apply.)

Because the folder is git-ignored, it lives only in the **main working tree** — a linked git worktree checks out tracked files only, so it never receives the config. Always resolve the config dir against the main working tree, not the current worktree, so every worktree shares one copy:

```bash
CO_REVIEW_DIR="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")/dev_docs/co-review"
```

In the main worktree this is just `<root>/dev_docs/co-review`; in a linked worktree it points back to the main tree's copy. Use `$CO_REVIEW_DIR` wherever this skill reads or writes `.co-review.yml` (and the same main-tree root for the `.gitignore` guard).

```yaml
local_reviewers:
  - codex # known agent → built-in default invocation
  - agy # known agent (Google Antigravity CLI) → built-in default invocation
  - devin # known agent (Cognition Devin CLI) → built-in default invocation
  - name: devin # object form for a built-in agent → optional model override
    model: swe-1.6 # override devin's --model (defaults to swe-1.6); use swe-1.6-slow on the Free tier
  - name: my-agent # custom agent → explicit invocation
    command: "my-agent review --stdin"
```

A built-in agent may be written as a bare string (`- devin`) or, to override its pinned `--model`, as an object with just `name:` and `model:` (`- {name: devin, model: swe-1.6}`). A `model:` override changes the reviewer command string, so it re-prompts the exact-match approval and the allow-rule must be updated to the new model. (Only `devin` reads `model:` today; a `command:` still marks an agent as a custom, untrusted invocation.)

> **`gemini` is retired.** Google sunset the Gemini CLI, so `gemini` is no longer a built-in reviewer. If an existing config lists `gemini`, **silently skip it** — don't probe for it, don't try to run it, and don't treat its absence as an error. Note in the run summary that the retired `gemini` entry was ignored, and offer to drop it from the config. (Treat any stray `gemini` entry exactly like a missing reviewer: noted, skipped, never fatal.)

**Resolution:**

- **File absent** → not configured yet. Probe `PATH` (`command -v`) for the known default agents and **ask the user** which (if any) to use, then write their choice to the config so it isn't asked again.
- **`local_reviewers: []`** (explicit empty list) → the user chose "none." Run Claude-only and **do not re-ask**. (Absent ≠ empty — that distinction is what lets the skill remember and skip asking.)
- **File with entries** → run the **known built-in agents** (`codex`, `agy`, `devin`) silently and note which ran. Skip any `gemini` entry (retired — see above). Any **custom `command:`**, or any agent not on the built-in list, is untrusted (see the safety note below) — show the user the exact command and get explicit confirmation before running it.

**Detection (PATH probe + config override):** the known default list is `codex`, `agy`, and `devin` (`gemini` is retired — skip it if present). Probe each with `command -v`. The config may also name agents that aren't on the default list, supplying a `command:` for how to invoke them.

> **`agy` (Google Antigravity CLI)** is a built-in default. Unlike `codex`, it talks to a cloud backend, so it **requires an Antigravity login** and **network access** — its invocation cannot run inside a restrictive Bash sandbox. If `agy` errors with `not logged into Antigravity` or a network/permission failure, treat it like any missing reviewer: note it, skip it, never fatal.
>
> **Auth has a storage split that bites headless/SSH sessions — probe before dispatching.** `agy` keeps its OAuth token in **two different places depending on the session**: an interactive GUI login writes it to the **macOS Keychain**, but when it detects an **SSH session** (any of `SSH_TTY`/`SSH_CLIENT`/`SSH_CONNECTION` set — which is how the Claude Code Bash tool commonly runs, e.g. tmux-over-SSH) it switches to a **file-based token store** at `~/.gemini/antigravity-cli/antigravity-oauth-token`. GUI logins only refresh the Keychain, so the file store goes stale/absent, `agy` decides it's "not logged in", and in `-p` print mode it launches an **interactive OAuth flow with a 30s timeout** that can never complete non-interactively — dying with `Error: authentication failed or timed out` after burning 30s **on every dispatch**. (Unsetting the SSH vars doesn't help: `agy` then tries the Keychain and macOS refuses non-interactive access from an SSH session — `exit status 36`, `errSecInteractionNotAllowed`.)
>
> - **Pre-flight probe (before every `agy` dispatch).** Run `agy models` first — **rc 0** in <1s when authenticated (it also lists models, confirming the pinned one exists); **rc≠0** with `Please sign in to view available models` in ~0.8s when not. If it fails, **skip `agy` immediately** (note it, never fatal) rather than dispatch into the 30s hang. See the **Pre-flight auth probe** section below.
> - **Reseed fix (one-time, run by the user in a _GUI_ terminal on the machine — not over SSH).** `SSH_TTY=1 agy` forces file-storage mode while the browser + Keychain are reachable, so its login writes a **fresh `~/.gemini/antigravity-cli/antigravity-oauth-token`**; after that, `agy` over SSH reads that file and self-refreshes it from the stored refresh token. If the probe later starts failing again, that refresh token was rotated/revoked — reseed the same way. (The session may still print a timeout message even when the token was written; verify by re-running the `agy models` probe.)
>
> **`agy` is a stateful, memory-backed agent — not a stateless review function like `codex exec`.** It persists every session to a per-conversation SQLite store and keeps cross-session memory (`~/.gemini/antigravity-cli/{conversations,brain,knowledge}`). This changes how you must drive it as a reviewer:
>
> - **Validate `<INPUT>` is non-empty before piping to `agy`.** On empty/failed stdin `agy` does **not** error — it silently retrieves a **prior conversation** and reviews stale, unrelated code with full confidence. Guard the dispatch: `[ -s "<INPUT>" ] && cat "<INPUT>" | agy …` (or check the byte count), and if `<INPUT>` is empty, skip `agy` rather than run it blind.
> - **Never place `<INPUT>` under `.git/`.** In a git **worktree** `.git` is a _file_ (a gitdir pointer), not a directory, so a redirect into `.git/…` fails — which is exactly what triggers the empty-stdin fabrication above. Use a real directory.
> - **Always start a fresh conversation.** Never pass `--continue` / `--conversation` for a review — each review must depend only on the current diff, never on accumulated memory.
> - **Treat `agy` as advisory-only, always reconciled.** It emits confidently-wrong findings on substance (in testing it invented a non-existent `BashUnsandboxed` permission prefix), and being agentic it explores/writes state even under `--sandbox -p`. Never let it be the sole reviewer; its output must always pass through the reconciler.
> - **Pin `--model`** for a reproducible reviewer identity (the unpinned default drifts between runs) **and** to exploit agy's real edge — a non-Claude voice. The built-in invocation defaults to `"Gemini 3.5 Flash (High)"`: vendor diversity at low quota cost, and in testing it caught the highest-value finding in ~14s. Pinning a model also makes empty-input degrade gracefully rather than fabricate. Reserve the heavier `"Gemini 3.1 Pro (High)"` for deep or high-stakes reviews — it consumes quota faster.

> **`devin` (Cognition Devin CLI)** is a built-in default. Like `agy` — and unlike stateless `codex` — it is **cloud-backed and session-based**: it authenticates to Devin's cloud, runs the model there, and persists a **session per directory** (`devin list`, `-c/--continue`, `-r/--resume`). It therefore **requires a Devin login, network access, and a plan with access to the pinned model** — its invocation **cannot run inside a restrictive Bash sandbox** (under sandbox it panics trying to write its own log file). If `devin` errors with `/upgrade to access this model`, `not authenticated`/`unauthorized`, or a network/permission failure, treat it like any missing reviewer: note it, skip it, never fatal.
>
> **Auth is file-based, so it has no keychain/SSH split like `agy`** — credentials live in `~/.local/share/devin/credentials.toml` and work identically over SSH. But still **pre-flight probe before dispatching**: `devin auth status` returns **rc 0** in <1s when logged in and prints the tier plus **allowed models** (so it doubles as a model-gating check); **rc≠0** / `not authenticated` when not. If it fails, **skip `devin`** (note it, never fatal) instead of dispatching. See the **Pre-flight auth probe** section below.
>
> Because it's stateful and agentic, drive it as a reviewer this specific way (verified against `devin 2026.8.18`):
>
> - **Feed the prompt via `--prompt-file`, never stdin.** This is not the codex/agy stdin pattern. Bare `devin -p` reading piped stdin **panics** (`repl_mode` unwrap on `None`); an inline `devin -p "<prompt>"` argument **suppresses stdin entirely** and makes devin ignore your diff and instead auto-load ambient repo context (git status, `AGENTS.md`, etc.). The only reliable path is to write the assembled `rubric + requests + diff` to `<INPUT>` and pass `--prompt-file "<INPUT>"`. `<INPUT>` is a **fixed absolute path** so the command stays invariant — but note it now appears **in the command string**, so devin's exact-match allow-rule includes that literal path (unlike codex/agy, which pipe via `cat` and keep the path out of the command).
> - **Validate `<INPUT>` is non-empty first** (`[ -s "<INPUT>" ] && devin …`), exactly as for `agy`: never hand a stateful agent an empty prompt file.
> - **Always start a fresh session.** Never pass `-c`/`--continue` or `-r`/`--resume` for a review — each review must depend only on the current diff, never on an accumulated session.
> - **Read-only is enforced structurally, not by prose.** Pass **`--sandbox`** (devin's own OS-level read/write scope enforcement — macOS seatbelt / Linux bwrap+seccomp) **and** **`--permission-mode auto`** (auto-approves _only_ read-only tools; in non-interactive mode any edit/command that isn't auto-approved is denied, not queued). **Never** pass `accept-edits`, `smart`, or `dangerous`. Keep both flags in the command **and** its allow-rule — don't rely on the rubric alone. (`--sandbox` here is devin's own sandbox for its tools; the `devin` process itself still runs **unsandboxed** at the Bash-tool level, since it needs network + log writes.) devin is agentic and will otherwise explore the repo, so the rubric's "review only what follows" framing matters.
> - **Pin `--model`; default `swe-1.6`.** The built-in invocation defaults to **`swe-1.6`** — Cognition's own current-gen SWE model, a voice distinct from Claude (the main agent), OpenAI (`codex`), and Gemini (`agy`), which is the point of a second reviewer; in testing it flagged an injected bug correctly in ~3.5s. **Model access is tier-gated, and the gating is not obvious:** on the **Devin Free tier** every model except `swe-1.6-slow` returns `/upgrade to access this model` (and the unpinned default panics), so Free users must pin **`swe-1.6-slow`**; on **Pro** the `swe-1.6-slow` variant disappears from the model list and `swe-1.6`/`swe-1.6-fast`/`gpt-5.2`/`claude-*`/`gemini-*` become available. Pin whatever your tier allows via the config object form `- {name: devin, model: <m>}`. Note a stale-auth trap: right after upgrading, the cached auth still reports the old gating, so a just-unlocked model can spuriously return `/upgrade` on the first call — re-run `devin auth status` (which re-fetches) and retry before concluding a model is unavailable. Changing the model changes the command string and re-prompts the exact-match approval.
> - **Treat `devin` as advisory-only, always reconciled** — like every external reviewer, its output must pass through the reconciler; never let it be the sole reviewer.

**Pre-flight auth probe.** The cloud-backed reviewers (`agy`, `devin`) fail slow when their auth is dead — `agy` in particular burns a fixed **30s** on a doomed OAuth flow _per dispatch_ (see the `agy` note above). Before dispatching either one, run its cheap auth probe and **skip the reviewer on failure** (note it, never fatal) so a logged-out agent costs ~1s instead of 30s. `codex` needs no probe (stateless, sandboxed). The probes are read-only, invariant commands — approve each once (see Permissions):

- **`agy`** → run bare `agy models` (no `timeout` wrapper — see below) — rc 0 (lists models) when authed; rc≠0 + `Please sign in to view available models` in ~0.8s when not. Run it, and only proceed to the `agy` dispatch if it exits 0. (Reseed instructions for a persistent failure are in the `agy` note above.)
- **`devin`** → run bare `devin auth status` (no `timeout` wrapper — see below) — rc 0 when logged in (prints tier + allowed models); rc≠0 / `not authenticated` when not. Only dispatch `devin` if it exits 0.

> **Run the probe bare; never wrap it in `timeout`.** The probe already self-bounds at ~1s (that's the point), so it needs no ceiling. `timeout`/`gtimeout` is **not** on stock macOS (no coreutils unless the user `brew install`ed it), so a `timeout 5 agy models` wrapper is itself `command not found` → the shell returns **rc 127**, which reads exactly like the reviewer failing auth when it's fully logged in. Observed 2026-07-04: this false-negatived *both* cloud reviewers at once. If you genuinely need a ceiling, gate on `command -v gtimeout` first — but you don't.
>
> **Don't blind-discard the probe's stderr; disambiguate `127`.** A `127` (or any non-zero) with *no* captured stderr is almost always the **wrapper/tool missing**, not the reviewer unauthenticated — a `2>&1 >/dev/null` swallows the telltale `command not found: timeout`. Before skipping a reviewer, confirm the failure came from the reviewer binary itself (it prints `Please sign in …` / `not authenticated`), not from a missing wrapper. Only skip on a genuine auth failure.

The probe is a fast gate that runs **before** dispatch, not alongside it: in step 5, run the probes first (they're ~1s each — batch them in parallel if you like), wait for them to finish, then dispatch only the reviewers that passed. A probe failure is a **skip**, reported in the run summary alongside any missing reviewers.

> These are the same per-coder auth probes the `select-coder` skill's [`scripts/probe-coders.sh`](../../scripts/probe-coders.sh) runs to populate its availability cache (`agy models` / `devin auth status`). co-review keeps its own **live** rc-gate rather than reading that cache — the cache is allowed to be 30 days stale, and a stale `agy: logged_in: true` would reintroduce the 30s hang. If you change the probe command for a coder, update it in both places so they don't drift.

**Built-in invocations.** Everything that varies per PR — the rubric, any reviewer-specific requests, and the diff — is assembled into **one input file** (`<INPUT>`), which is then either piped on **stdin** with a short fixed-pointer argument (`codex`, `agy`) or handed over with **`--prompt-file "<INPUT>"`** (`devin`). Because nothing variable ends up in the command string — only the fixed `<INPUT>`/pointer — the command is invariant and can be approved once with an exact-match rule (see Permissions).

Assemble the input **and** pipe it to the agent in a **single shell invocation** (one Bash call), so the bytes the agent reads are written and read in the same shell — and therefore the same sandbox context (see the note below for why splitting this across two calls desyncs). Build the stream with `cat` + redirection only — **no `{ }` group, no here-doc** — so every part stays within the permission matcher's documented contract: only `|`, `&&`, `;`, `&`, and newlines split a command into separately-matched segments, and redirection (`>`, `>>`) is transparent to matching. `<INPUT>` is a fixed absolute path (**not** a `$TMPDIR`-relative one); opening it with `>` truncates any leftover file before the diff is read, so a prior run's bytes can never be consumed. **When you dispatch more than one reviewer in parallel (step 5), give each reviewer its _own_ fixed `<INPUT>` (and `<REQUESTS>`) path** — e.g. suffix it per agent (`…/co-review-input.codex`, `.agy`, `.devin`). Each dispatch truncates (`>`) and then reads its `<INPUT>`, so a single shared path would let one agent's truncation clobber another's file mid-read (empty/partial/cross-agent input). Per-agent paths stay fixed, so the exact-match allow-rules still hold — devin's rule just embeds its own `.devin` path.

1. Reviewer-specific requests (if any) go in a **file** — write `<REQUESTS>` (a fixed absolute path) with the requests, or skip it entirely when there are none. Keeping the requests in a file (not inline in the command) is what keeps the command text invariant, so the exact-match approval below still holds whatever the requests say.
2. In **one** shell invocation, assemble then dispatch (keep the pointer **byte-for-byte** identical to the Permissions rules):

- `codex`, GitHub mode, **with** requests → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | codex exec --sandbox read-only "<POINTER>"`
- `codex`, GitHub mode, **no** requests → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | codex exec --sandbox read-only "<POINTER>"` (use whatever read-only/sandbox flag your codex version supports).
- `agy`, GitHub mode, **with** requests → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && cat "<INPUT>" | agy --sandbox --model "Gemini 3.5 Flash (High)" -p "<POINTER>"` — `agy` reads the diff/rubric on stdin and merges it with the `-p` pointer; `--sandbox` enables its read-only terminal restrictions. The leading `[ -s "<INPUT>" ] &&` is an **agy-only guard** (a harmless shell-builtin test): it skips the call when `<INPUT>` is empty, defending against the stale-conversation fabrication (the pointer's `NO INPUT` clause is the second layer). `--model "Gemini 3.5 Flash (High)"` pins a non-Claude model for genuine reviewer diversity at low quota cost — swap in `"Gemini 3.1 Pro (High)"` for a deeper (higher-consumption) review, but doing so changes the command string and re-prompts the exact-match approval. `agy` needs network + an Antigravity login, so this line must run **unsandboxed** in the Bash tool — it cannot run under a restrictive sandbox.
- `agy`, GitHub mode, **no** requests → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && cat "<INPUT>" | agy --sandbox --model "Gemini 3.5 Flash (High)" -p "<POINTER>"`
- `devin`, GitHub mode, **with** requests → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && devin -p --prompt-file "<INPUT>" --model "swe-1.6" --sandbox --permission-mode auto` — devin reads the assembled `rubric + requests + diff` from the file via **`--prompt-file`** (there is **no stdin pipe** and **no `-p "<POINTER>"` argument** — piping stdin panics and an inline prompt suppresses the file; the rubric itself carries the read-only instruction). The leading `[ -s "<INPUT>" ] &&` is the same empty-input guard used for `agy`. `--sandbox` + `--permission-mode auto` enforce read-only at the OS and tool layers; `--model "swe-1.6"` is the default pin (use `swe-1.6-slow` on the Free tier, or swap to `swe-1.6-fast`/`gpt-5.2`/… via the config object form, which re-prompts the exact-match approval). devin needs network + a Devin login, so this line must run **unsandboxed** in the Bash tool. **Never** add `-c`/`--continue`/`-r`/`--resume` — every review is a fresh session.
- `devin`, GitHub mode, **no** requests → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; [ -s "<INPUT>" ] && devin -p --prompt-file "<INPUT>" --model "swe-1.6" --sandbox --permission-mode auto`
- `--local` mode → swap `gh pr diff <n>` for `git diff <base>`, and append any untracked files you read **in the same invocation**: `… ; git diff <base> >> "<INPUT>"; cat <untracked-file> … >> "<INPUT>"; cat "<INPUT>" | <agent> …` (for `devin`, keep the `--prompt-file "<INPUT>"` tail instead of the `cat "<INPUT>" |` pipe).

Each `;`/`|`/`&&`-separated segment is permission-matched on its own — `cat …` → `Bash(cat:*)`, `gh pr diff …` → `Bash(gh pr diff:*)`, `git diff …` → `Bash(git diff:*)`, the `[ -s "<INPUT>" ]` guard (used by `agy` and `devin`) → a shell-builtin test (harmless; those agents run unsandboxed and prompt regardless), and the reviewer tail → its exact rule (`codex`/`agy` read `<INPUT>` from the `cat "<INPUT>" |` pipe; `devin` reads it via `--prompt-file "<INPUT>"`, so the fixed `<INPUT>` path is part of devin's matched command) — and redirection (`>`, `>>`) is transparent to matching, so this assemble-then-dispatch line is fully covered by the rules below. The only things that change between runs are the contents of `<REQUESTS>`, `<INPUT>`, and the diff — all files, never the command text.

where `<POINTER>` is exactly:

> Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.

(The `Do NOT explore … or retrieve any prior conversation` and `If stdin is empty … NO INPUT` clauses exist to discipline **agentic, memory-backed** reviewers like `agy`: they stop it wandering the filesystem and — critically — stop it silently reviewing a **stale prior conversation** when stdin is empty. The clauses are harmless to stateless reviewers like `codex`, so the pointer stays shared.)

A custom agent must supply its own `command:` (input is piped on stdin).

> **Why a single shell call.** Splitting assembly and dispatch across two Bash calls silently feeds reviewers a **stale** diff: assembly runs sandboxed while the network-bound reviewer runs unsandboxed, and `$TMPDIR`/`/tmp` resolve differently across that boundary, so the read comes from a leftover file. Write **and** read `<INPUT>` in the **same** invocation, open it with `>` to truncate leftovers, and key it off a fixed absolute path — never `$TMPDIR`. Don't reach for a `{ }` group or here-doc either: they aren't in the matcher's splitter set (`|`, `&&`, `;`, `&`, newlines), so they'd void the approve-once exact-match rules.

> **Long reviews.** The invocations above capture the agent's **stdout from a foreground Bash call**, which is fine for a fast review but risks the Bash tool's ~7-minute foreground ceiling on a slow one — a hit there kills the run with no output. For a review that could run long, background the dispatch (`run_in_background: true`) and capture to a scratchpad file (`codex exec -o <file>`), then echo the exit code + output byte count so an empty result is detectable. See [`dev_docs/external-agents.md`](../../dev_docs/external-agents.md) for the full pattern. Note that adding `-o <file>` changes the command string, so update the exact-match permission rule in lockstep.

These agents must be constrained to **read-only**: they should emit a review and nothing else. Agentic CLIs like `codex exec`, `agy`, and `devin` can edit files or run commands by default — the pointer/rubric says read-only and each built-in invocation pins the agent's own read-only flag (`codex --sandbox read-only`, `agy --sandbox`, `devin --sandbox --permission-mode auto`), but never rely on the prompt alone: keep the sandbox flag(s) in both the command and its allow-rule, especially in `--local` mode where edits are in flight.

> **Untrusted config — `.co-review.yml` is committed to the repo under review.** This skill runs in repos you don't control, so the config (and any custom `command:`) can be supplied by whoever wrote the repo. Treat a custom `command:`, or any agent not in the built-in list (`codex`, `agy`, `devin`), as untrusted code: **never run it silently.** Print the agent name and the exact command, and get explicit user confirmation before executing. Only the built-in agents invoked through their documented commands may run without a prompt.

## Permissions (approve once)

The reviewer command is **invariant**: everything that varies per PR (the diff and any reviewer-specific requests) travels in the `<INPUT>` file — reached on stdin with a fixed pointer argument (`codex`, `agy`) or via `--prompt-file "<INPUT>"` (`devin`) — so the command string never changes. Approve each reviewer **once** with an **exact-match** rule — no broad wildcard. Merge the rules for the reviewers you use into the `permissions.allow` array in `~/.claude/settings.json` (user-wide) or the repo's `.claude/settings.json` — don't overwrite an existing settings file:

```json
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(gh pr diff:*)",
      "Bash(git diff:*)",
      "Bash(codex exec --sandbox read-only \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")",
      "Bash(agy --sandbox --model \"Gemini 3.5 Flash (High)\" -p \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")",
      "Bash(agy models)",
      "Bash(devin -p --prompt-file \"<INPUT>\" --model \"swe-1.6\" --sandbox --permission-mode auto)",
      "Bash(devin auth status)"
    ]
  }
}
```

Why this is narrow:

- The `codex`, `agy`, and `devin` rules are **exact** — each authorizes only this one read-only review command with that exact prompt/flags. They do **not** grant arbitrary `codex exec` / `agy` / `devin` runs, and they pin the read-only sandbox flag(s) into the approved string (for `devin`, `--sandbox --permission-mode auto`, with no `--continue`/`--resume`). For `devin`, replace `<INPUT>` in the rule with the **same literal fixed absolute path** the invocation writes to (it appears in the command because devin reads it via `--prompt-file`); keep it a stable user-level path so one rule works across repos. Edit the pointer, path, or flags and Claude Code re-prompts, so the approval can't silently come to mean something else.
- `Bash(agy models)` and `Bash(devin auth status)` are the pre-flight auth probes — read-only status queries with no arguments that vary, so they're exact-match and safe to approve once.
- `Bash(cat:*)`, `Bash(gh pr diff:*)`, and `Bash(git diff:*)` cover assembling the input stream — they only **read** repo/PR data; the sole write is the redirected `<INPUT>` temp file (redirection targets aren't constrained by the rule, and it's written and read in the same shell call). Add only the diff source you use (`gh pr diff` for PRs, `git diff` for `--local`).
- The pointer string (and, for `devin`, the `--prompt-file "<INPUT>"` path plus the flag set) must match **byte-for-byte** between the command and the rule. Copy the invocation and the rules together; if you edit one, edit the other. If your `codex`, `agy`, or `devin` version uses a different read-only flag (or you pin a different `agy`/`devin --model`), update both.
- These do **not** cover custom `command:` agents from `.co-review.yml` — those are untrusted by design (see above) and must stay prompt-on-every-run. (Plugins can't ship permission rules — only `agent`/`subagentStatusLine` settings — so this is a manual one-time step per user.)

## Steps

1. **Parse invocation.** Note any `--local`, `--remote`, `--post`, and `--base <branch>` flags and whether a PR number was passed. `--local` and `--remote` are mutually exclusive — if both are present, stop and ask which was meant. `--post` requires a PR and is **mutually exclusive with `--local`** — if both are present, stop and ask which was meant.

2. **Identify the PR** (skip entirely in `--local` mode).
   - If the user passed a PR number, use it.
   - Otherwise: run `git branch --show-current` first, then `gh pr list --head <branch> --json number,url` with the literal branch value substituted in. Do **not** combine them with `$(...)` — command substitution inside a Bash tool call is rejected by the permission matcher even when both subcommands are allowlisted.
   - If none, stop and say so (or suggest `--local` if the user just wants to review uncommitted work).

3. **Gather inputs.**
   - **GitHub mode** (in parallel):
     - **Wait for the bot reviewer first if the PR was just opened.** When you want to reconcile against a bot reviewer (e.g. Copilot) that hasn't posted yet, don't hand-write a `gh pr view … | sleep` poll loop — they drift on interval, timeout, and (critically) the reviewer login. Invoke the shared fixture instead and proceed once it reports `landed`:

       ```bash
       "${CLAUDE_PLUGIN_ROOT}/scripts/await-pr-review.sh" --pr <n> --repo <owner/name>
       ```

       It defaults to the `Copilot` reviewer, fast-returns if the review already exists, and matches both the `reviews[]` author login (`copilot-pull-request-reviewer`) and the `reviewRequests[]` display name (`Copilot`) — see the script header. Skip this if you're not waiting on a bot (the review is already there, or there's no bot reviewer).
     - `gh pr view <n> --json title,body,reviews,comments,files`
     - `gh pr diff <n>`
     - `gh api repos/{owner}/{repo}/pulls/<n>/comments` for inline review comments (top-level `comments` from `gh pr view` does not include inline diff comments).
   - **Local mode** (`--local`): `git diff <base>` (default `base = main`) for tracked changes, **plus** untracked files via `git ls-files --others --exclude-standard` so new files aren't missed (mind the merge-base caveat in the Flags section if `<base>` has advanced). No `gh` calls. There are no GitHub comments to reconcile.

4. **Resolve local reviewers.** If `--remote` was passed, skip this step entirely — no probe, no prompt, no config write — and continue with no local agents. Otherwise read `$CO_REVIEW_DIR/.co-review.yml` (resolve `CO_REVIEW_DIR` against the main working tree as shown under **Local reviewers → Config file** — do **not** use `git rev-parse --show-toplevel`, which points at the current worktree and misses the git-ignored config):
   - Absent → probe `PATH` for the known agents and ask the user which to use, then write the choice (including an empty list if they decline all) to the config. Since this is local config, also keep it out of git by adding the entire `dev_docs/co-review/` folder to the main tree's `.gitignore` (the `git check-ignore` guard keeps it idempotent):

     ```bash
     MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
     git -C "$MAIN_ROOT" check-ignore -q dev_docs/co-review/ || echo 'dev_docs/co-review/' >> "$MAIN_ROOT/.gitignore"
     ```
   - Empty list → no local reviewers; continue Claude-only.
   - Entries present → the built-in agents (`codex`, `agy`, `devin`) are used; for any custom `command:` or unknown agent, show it and get explicit confirmation first (see the untrusted-config note). Skip any `gemini` entry (retired — note it was ignored and offer to drop it from the config). Note which will run.

5. **Dispatch local-agent reviews** (if any) **in parallel.** For the cloud-backed reviewers, **run the pre-flight auth probe first** (`agy models` / `devin auth status` — see **Local reviewers → Pre-flight auth probe**) and dispatch only the ones that exit 0; a probe failure is a skip (noted in the summary, never fatal), which spares you `agy`'s 30s auth-timeout hang. Then assemble the `<INPUT>` file (rubric + any reviewer-specific requests + diff) and hand it to each surviving agent exactly as described under **Local reviewers → Built-in invocations** — piped on stdin with the fixed pointer prompt for `codex`/`agy`, or via `--prompt-file "<INPUT>"` for `devin`; capture stdout. **Give each parallel reviewer its own fixed `<INPUT>` path** (suffix per agent) so their truncate-then-read cycles can't race on a shared file — see the per-agent-path note under Built-in invocations. For any custom `command:` or non-built-in agent, show the command and get explicit user confirmation before the first run (untrusted config; see the note in Local reviewers). If an agent errors, times out, or isn't actually runnable, note it and continue — a missing reviewer is not fatal. Output is free-form prose; do not impose a JSON contract on external tools.

6. **Assess scope first.** Before any per-line review, judge whether the change is too big and should be split. Only raise this if you have **high confidence** — don't flag every multi-file change. Signals that justify a split call:
   - Multiple unrelated concerns in one diff (e.g., a refactor + a feature + a config change).
   - Distinct logical units that could land independently without breaking each other.
   - A reviewer realistically cannot hold the whole change in their head.

   Mere line count or file count alone is **not** sufficient — a large mechanical rename is fine as one unit. If you do call a split, name the proposed pieces concretely (files/hunks + one-line description each), and present the recommendation to the user as part of the review. If the change is appropriately sized, say so. (This runs in `--local` mode too — useful before a PR even exists.)

7. **Review the change yourself.** Form an independent review focused on:
   - Correctness and obvious bugs
   - Project conventions (CLAUDE.md / AGENTS.md already in context)
   - Security and perf where relevant
   - Test coverage gaps that matter
     Skip nitpicks, formatting, and pre-existing issues. Produce a list of findings with `file:line`, the issue, and your suggested fix.

8. **Spawn the reconciler sub-agent** (`general-purpose`). Give it:
   - The full diff
   - All GitHub inline comments (with author + path + line) — none in `--local` mode
   - Every review's findings — your own and each local agent's — labelled neutrally as "Reviewer A", "Reviewer B", … alongside the GitHub authors, **not** tagged with which agent produced them. The reconciler should not know which list came from "you" or which came from codex.

   Ask the sub-agent to:
   - Decide for each finding whether it's correct, given the diff and the project context it can read from the repo.
   - Assign a confidence: **high** (clearly correct, low-risk fix), **medium** (probably correct but a judgment call), **low** (wrong, not applicable to this codebase, or over-engineering for a personal repo).
   - Return a JSON array, one object per finding: `{file, line, issue, source, confidence, recommended_fix, rationale}`.
   - Treat suggestions that are over-engineered for this codebase (e.g., enterprise hardening for a personal repo) or that don't apply to its actual setup (e.g., worktree handling on a directly-cloned repo) as **low** confidence and say why — the sub-agent won't see this skill's Rules section unless you pass it along.
   - **In `--post` mode**, tell the reconciler the findings will be posted as comments on **someone else's** PR, so each `recommended_fix` should read as a concrete suggestion addressed to the author, not as an edit you're about to make.

9. **Reconcile and present** to the user. Always note which reviewers contributed (Claude + which local agents ran, or which were skipped and why). Then branch on disposition:
   - **Default (your PR):**
     - Auto-fix list (high confidence) — state what you will change.
     - Ask list (medium) — one yes/no question per item.
     - Skip list (low) — name them so the user can override if they disagree.
   - **`--post` mode (someone else's PR):**
     - Post-candidate list — **high + medium** findings, as a single numbered list. For each: `file:line`, the issue, the suggested fix, and its tier. These are what _may_ be posted, pending your vetting (step 10).
     - Skip list (low) — name them so the user can pull one back in if they disagree.

The remaining steps depend on disposition.

**Default disposition (your PR):**

10. **Apply high-confidence fixes** with Edit. Verify each:
    - Shell scripts: `bash -n`
    - Code: lint / type-check / tests if the project has them
    - Don't bundle in unrelated cleanups.

11. **Wait for the user's answers** on the medium items. Apply the ones they say yes to.

12. **Commit and push the changes.** Once the fixes are applied and verified, commit them and push to the current branch's upstream:
    - **Never commit/push to the default branch.** If the current branch is the repo's default branch (`main`/`master`), stop and tell the user to move the fixes onto a feature branch first — don't auto-commit or push review fixes straight to the default branch.
    - Stage only the files you changed as part of the review fixes — don't sweep in unrelated work that was already in the working tree.
    - Write a concise commit message describing the review fixes (e.g., `Apply co-review fixes`), summarizing the items addressed.
    - Push the current branch. If it already has an upstream (`git rev-parse --abbrev-ref --symbolic-full-name @{u}` succeeds), a plain `git push` is enough. Otherwise set one explicitly against the branch's intended remote — `git push -u <remote> HEAD`, where `<remote>` is the configured remote (default `origin`, but don't assume it: fall back to whatever `git remote` reports if `origin` isn't present).
    - If there are no committable changes (nothing was auto-fixed and the user approved nothing), skip this step.
    - Then summarize what changed and confirm the commit/push.

**`--post` disposition (someone else's PR):**

10. **Vet before posting.** Never touch the working tree — you don't own this code. Present the numbered post-candidate list and let the user deselect, edit the wording of, or pull a low finding into any candidate. Nothing is posted until the user explicitly approves the final set. If they approve none, stop and say so — post nothing.

11. **Choose the verdict.** Ask the user which review event to submit: `COMMENT` (neutral), `REQUEST_CHANGES`, or `APPROVE`. Ask this every run; don't assume.

12. **Post one batched PR review.** Submit a single review via `gh api repos/{owner}/{repo}/pulls/<n>/reviews` (use `--method POST` with `--input` reading a JSON file you write, so quoting and newlines survive):
    - `event` = the chosen verdict.
    - `body` = a short summary plus any findings that can't be anchored to a specific diff line (e.g., "missing test for X", whole-file concerns).
    - `comments` = an array of `{path, line, body}`, one per anchored candidate. `line` is the **actual file line number on the right/new side** of the diff (not a relative diff position) — a comment on an unchanged line is rejected by the API. Before submitting, anchor-check every comment: confirm its `line` is among the diff's added/modified right-side lines, and fold any that don't anchor into `body` instead. The review POST is **atomic** — a single bad line rejects the whole review and posts nothing, so validate up front rather than reacting to a rejection. If the POST still fails, retry once with the offending comment(s) moved to `body`.

13. **Report the result.** Print the review URL (`gh pr view <n> --json url` plus the review, or the API response's `html_url`). Don't commit or push anything — you changed no files.

## Rules

- Respect AGENTS.md / CLAUDE.md instructions already loaded.
- Don't re-litigate decisions the user made earlier in the conversation.
- If a bot or local-agent comment is wrong for this codebase (e.g., over-engineering for a personal repo, worktree-handling on a directly-cloned repo), the reconciler should mark it low — say so explicitly so the user sees why it was skipped.
- Never auto-fix items the user has already declined in this session.
- A local agent that fails to run is noted and skipped, never fatal.
- `gemini` is retired (the Gemini CLI was sunset). Never probe for or invoke it; silently skip any `gemini` entry left in an existing config, note that it was ignored, and offer to drop it.
- Don't re-ask the local-reviewer question once a config (including an explicit empty list) exists.
- Never silently run a custom `command:` or non-built-in agent from `.co-review.yml` — it's repo-controlled, untrusted code. Show it and confirm first.
- In `--post` mode, never edit the reviewed code — it isn't yours. The only output is the GitHub review.
- In `--post` mode, nothing is posted to GitHub until the user has vetted and explicitly approved the final comment set and chosen the verdict.
