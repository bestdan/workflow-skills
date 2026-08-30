---
name: co-review
description: Use when the user wants a collaborative review of a PR — their own read reconciled against existing bot/reviewer comments, with high-confidence fixes applied and judgment calls surfaced — typically via /co-review or asking for a "co-review". Flags — --local reviews the uncommitted working tree (no PR); --remote skips local reviewer agents; --post reviews someone else's PR and posts vetted findings to GitHub instead of editing files; --non-interactive runs unattended with no prompts and bounded reviewer waits.
---

# co-review — collaborative PR review

Combines a fresh review of the PR with whatever comments are already on GitHub — plus, optionally, reviews from other local agents you have installed — then splits the result into "auto-fix" and "ask the user."

## Independence note

The main agent writes the review _and_ applies fixes, but does **not** judge whether its own findings are correct. That job is delegated to a sub-agent (the reconciler) which sees the main agent's review, any local-agent reviews, and the GitHub comments side-by-side without knowing which came from "you." This isn't perfect independence — the main agent still chooses what to flag and writes the prose the reconciler reads — but it prevents the obvious failure mode (an agent grading its own homework).

## Modes

Three mode choices:

- **GitHub vs local** — by default co-review operates on a PR (fetches the diff and comments from GitHub). With `--local` it operates on your working tree instead: no PR required, no GitHub calls.
- **Which reviewers** — the main agent always reviews. Other local agents (codex, agy, devin, copilot, …) join the pool if configured. `--remote` forces them off for one run.
- **What happens to the findings** — by default co-review assumes the PR is **yours**: it auto-fixes high-confidence items in your working tree. With `--post` it assumes you're reviewing **someone else's** PR: it never touches the code and instead posts the vetted findings back to GitHub as a PR review.

### Flags

- `--local` — review local changes instead of a PR. Diff comes from `git diff <base>`: your working tree (committed **and** uncommitted changes) compared against `<base>`, **plus** any untracked files (`git ls-files --others --exclude-standard`), which `git diff` does not show — read those so brand-new files aren't silently skipped. No `gh` calls are made and no PR is required. Caveat: `git diff <base>` compares against `<base>`'s current tip, so if `<base>` has advanced since you branched it will also surface those upstream commits as reversed changes — diff against the merge-base instead (compute it in a separate call; don't use `$(...)`, per step 2).
- `--base <branch>` — base to diff against in `--local` mode. Defaults to `main`.
- `--remote` — skip local agents for this run: the main agent reviews and folds in GitHub comments as usual, but codex is not probed, asked about, or dispatched, and the config is left untouched. Useful for a quick "just the normal PR review" without spinning up extra agents. Mutually exclusive with `--local` (which drops GitHub entirely); if both are passed, stop and ask which the user meant.
- `--post` — review someone else's PR and post the vetted findings **back to the PR** instead of editing local files. The review and reconciliation are identical to the default flow, but the auto-fix step is replaced: nothing in the working tree is ever changed, and high/medium findings (after you vet them) are submitted as a single GitHub PR review with inline comments. Requires a PR — mutually exclusive with `--local`; if both are passed, stop and ask which the user meant. Composes with `--remote` (post a Claude-only review) and with local reviewers (post a reconciled multi-agent review).
- `--non-interactive` — run unattended: never prompt, bound every reviewer wait, and disable untrusted custom commands. Built for callers with no human in the loop (the auto-pilot `/deliver-task` lifecycle). Every decision that would otherwise prompt takes a documented default or is skipped with a logged note, and the run summary reports which reviewer classes actually ran. See **Non-interactive mode** for the full policy. Composes with all other flags.
- `--reviewer-set cheap-single` — under `--non-interactive`, run exactly one
  available cheap built-in local reviewer (`codex` first, then `agy`) plus the
  normal main-agent/reconciler path. Do not dispatch any other configured local
  reviewer or remote bot; log the selected reviewer or that none was available.
  Other invocations are unchanged.
- `--allow-command <cmd>` — pre-approve one custom/non-built-in reviewer command for this run (repeatable). Only meaningful with `--non-interactive`, where untrusted commands are otherwise skipped (they can't be confirmed with no human present). The value must match the config's `command:` string byte-for-byte; a non-matching custom command is still skipped and logged. This allowlist is the **caller's** explicit approval (the user or orchestrator passing the flag) — it is never sourced from the repo's `.co-review.yml`, so a repo can't self-approve its own custom command.

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
  - copilot # known agent (GitHub Copilot CLI) → built-in default invocation
  - name: devin # object form for a built-in agent → optional model override
    model: swe-1.6 # override devin's --model (defaults to swe-1.6); check `devin models list` for your tier
  - name: my-agent # custom agent → explicit invocation
    command: "my-agent review --stdin"
```

A built-in agent may be written as a bare string (`- devin`) or, to override its pinned `--model`, as an object with just `name:` and `model:` (`- {name: devin, model: swe-1.6}`). A `model:` override changes the reviewer command string, so it re-prompts the exact-match approval and the allow-rule must be updated to the new model. (`devin` and `copilot` read `model:` today — for `copilot` it appends an otherwise-omitted `--model`, see its note below; a `command:` still marks an agent as a custom, untrusted invocation.)

> **`gemini` is retired.** Google sunset the Gemini CLI, so `gemini` is no longer a built-in reviewer. If an existing config lists `gemini`, **silently skip it** — don't probe for it, don't try to run it, and don't treat its absence as an error. Note in the run summary that the retired `gemini` entry was ignored, and offer to drop it from the config. (Treat any stray `gemini` entry exactly like a missing reviewer: noted, skipped, never fatal.)

**Resolution:**

- **File absent** → not configured yet. Probe `PATH` (`command -v`) for the known default agents and **ask the user** which (if any) to use, then write their choice to the config so it isn't asked again.
- **`local_reviewers: []`** (explicit empty list) → the user chose "none." Run Claude-only and **do not re-ask**. (Absent ≠ empty — that distinction is what lets the skill remember and skip asking.)
- **File with entries** → run the **known built-in agents** (`codex`, `agy`, `devin`, `copilot`) silently and note which ran. Skip any `gemini` entry (retired — see above). Any **custom `command:`**, or any agent not on the built-in list, is untrusted (see the safety note below) — show the user the exact command and get explicit confirmation before running it.

**Detection (PATH probe + config override):** the known default list is `codex`, `agy`, `devin`, and `copilot` (`gemini` is retired — skip it if present). Probe each with `command -v`. The config may also name agents that aren't on the default list, supplying a `command:` for how to invoke them.

**Built-in reviewer files.** Each built-in reviewer's full playbook — auth/availability quirks, pre-flight probe (if any), driving rules, exact invocation (GitHub + `--local`, with/without requests), and permission allow-rule(s) — lives in its own file under [`reviewers/`](reviewers/). **Before dispatching a reviewer, read its file** and follow it. Load only the files for the reviewers that survive config resolution + PATH probe (+ auth probe), so a run pays for only the reviewers it actually uses:

| Reviewer  | Voice         | Cloud (needs login + network)? | Pre-flight probe              | Playbook                                       |
| --------- | ------------- | ------------------------------ | ----------------------------- | ---------------------------------------------- |
| `codex`   | OpenAI        | no — stateless, sandboxed      | none needed                   | [`reviewers/codex.md`](reviewers/codex.md)     |
| `agy`     | Gemini        | yes — Antigravity              | `agy models` (rc-gate)        | [`reviewers/agy.md`](reviewers/agy.md)         |
| `devin`   | Cognition SWE | yes — Devin                    | `devin auth status` (rc-gate) | [`reviewers/devin.md`](reviewers/devin.md)     |
| `copilot` | GitHub-routed | yes — GitHub Copilot           | none — caught from output     | [`reviewers/copilot.md`](reviewers/copilot.md) |

All four are **advisory-only** and must pass through the reconciler; a missing, unauthenticated, or failing reviewer is always noted and skipped, **never fatal**. The three cloud reviewers run **unsandboxed** in the Bash tool (they need network). Everything they share — the single-shell-call dispatch contract, the `<INPUT>`/`<REQUESTS>`/`<POINTER>` placeholders, per-agent paths, the pre-flight probe warnings, and the shared permission rules — is documented below and referenced by each file. `gemini` is retired (see above).

**Pre-flight auth probe.** The **probe-gated** cloud reviewers (`agy`, `devin`) fail slow when their auth is dead — `agy` in particular burns a fixed **30s** on a doomed OAuth flow _per dispatch_ (see [`reviewers/agy.md`](reviewers/agy.md)). Before dispatching either, run its cheap auth probe (the exact command is in the reviewer's file) and **skip the reviewer on failure** (note it, never fatal) so a logged-out agent costs ~1s instead of 30s. `codex` needs no probe (stateless, sandboxed), and `copilot` gets none either — it has no `auth status` subcommand and fails fast, so its auth/policy failure is caught from its **output** at dispatch (unreliable exit code — see [`reviewers/copilot.md`](reviewers/copilot.md)), not by a pre-flight probe. The probes are read-only, invariant commands — approve each once (see Permissions and the reviewer files). Two cross-cutting probe cautions:

> **Run the probe bare; never wrap it in `timeout`.** The probe already self-bounds at ~1s (that's the point), so it needs no ceiling. `timeout`/`gtimeout` is **not** on stock macOS (no coreutils unless the user `brew install`ed it), so a `timeout 5 agy models` wrapper is itself `command not found` → the shell returns **rc 127**, which reads exactly like the reviewer failing auth when it's fully logged in. Observed 2026-07-04: this false-negatived _both_ cloud reviewers at once. If you genuinely need a ceiling, gate on `command -v gtimeout` first — but you don't.
>
> **Don't blind-discard the probe's stderr; disambiguate `127`.** A `127` (or any non-zero) with _no_ captured stderr is almost always the **wrapper/tool missing**, not the reviewer unauthenticated — a `>/dev/null 2>&1` swallows the telltale `command not found: timeout`. Before skipping a reviewer, confirm the failure came from the reviewer binary itself (it prints `Please sign in …` / `not authenticated`), not from a missing wrapper. Only skip on a genuine auth failure.

The probe is a fast gate that runs **before** dispatch, not alongside it: in step 5, run the probes first (they're ~1s each — batch them in parallel if you like), wait for them to finish, then dispatch only the reviewers that passed. A probe failure is a **skip**, reported in the run summary alongside any missing reviewers. (These live rc-gates deliberately duplicate the `select-coder` skill's [`scripts/probe-coders.sh`](../../scripts/probe-coders.sh) probes rather than read its up-to-30-days-stale availability cache — a stale `agy: logged_in: true` would reintroduce the 30s hang. Each reviewer file notes this so the two stay in sync.)

**Built-in invocations.** Everything that varies per PR — the rubric, any reviewer-specific requests, and the diff — is assembled into **one input file** (`<INPUT>`), which is then handed to the reviewer one of three ways: piped on **stdin** with a short fixed-pointer argument (`codex`, `copilot`), named **by path inside the pointer** (`agy` — it does **not** read stdin, so its pointer tells it which file to read; see [`reviewers/agy.md`](reviewers/agy.md)), or handed over with **`--prompt-file "<INPUT>"`** (`devin`). Because nothing variable ends up in the command string — only the fixed `<INPUT>` path and the pointer — the command is invariant and can be approved once with an exact-match rule (see Permissions).

Assemble the input **and** hand it to the agent in a **single shell invocation** (one Bash call), so the bytes the agent reads are written and read in the same shell — and therefore the same sandbox context (see the note below for why splitting this across two calls desyncs). Build the stream with `cat` + redirection only — **no `{ }` group, no here-doc** — so every part stays within the permission matcher's documented contract: only `|`, `&&`, `;`, `&`, and newlines split a command into separately-matched segments, and redirection (`>`, `>>`) is transparent to matching. `<INPUT>` is a fixed absolute path (**not** a `$TMPDIR`-relative one); opening it with `>` truncates any leftover file before the diff is read, so a prior run's bytes can never be consumed. **When you dispatch more than one reviewer in parallel (step 5), give each reviewer its _own_ fixed `<INPUT>` (and `<REQUESTS>`) path** — e.g. suffix it per agent (`…/co-review-input.codex`, `.agy`, `.devin`, `.copilot`). Each dispatch truncates (`>`) and then reads its `<INPUT>`, so a single shared path would let one agent's truncation clobber another's file mid-read (empty/partial/cross-agent input). Per-agent paths stay fixed, so the exact-match allow-rules still hold — devin's rule just embeds its own `.devin` path.

1. Reviewer-specific requests (if any) go in a **file** — write `<REQUESTS>` (a fixed absolute path) with the requests, or skip it entirely when there are none. Keeping the requests in a file (not inline in the command) is what keeps the command text invariant, so the exact-match approval below still holds whatever the requests say.
2. In **one** shell invocation, assemble then dispatch. The exact per-reviewer command (GitHub + with/without requests) lives in that reviewer's file under [`reviewers/`](reviewers/) — **read it and copy the command byte-for-byte** (the pointer/flags must stay identical to the Permissions rules). They all share this shape: `cat "<this skill dir>/review_prompt.md" ["<REQUESTS>"] > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; <dispatch>`, where `<dispatch>` is `cat "<INPUT>" | <agent> …` for the **stdin** reviewers (`codex`, `copilot`), `agy … -p "<AGY-POINTER>"` for **agy** (no stdin — its pointer names `<INPUT>` by path and agy reads the file), or `devin … --prompt-file "<INPUT>"` for **devin**. The stateful reviewers (`agy`, `devin`, `copilot`) prefix the dispatch with the `[ -s "<INPUT>" ] &&` empty-input guard; `codex` does not need it.

> **Chain assembly with `&&` when the reviewer reads `<INPUT>` itself** (`agy`, `devin`). The `[ -s "<INPUT>" ]` guard **cannot** detect a failed `gh pr diff`: the rubric is written first, so `<INPUT>` is non-empty either way, and `;` dispatches regardless of `gh`'s exit code — producing a confident review of a rubric with **no diff attached**. Use `cat … > "<INPUT>" && gh pr diff <n> >> "<INPUT>" && <dispatch>` so the dispatch is gated on the diff actually landing. (`&&` is in the matcher's splitter set, so the approve-once rules are unaffected.)

- `--local` mode → swap `gh pr diff <n>` for `git diff <base>`, and append any untracked files you read **in the same invocation**: `… ; git diff <base> >> "<INPUT>"; cat <untracked-file> … >> "<INPUT>"; <dispatch>` (keeping each reviewer's own dispatch tail from step 2).

Each `;`/`|`/`&&`-separated segment is permission-matched on its own — `cat …` → `Bash(cat:*)`, `gh pr diff …` → `Bash(gh pr diff:*)`, `git diff …` → `Bash(git diff:*)`, the `[ -s "<INPUT>" ]` guard (used by `agy`, `devin`, and `copilot`) → a shell-builtin test (harmless; those agents run unsandboxed and prompt regardless), and the reviewer tail → its exact rule (`codex`/`copilot` read `<INPUT>` from the `cat "<INPUT>" |` pipe; `agy` is told `<INPUT>`'s path inside its `-p` pointer and reads the file itself; `devin` reads it via `--prompt-file "<INPUT>"` — so for `agy` and `devin` the fixed `<INPUT>` path is part of the matched command) — and redirection (`>`, `>>`) is transparent to matching, so this assemble-then-dispatch line is fully covered by the rules below. The only things that change between runs are the contents of `<REQUESTS>`, `<INPUT>`, and the diff — all files, never the command text.

where `<POINTER>` is exactly:

> Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.

(The `Do NOT explore … or retrieve any prior conversation` and `If stdin is empty … NO INPUT` clauses exist to discipline **agentic, memory-backed** reviewers like `agy`: they stop it wandering the filesystem and — critically — stop it silently reviewing a **stale prior conversation** when it has no diff to work from. The clauses are harmless to stateless reviewers like `codex`, so the pointer stays shared.)

**`agy` takes a retargeted pointer (`<AGY-POINTER>`).** It has no stdin, so the two `stdin` references above are false for it — and a pointer telling it its input is "on stdin" is precisely what made it emit a spurious `NO INPUT` on a full 62KB diff. Its variant names `<INPUT>` by path instead and lives in [`reviewers/agy.md`](reviewers/agy.md); copy it from there byte-for-byte.

A custom agent must supply its own `command:` (input is piped on stdin).

> **Why a single shell call.** Splitting assembly and dispatch across two Bash calls silently feeds reviewers a **stale** diff: assembly runs sandboxed while the network-bound reviewer runs unsandboxed, and `$TMPDIR`/`/tmp` resolve differently across that boundary, so the read comes from a leftover file. Write **and** read `<INPUT>` in the **same** invocation, open it with `>` to truncate leftovers, and key it off a fixed absolute path — never `$TMPDIR`. Don't reach for a `{ }` group or here-doc either: they aren't in the matcher's splitter set (`|`, `&&`, `;`, `&`, newlines), so they'd void the approve-once exact-match rules.

> **Long reviews.** The invocations above capture the agent's **stdout from a foreground Bash call**, which is fine for a fast review but risks the Bash tool's ~7-minute foreground ceiling on a slow one — a hit there kills the run with no output. For a review that could run long, background the dispatch (`run_in_background: true`) and capture to a scratchpad file (`codex exec -o <file>`), then echo the exit code + output byte count so an empty result is detectable. See [`dev_docs/external-agents.md`](../../dev_docs/external-agents.md) for the full pattern. Note that adding `-o <file>` changes the command string, so update the exact-match permission rule in lockstep. Byte count alone only catches a **silent** result — an agent that exits 0 after a preamble, a progress update, or a tool result, with no findings, produces non-empty output that reads as a clean review. `review_prompt.md`'s rubric requires a terminal `REVIEW_COMPLETE: PASS` / `REVIEW_COMPLETE: FINDINGS` line; check for it in the captured output alongside the byte count. Missing it means the review is **incomplete, not PASS** — treat it like a skipped reviewer (noted, never fatal).

These agents must be constrained to **read-only**: they should emit a review and nothing else. Agentic CLIs like `codex exec`, `agy`, `devin`, and `copilot` can edit files or run commands by default — the pointer/rubric says read-only and each built-in invocation pins the agent's own read-only posture (`codex --sandbox read-only`, `agy --sandbox`, `devin --permission-mode auto`, `copilot --no-ask-user` **with no** `--allow-all-tools`/`--yolo`), but never rely on the prompt alone: keep the read-only flag(s) in both the command and its allow-rule, especially in `--local` mode where edits are in flight. Note `devin` still has no OS-level sandbox — its `--sandbox` had to be dropped (see the reviewer file), so the tool layer alone bounds what it may _do_. What it may read _as instructions_ is bounded separately: devin is dispatched from a fixed neutral cwd so the reviewed repo's own agent config is never on its discovery path.

> **Untrusted config — `.co-review.yml` is committed to the repo under review.** This skill runs in repos you don't control, so the config (and any custom `command:`) can be supplied by whoever wrote the repo. Treat a custom `command:`, or any agent not in the built-in list (`codex`, `agy`, `devin`, `copilot`), as untrusted code: **never run it silently.** Print the agent name and the exact command, and get explicit user confirmation before executing. Only the built-in agents invoked through their documented commands may run without a prompt.

## Staleness pre-flight

Stale local git state silently poisons a review: in `--local` mode the diff is computed against a `<base>` branch that's behind its remote (upstream commits show up as reversed changes, or are missed entirely), and in the default disposition auto-fixes get committed and pushed onto a branch whose remote tip has already moved. Before gathering inputs, run the shared fixture:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/preflight-freshness.sh" --ref <branch> [--ref <branch> ...]
```

It compares each local branch to its remote tip via `git ls-remote` — read-only, nothing is fetched or mutated — and ends with a parseable `FRESHNESS:` line (see the script header). Which refs to check depends on mode:

- **Default (your PR):** the current branch — fixes are committed and pushed to it in step 12, so a stale local copy means a rejected push or clobbered upstream commits.
- **`--local`:** the `<base>` branch (default `main`) — the diff is computed against it.
- **`--post`:** skip the pre-flight — the diff comes from `gh pr diff` and no local files are touched, so local staleness is irrelevant.

Verdict handling:

- **exit 0 (`fresh`)** → proceed.
- **exit 1 (`stale`)** → stop and tell the user which refs are behind, and offer to update (`git pull --ff-only`, or fetch + rebase). Don't auto-pull — moving the working tree out from under in-flight work is the user's call.
- **exit 3 (`unknown`)** → the `ls-remote` couldn't reach the remote (offline, or a network sandbox). Warn, proceed, and note it in the run summary — don't treat it as fatal.

`git ls-remote` needs network, so this call must run unsandboxed in the Bash tool (like the cloud reviewer dispatches).

## Non-interactive mode

`--non-interactive` makes the whole flow safe to run with **no human in the loop** — the auto-pilot orchestrator's `/deliver-task` lifecycle calls it this way. Two guarantees hold for the entire run: **it never prompts**, and **every wait is bounded**. When the flag is absent, everything below is unchanged — the interactive behavior documented elsewhere in this file is the default.

The governing rule: **any decision that would prompt takes the documented default below, or is skipped with a logged note** — never a pending prompt. The run summary (step 9) then reports which reviewer classes ran, which timed out, and which were skipped, so the caller can act on the gaps in the morning.

**Reviewer-class timeouts.** Three classes, three bounds:

- **Local reviewer agents** (the main Claude review and the reconciler sub-agent, in-process) — **no extra bound**; they don't wait on anything external.
- **CLI reviewers** (`codex`, `agy`, `devin`, `copilot` dispatched as local CLIs) — **15 min** each. Background the dispatch (`run_in_background: true`, per the **Long reviews** note) and stop waiting at 15 min; a reviewer still running is treated as timed-out — noted, skipped, never fatal. **Kill the backgrounded process and discard its partial output** when you stop waiting, so no orphaned reviewer keeps running or emits late output into its scratch file after the run moves on.
- **Remote bots** (a GitHub bot review polled via `await-pr-review.sh`, e.g. Copilot) — **20 min**, i.e. pass `--timeout 1200`. The script **exits non-zero on timeout**, which would fail the Bash tool call and halt the run — so invoke it tolerantly (`… --timeout 1200 || true`, or capture and inspect the `AWAIT_REVIEW:` line) and, on timeout, proceed with whatever landed.

**Per-decision defaults (each replaces a prompt):**

- **Conflicting flags** (`--local`+`--remote`, `--local`+`--post`) — can't be resolved without asking, so this is a **hard error**: stop with a logged reason (not a prompt). A correct caller passes a valid combination; the orchestrator does.
- **Staleness `stale`** (step 3) — do **not** stop and offer to update. Log the stale refs and **proceed** (the auto-pilot caller runs its own per-task `git fetch` + freshness gate upstream, per the spec). An `unknown` verdict is warned-and-continued as usual.
- **Reviewer config absent** (step 4) — do **not** prompt and do **not** write `.co-review.yml`. Probe `PATH` and run the **built-in** reviewers that are available and pass their auth probe; if none are, run Claude-only. Log which were used. (The auto-pilot launch phase is where reviewer config is meant to be resolved deliberately; absent config unattended just means "built-ins if present.")
- **Untrusted custom / non-built-in command** (Local reviewers, step 5) — **skip it** with a logged note, **unless** its `command:` string was pre-approved via `--allow-command <cmd>` (byte-for-byte). Repo-controlled code is never run unattended on the strength of the config alone.
- **Medium-confidence findings** (default disposition, steps 9/11) — there's no one to answer the per-item yes/no. Apply **only** high-confidence auto-fixes; record every medium finding as a **deferred judgment call** in the summary (the `/deliver-task` caller logs these to its `QUESTIONS.md` for morning review). Never apply a medium item unattended.
- **`--post` verdict** (step 11, `--post` only) — default the review event to **`COMMENT`** (never `REQUEST_CHANGES`/`APPROVE` unattended) and post the high+medium set without a vetting prompt. `/deliver-task` uses the default (own-PR) disposition, so this path is rare, but it stays deterministic.

**Bots-unavailable fallback.** If the remote bot times out (20 min) or can't run at all (e.g. a draft PR on a repo whose bots don't review drafts), fall back to **local reviewers only** — the run still completes and the summary records that the bot class was skipped and why.

## Permissions (approve once)

The reviewer command is **invariant**: everything that varies per PR (the diff and any reviewer-specific requests) travels in the `<INPUT>` file — reached on stdin with a fixed pointer argument (`codex`, `copilot`), named by its fixed path inside the `-p` pointer (`agy`), or via `--prompt-file "<INPUT>"` from a fixed neutral cwd (`devin` — that cwd is load-bearing, not incidental; see [`reviewers/devin.md`](reviewers/devin.md)) — so the command string never changes. Approve each reviewer **once** with an **exact-match** rule — no broad wildcard. Two layers of rules:

**Shared rules** (input assembly + staleness pre-flight) — add once, they cover every reviewer:

```json
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(gh pr diff:*)",
      "Bash(git diff:*)",
      "Bash(git ls-remote:*)"
    ]
  }
}
```

**Per-reviewer rules** — each reviewer's own exact-match rule(s) (the review command, plus the pre-flight probe for `agy`/`devin`, and for `devin` the two segments that prepare and enter its neutral cwd) live in its file under [`reviewers/`](reviewers/). Add only the ones for the reviewers you use; copy them verbatim. Merge everything into the `permissions.allow` array in `~/.claude/settings.json` (user-wide) or the repo's `.claude/settings.json` — don't overwrite an existing settings file.

Why this is narrow:

- The per-reviewer rules are **exact** — each authorizes only its one read-only review command with that exact prompt/flags, pinning the read-only posture into the approved string (see each reviewer file for the details: `codex --sandbox read-only`; `agy --sandbox --model …`; `devin --permission-mode auto` with a literal `--prompt-file "<INPUT>"` path and a fixed neutral cwd; `copilot --no-ask-user` with **no** `--allow-all-tools`/`--yolo`). None grant arbitrary runs of the agent. Edit the pointer, path, or flags and Claude Code re-prompts, so the approval can't silently come to mean something else. The pointer/flags must match **byte-for-byte** between the reviewer file's invocation and its rule — if you edit one, edit the other.
- The `agy`/`devin` probe rules (`Bash(agy models)`, `Bash(devin auth status)`) are read-only status queries with no varying arguments — exact-match, safe to approve once. `copilot` has no probe rule (no `auth status` command; failures are caught from output).
- `Bash(git ls-remote:*)` covers the staleness pre-flight — it only reads remote ref tips and mutates nothing (no fetch).
- `Bash(cat:*)`, `Bash(gh pr diff:*)`, and `Bash(git diff:*)` cover assembling the input stream — they only **read** repo/PR data; the sole write is the redirected `<INPUT>` temp file (redirection targets aren't constrained by the rule, and it's written and read in the same shell call). Add only the diff source you use (`gh pr diff` for PRs, `git diff` for `--local`).
- These do **not** cover custom `command:` agents from `.co-review.yml` — those are untrusted by design (see above) and must stay prompt-on-every-run. (Plugins can't ship permission rules — only `agent`/`subagentStatusLine` settings — so this is a manual one-time step per user.)

## Comment style — conventional comments

Every finding you show the user or post to GitHub starts with a
[conventional comment](https://conventionalcomments.org/) label:

```
<label> [(decorations)]: <subject>
```

Labels: `praise`, `nitpick`, `suggestion`, `issue`, `todo`, `question`,
`thought`, `chore`, `note`. Decorations: `(non-blocking)`, `(blocking)`,
`(if-minor)`.

The **label** says what the finding is; the **decoration** says what it costs
the merge. Confidence decides neither — it only sets the default:

- **Label** — a defect is `issue`, a clear improvement is `suggestion`, an
  uncertainty is `question` or `thought`, housekeeping is `chore`.
- **Decoration** — `(blocking)` only when the change should not merge as-is.
  Otherwise `(non-blocking)`, or `(if-minor)` when the fix is worth doing only
  if it turns out cheap.
- **Confidence sets the default label, not the weight.** High findings are
  usually `issue:`/`suggestion:`, medium usually `suggestion:`/`question:`, low
  usually `thought:`/`note:`. But a high-confidence typo fix is still
  non-blocking, and a medium-confidence data-loss bug is still blocking — the
  reconciler's confidence is how sure it is, not how much the finding matters.

Two rules that carry the weight here:

- **The label is a claim about the finding, not decoration.** Don't label a
  defect `nitpick` to soften it, and don't label a preference `issue` to force
  it. A blocking decoration on someone else's PR says the change should not
  merge as-is — mean it.
- **`praise:` is a real label — use it.** A review that is only defects reads as
  hostile, especially on someone else's PR. Praise is yours to give, not the
  reviewer pipeline's: it has no `recommended_fix` and no confidence tier, so it
  goes in the summary you present and in the `--post` review `body` — never
  through the finding list.

This applies to **all** dispositions: the lists at step 9, the `body` and
inline `comments` of a `--post` review, and — most of all — a local read of
someone else's PR that you present as an FYI rather than posting, where the
label is the only signal of how hard each finding is meant to land.

## Steps

1. **Parse invocation.** Note any `--local`, `--remote`, `--post`, `--non-interactive`, `--reviewer-set cheap-single`, `--allow-command <cmd>` (repeatable), and `--base <branch>` flags and whether a PR number was passed. `--local` and `--remote` are mutually exclusive — if both are present, stop and ask which was meant. `--post` requires a PR and is **mutually exclusive with `--local`** — if both are present, stop and ask which was meant. `--reviewer-set cheap-single` requires `--non-interactive`; any other reviewer-set value is a hard error. Under `--non-interactive` a conflicting-flag combination cannot be resolved by asking, so it is a **hard error** (stop with a logged reason), not a prompt — see **Non-interactive mode**.

2. **Identify the PR** (skip entirely in `--local` mode).
   - If the user passed a PR number, use it.
   - Otherwise: run `git branch --show-current` first, then `gh pr list --head <branch> --json number,url` with the literal branch value substituted in. Do **not** combine them with `$(...)` — command substitution inside a Bash tool call is rejected by the permission matcher even when both subcommands are allowlisted.
   - If none, stop and say so (or suggest `--local` if the user just wants to review uncommitted work).

3. **Gather inputs.** First run the **staleness pre-flight** (see the section above) on the mode's refs — current branch by default, `<base>` in `--local` mode, skipped in `--post` mode. On `stale`, stop and surface it (offer to update) before anything else; on `unknown`, warn and continue. Under `--non-interactive`, a `stale` verdict is **logged and proceeded past** rather than stopped on (see **Non-interactive mode**). Then:
   - **GitHub mode** (in parallel):
     - **Wait for the bot reviewer first if the PR was just opened.** When you want to reconcile against a bot reviewer (e.g. Copilot) that hasn't posted yet, don't hand-write a `gh pr view … | sleep` poll loop — they drift on interval, timeout, and (critically) the reviewer login. Invoke the shared fixture instead and proceed once it reports `landed`:

       ```bash
       "${CLAUDE_PLUGIN_ROOT}/scripts/await-pr-review.sh" --pr <n> --repo <owner/name>
       ```

       It defaults to the `Copilot` reviewer, fast-returns if the review already exists, and matches both the `reviews[]` author login (`copilot-pull-request-reviewer`) and the `reviewRequests[]` display name (`Copilot`) — see the script header. Skip this if you're not waiting on a bot (the review is already there, or there's no bot reviewer). Under `--non-interactive`, bound the wait at the remote-bot ceiling — pass `--timeout 1200` (20 min) — and invoke it tolerantly (`… || true`, since it **exits non-zero on timeout** and would otherwise fail the Bash call and halt the run); on timeout, proceed with whatever landed and note the bot class as skipped (see **Non-interactive mode**).
     - `gh pr view <n> --json title,body,reviews,comments,files`
     - `gh pr diff <n>`
     - `gh api repos/{owner}/{repo}/pulls/<n>/comments` for inline review comments (top-level `comments` from `gh pr view` does not include inline diff comments).
   - **Local mode** (`--local`): `git diff <base>` (default `base = main`) for tracked changes, **plus** untracked files via `git ls-files --others --exclude-standard` so new files aren't missed (mind the merge-base caveat in the Flags section if `<base>` has advanced). No `gh` calls. There are no GitHub comments to reconcile.

4. **Resolve local reviewers.** If `--remote` was passed, skip this step entirely — no probe, no prompt, no config write — and continue with no local agents. With `--reviewer-set cheap-single`, do not read or write config: probe only `codex`, then `agy`, select the first available one, and skip all others. Otherwise read `$CO_REVIEW_DIR/.co-review.yml` (resolve `CO_REVIEW_DIR` against the main working tree as shown under **Local reviewers → Config file** — do **not** use `git rev-parse --show-toplevel`, which points at the current worktree and misses the git-ignored config). **Under `--non-interactive`, config-absent does not prompt or write** — probe `PATH` and run the available built-in reviewers (Claude-only if none), logging which were used (see **Non-interactive mode**); the bullets below are the interactive behavior:
   - Absent → probe `PATH` for the known agents and ask the user which to use, then write the choice (including an empty list if they decline all) to the config. Since this is local config, also keep it out of git by adding the entire `dev_docs/co-review/` folder to the main tree's `.gitignore` (the `git check-ignore` guard keeps it idempotent):

     ```bash
     MAIN_ROOT="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
     git -C "$MAIN_ROOT" check-ignore -q dev_docs/co-review/ || echo 'dev_docs/co-review/' >> "$MAIN_ROOT/.gitignore"
     ```
   - Empty list → no local reviewers; continue Claude-only.
   - Entries present → the built-in agents (`codex`, `agy`, `devin`, `copilot`) are used; for any custom `command:` or unknown agent, show it and get explicit confirmation first (see the untrusted-config note). Skip any `gemini` entry (retired — note it was ignored and offer to drop it from the config). Note which will run.

5. **Dispatch local-agent reviews** (if any) **in parallel.** **Read each dispatched reviewer's file under [`reviewers/`](reviewers/) first** and follow it — it carries that reviewer's probe, exact invocation, and quirks. For the probe-gated cloud reviewers, **run the pre-flight auth probe first** (`agy models` / `devin auth status`) and dispatch only the ones that exit 0; a probe failure is a skip (noted in the summary, never fatal), which spares you `agy`'s 30s auth-timeout hang. `copilot` has no probe — dispatch it directly and, if its first output line is an auth/policy error rather than a review, skip it (note it, never fatal). Then assemble the `<INPUT>` file (rubric + any reviewer-specific requests + diff) and hand it to each surviving agent per its file — piped on stdin with the fixed pointer prompt for `codex`/`copilot`, named by path inside the `-p` pointer for `agy` (it does **not** read stdin — it reads the file itself), or via `--prompt-file "<INPUT>"` for `devin`; capture stdout (`copilot` also captures stderr via `2>&1`). **Give each parallel reviewer its own fixed `<INPUT>` path** (suffix per agent) so their truncate-then-read cycles can't race on a shared file — see the per-agent-path note under Built-in invocations. For any custom `command:` or non-built-in agent, show the command and get explicit user confirmation before the first run (untrusted config; see the note in Local reviewers) — **under `--non-interactive` this cannot be confirmed, so the custom command is skipped and logged unless it was pre-approved via `--allow-command <cmd>`** (see **Non-interactive mode**). If an agent errors, times out, or isn't actually runnable, note it and continue — a missing reviewer is not fatal. An agent that exits 0 but whose captured output has no terminal `REVIEW_COMPLETE: PASS` / `REVIEW_COMPLETE: FINDINGS` line (per `review_prompt.md`'s completion contract) is **incomplete, not PASS** — treat it exactly like a skipped reviewer: note it and continue, never fatal. But classify before discarding: the pointer strings are pinned byte-for-byte into the exact-match permission rules and say nothing about the verdict line, so a reviewer that emits real findings without the sentinel is a formatting miss, not a dead agent — pass those findings to the reconciler and note the missing verdict in the summary. The incomplete-skip treatment is only for verdict-less output with no findings text; the one inference that stays forbidden is reading a missing sentinel as PASS. Under `--non-interactive`, also stop waiting on any CLI reviewer at its **15-min** bound (background the dispatch per the **Long reviews** note) and treat a still-running reviewer as timed-out. Output is free-form prose; do not impose a JSON contract on external tools — the one terminal verdict line is a completion sentinel, not a schema.

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

8. **Spawn the reconciler sub-agent** (`co-review-reconciler`, bundled at [`agents/co-review-reconciler.md`](../../agents/co-review-reconciler.md)). It is dispatched **without `Edit`/`Write`/`NotebookEdit`** on purpose: the reconciler judges findings and must never author the fix it just graded. Steps 10–11 exist so the user sees the finding list before the tree changes, and under `--post` the tree is never touched at all — a writing reconciler breaks both. If that agent type is unavailable in the harness, fall back to `general-purpose` **and** state in the prompt that the reconciler is read-only and must not modify any file, run any writing command, or stage anything.

   **Record the tree state before dispatching** — `git status --porcelain` **and** a content hash of the tracked changes (`git diff HEAD | shasum`) — then compare both after the sub-agent returns. Porcelain status alone is not enough: it reports each path's status code, not its contents, so in `--local` mode — where the reviewed files are already reported as modified — an edit to one of them leaves the output byte-identical. That is precisely the mode the check exists to guard. On any drift, surface it to the user as an unexpected modification rather than silently inheriting it, and keep those edits out of the auto-fix list. Report **that** the tree changed, not who changed it: a concurrent editor produces the same signal, and asserting a culprit the check cannot identify is the same mis-attribution this guard exists to catch.

   Give it:
   - The full diff
   - All GitHub inline comments (with author + path + line) — none in `--local` mode
   - Every review's findings — your own and each local agent's — labelled neutrally as "Reviewer A", "Reviewer B", … alongside the GitHub authors, **not** tagged with which agent produced them. The reconciler should not know which list came from "you" or which came from codex.

   Ask the sub-agent to — **this list is the fallback contract, and it is a deliberate mirror of [`agents/co-review-reconciler.md`](../../agents/co-review-reconciler.md): keep the two in sync.** The agent file governs a `co-review-reconciler` dispatch, but the fallback never loads it, and neither does any distribution that ships this skill without the plugin's `agents/` directory (the claude.ai zips do exactly that — see [`scripts/build-claude-ai-zips.sh`](../../scripts/build-claude-ai-zips.sh)). So this copy is the only judging contract those paths get. Change a judging rule in one place and change it in the other:
   - Decide for each finding whether it's correct, given the diff and the project context it can read from the repo.
   - Assign a confidence: **high** (clearly correct, low-risk fix), **medium** (probably correct but a judgment call), **low** (wrong, not applicable to this codebase, or over-engineering for a personal repo).
   - Return a JSON array, one object per finding: `{file, line, issue, source, confidence, recommended_fix, rationale}`.
   - Treat suggestions that are over-engineered for this codebase (e.g., enterprise hardening for a personal repo) or that don't apply to its actual setup (e.g., worktree handling on a directly-cloned repo) as **low** confidence and say why — the sub-agent won't see this skill's Rules section unless you pass it along.
   - A finding that recommends pinning or SHA-pinning a GitHub Action to a version/tag is **never high confidence** unless the finding (or your own check — e.g. `gh api repos/<owner>/<repo>/releases/latest`) confirms that version is current — a reviewer endorsing a stale major version is exactly the failure mode this check exists to catch. Downgrade an unverified pin recommendation to medium and say why in the rationale.
   - **In `--post` mode**, tell the reconciler the findings will be posted as comments on **someone else's** PR, so each `recommended_fix` should read as a concrete suggestion addressed to the author, not as an edit you're about to make.

9. **Reconcile and present** to the user. Label every finding per **Comment style** above — exactly one label each: the reconciled tier's label **replaces** any prefix a reviewer agent already wrote into the finding text, so a downgraded item can't keep a stale `(blocking)`. Always note which reviewers contributed (Claude + which local agents ran, or which were skipped and why). Under `--non-interactive`, this note is the run's machine-readable outcome: report each **reviewer class** (local agents, CLI reviewers, remote bots) as ran / timed-out / skipped-with-reason, so the caller can see the coverage gaps. Then branch on disposition:

   **Escalate architectural/design judgment calls to Fable before asking the user.** A medium-confidence item is frequently a technical question with a defensible "right" answer — signal handling, test design patterns, harness structure, and similar calls a reasonable engineer could resolve from the code and conventions alone — not a matter of what the user wants. For those, consult Fable (the Agent tool's `model: fable`) for a decisive recommendation **before** the item reaches the user, then turn the ask into a go/no-go on that recommendation rather than an open-ended "which do you want" question. Reserve a genuinely open question for items that are actual user-preference (priority, scope, whether the work is worth doing at all) — Fable has no standing to answer those on the user's behalf. Note in the summary which items were escalated and what Fable recommended. **A failed escalation is never fatal** — if Fable can't be reached or doesn't come back, note it and put the open question to the user as you would have without this rule, rather than stalling the review on it. This governs the **Default (your PR)** disposition below, where a medium item becomes a question you have to answer. It does not apply under `--non-interactive` (step 11 already skips every medium item there; there's no question to escalate), and it does not apply in `--post` mode, where medium findings aren't asked at all — they go onto the post-candidate list and you vet them at step 10.
   - **Default (your PR):**
     - Auto-fix list (high confidence) — state what you will change.
     - Ask list (medium) — one yes/no question per item, per the escalation rule above.
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

11. **Wait for the user's answers** on the medium items. Apply the ones they say yes to. Under `--non-interactive` there is no one to answer: **skip every medium item** and record it as a deferred judgment call in the summary (the `/deliver-task` caller logs these for morning review) — never apply a medium item unattended. See **Non-interactive mode**.

12. **Commit and push the changes.** Once the fixes are applied and verified, commit them and push to the current branch's upstream:
    - **Never commit/push to the default branch.** If the current branch is the repo's default branch (`main`/`master`), stop and tell the user to move the fixes onto a feature branch first — don't auto-commit or push review fixes straight to the default branch. **Under `--non-interactive`** there is no one to tell: this is a **logged hard error that ends the run** (never a prompt), preserving the no-prompt guarantee. A correct caller (the `/deliver-task` orchestrator) always runs on a feature branch, so this shouldn't trigger.
    - **Guard against pushing into a merged PR (GitHub mode only).** A PR can merge in the window between the review starting and this push. Pushing then still "succeeds" but the fixes land on a dead branch and never reach the base — silently. Two cheap checks bracket the push, both via the shared fixture (parseable trailing `PRGUARD:` line, exit-code gated; runs unsandboxed — it calls `gh` and `git fetch`):
      - **Before committing:** `scripts/pr-fix-guard.sh check --pr <n> [--repo <owner/name>]`. On `state=merged`/`state=closed` (exit 4), **do not push** — jump straight to recovery below. On `state=unknown` (exit 3, gh offline) warn and proceed; the after-check still backstops.
      - **After pushing:** `scripts/pr-fix-guard.sh verify --pr <n> --commit <pushed-sha> [--base <branch>] [--remote <name>] [--repo <owner/name>]`. `verdict=landed`/`verdict=open` → done. `verdict=orphaned` (exit 5) → the merge raced the push; run recovery.
      - **Recovery:** branch from fresh base (`git fetch <remote> <base>`, `git checkout -B <fix-branch> <remote>/<base>`), cherry-pick the orphaned fix commit, push it, and open a follow-up PR referencing the merged one. Interactively, tell the user and confirm the follow-up PR title; **under `--non-interactive`, open the follow-up PR automatically and record its URL in the run summary** — never prompt. This is the mechanical form of the manual recovery that PR #215 did for #214.
      - Detection is by **content diff, not commit ancestry** — squash merges make the branch's commits non-ancestors of base even on a healthy merge, so an `--is-ancestor` check reports every squash as orphaned. The fixture encodes this; don't second-guess it with a hand-rolled ancestry test.
    - Stage only the files you changed as part of the review fixes — don't sweep in unrelated work that was already in the working tree.
    - Write a concise commit message describing the review fixes (e.g., `Apply co-review fixes`), summarizing the items addressed.
    - Push the current branch. If it already has an upstream (`git rev-parse --abbrev-ref --symbolic-full-name @{u}` succeeds), a plain `git push` is enough. Otherwise set one explicitly against the branch's intended remote — `git push -u <remote> HEAD`, where `<remote>` is the configured remote (default `origin`, but don't assume it: fall back to whatever `git remote` reports if `origin` isn't present).
    - If there are no committable changes (nothing was auto-fixed and the user approved nothing), skip this step.
    - Then summarize what changed and confirm the commit/push.

**`--post` disposition (someone else's PR):**

10. **Vet before posting.** Never touch the working tree — you don't own this code. Present the numbered post-candidate list and let the user deselect, edit the wording of, or pull a low finding into any candidate. Nothing is posted until the user explicitly approves the final set. If they approve none, stop and say so — post nothing. **Under `--non-interactive`** there is no one to vet: skip this gate and treat the reconciled high+medium set as the final post set (low findings stay excluded), then continue to step 11 — see **Non-interactive mode**.

11. **Choose the verdict.** Ask the user which review event to submit: `COMMENT` (neutral), `REQUEST_CHANGES`, or `APPROVE`. Ask this every run; don't assume. Under `--non-interactive`, don't ask: default to **`COMMENT`** (never `REQUEST_CHANGES`/`APPROVE` unattended) and post the vetted high+medium set without a prompt (see **Non-interactive mode**).

12. **Post one batched PR review.** Submit a single review via `gh api repos/{owner}/{repo}/pulls/<n>/reviews` (use `--method POST` with `--input` reading a JSON file you write, so quoting and newlines survive):
    - `event` = the chosen verdict.
    - `body` = a short summary plus any findings that can't be anchored to a specific diff line (e.g., "missing test for X", whole-file concerns). Each such finding keeps its conventional-comment label.
    - `comments` = an array of `{path, line, body}`, one per anchored candidate. Each `body` opens with the finding's conventional-comment label (see **Comment style**). `line` is the **actual file line number on the right/new side** of the diff (not a relative diff position) — a comment on an unchanged line is rejected by the API. Before submitting, anchor-check every comment: confirm its `line` is among the diff's added/modified right-side lines, and fold any that don't anchor into `body` instead. The review POST is **atomic** — a single bad line rejects the whole review and posts nothing, so validate up front rather than reacting to a rejection. If the POST still fails, retry once with the offending comment(s) moved to `body`.

13. **Report the result.** Print the review URL (`gh pr view <n> --json url` plus the review, or the API response's `html_url`). Don't commit or push anything — you changed no files.

## Rules

- Respect AGENTS.md / CLAUDE.md instructions already loaded.
- Don't re-litigate decisions the user made earlier in the conversation.
- If a bot or local-agent comment is wrong for this codebase (e.g., over-engineering for a personal repo, worktree-handling on a directly-cloned repo), the reconciler should mark it low — say so explicitly so the user sees why it was skipped.
- **The reconciler never writes, in any disposition.** It is dispatched as `co-review-reconciler`, which has no `Edit`/`Write`/`NotebookEdit`, and its own prompt forbids writing commands. A reconciler that authors a fix has both jumped the user-approval step and graded its own homework. Compare porcelain status **and** a tracked-content hash across the dispatch, and report any drift without claiming who caused it (step 8).
- Never auto-fix items the user has already declined in this session.
- Architectural/design judgment calls (signal handling, test design patterns, harness structure, and similar calls with a defensible "right" answer) get escalated to Fable for a recommendation before they reach the user as a question; genuine user-preference calls (priority, scope, whether to do the work) go to the user directly. See step 9.
- A local agent that fails to run is noted and skipped, never fatal.
- `gemini` is retired (the Gemini CLI was sunset). Never probe for or invoke it; silently skip any `gemini` entry left in an existing config, note that it was ignored, and offer to drop it.
- Don't re-ask the local-reviewer question once a config (including an explicit empty list) exists.
- Never silently run a custom `command:` or non-built-in agent from `.co-review.yml` — it's repo-controlled, untrusted code. Show it and confirm first.
- In `--post` mode, never edit the reviewed code — it isn't yours. The only output is the GitHub review.
- In `--post` mode, nothing is posted to GitHub until the user has vetted and explicitly approved the final comment set and chosen the verdict. **Exception:** `--non-interactive` posts the vetted high+medium set as a `COMMENT` review without a prompt (see **Non-interactive mode**).
- `--non-interactive` never prompts and bounds every reviewer wait. It takes a documented default at each decision point, disables untrusted custom commands (unless pre-approved via `--allow-command`), applies only high-confidence fixes (medium items are logged, never applied), and reports every reviewer class as ran / timed-out / skipped. When the flag is absent, all interactive behavior is unchanged.
- **Validation fixtures / schema changes: run the validator, don't reason on paper.** When the diff adds or edits validation fixtures — or the schema/rules that validate them — **execute the real validator over each fixture row** instead of reasoning about which rules _should_ fire. Execution catches masked or secondary violations (e.g. a malformed UUID, or a field that trips a second rule only once the first passes) that abstract analysis misses. This is for Claude's own review pass, and only where the code is trusted **and** the checkout matches what you're reviewing:
  - **`--local` and the default (own-PR) disposition:** run the validator against the working tree. In GitHub mode, confirm the checkout is the PR head first (you're usually already on the branch); if it isn't, check out the PR head in a temp worktree — or skip with a coverage note — rather than validating unrelated code.
  - **`--post` (someone else's PR):** the validator is contributor-controlled, **untrusted** code — treat it exactly like a custom `command:`. Don't run it silently: confirm with the user and run it only in an isolated environment (no credentials, no network). Under `--non-interactive`, **skip it unless pre-approved via `--allow-command`** and report the fixtures as unvalidated. Otherwise reason from the diff and flag them as unvalidated.
  - The sandboxed external reviewers are read-only and can't run commands, so closing this gap (where it's safe to close) is on Claude.
