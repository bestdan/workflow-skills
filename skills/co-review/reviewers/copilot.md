# co-review reviewer — `copilot` (GitHub Copilot CLI)

`copilot` is a built-in default reviewer. Like `agy` and `devin` — and unlike stateless `codex` — it is **cloud-backed and agentic**: it authenticates to GitHub, runs the model in GitHub's cloud, can edit files and run shell commands, and persists a **session** you can resume (`--continue`, `-r`/`--resume`). It therefore **requires a GitHub Copilot subscription with CLI access, an auth token, and network** — its invocation **cannot run inside a restrictive Bash sandbox**. If `copilot` errors with `Access denied by policy settings` (org/subscription gating), an auth/login failure, or a network error, treat it like any missing reviewer: note it, skip it, never fatal.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the invocation below.

## No pre-flight probe — catch failures from output

Auth resolves from a token or the credential store — there is **no `auth status` subcommand**. `copilot` uses, in precedence order, `COPILOT_GITHUB_TOKEN` / `GH_TOKEN` / `GITHUB_TOKEN`, else the OS credential store written by `copilot login`. Unlike `agy`/`devin` there is **no cheap offline probe**, so co-review runs **no** pre-flight probe for it. Instead it fails **fast** (~1–2s) with a diagnostic first line, so catch that from its **captured output** (the invocation appends `2>&1` so stderr is folded in) — not its exit code, which is unreliable (an org-policy denial still exits `0`, observed on `1.0.68`). If the first line of output is an error (`Access denied by policy`, `not authenticated`/login errors) rather than a review, skip it (note it, never fatal).

## Driving quirks (verified against `GitHub Copilot CLI 1.0.68`)

Because it's stateful and agentic, drive it as a reviewer this way:

- **Feed the diff on stdin with `-s -p`.** `-p`/`--prompt "<POINTER>"` runs non-interactively and exits; `-s`/`--silent` strips the stats banner so stdout is just the review. The assembled `rubric + requests + diff` is piped on stdin (`cat "<INPUT>" | copilot …`), exactly the `codex`/`agy` pattern — copilot reads piped stdin as context and merges it with the `-p` pointer.
- **Validate `<INPUT>` is non-empty first** (`[ -s "<INPUT>" ] && … | copilot …`), exactly as for `agy`/`devin`: a session-backed agent handed empty input can retrieve a prior session and review stale, unrelated code.
- **Always start a fresh session.** Never pass `--continue`, `-r`/`--resume`, or `--session-id` for a review — each review must depend only on the current diff, never on an accumulated session.
- **Read-only is enforced structurally, by withholding write permission.** Do **not** pass `--allow-all-tools`, `--yolo`, or set `COPILOT_ALLOW_ALL` — those let it edit files and run shell. Pass **`--no-ask-user`** so it runs autonomously; with no tool pre-approved and no interactive prompt available, any edit/shell tool is denied, not queued. The diff travels in the prompt, so a review needs no tools at all — it's pure text-in/text-out. Keep `--no-ask-user` and the absence of `--allow-all-tools`/`--yolo` in both the command and its allow-rule; don't rely on the rubric alone. (Belt-and-suspenders: `--deny-tool 'shell'` / `--deny-tool 'write'` add explicit denials, but withholding allow-all is the guarantee.)
- **Pin `--model` only via the config object form; the base invocation omits it** and uses copilot's default `auto` routing. copilot's available models are account/subscription-gated and can't be assumed, so — unlike `agy`/`devin` — the built-in invocation does **not** pin one. To pin (e.g. for a reproducible non-Claude voice), use `- {name: copilot, model: <m>}`, which appends `--model <m>` and re-prompts the exact-match approval.
- **Treat `copilot` as advisory-only, always reconciled** — like every external reviewer, its output must pass through the reconciler; never let it be the sole reviewer.

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> --repo <owner>/<name> >> "<INPUT>"; [ -s "<INPUT>" ] && cat "<INPUT>" | copilot -s -p "<POINTER>" --no-ask-user 2>&1` — copilot reads the assembled `rubric + requests + diff` on stdin and merges it with the `-p` pointer; `-s` silences the stats banner, and `--no-ask-user` runs it autonomously. The trailing `2>&1` folds copilot's **stderr into the captured output** — required because its auth/policy failures are detected by parsing that output (its exit code is unreliable, see above), and the diagnostic may land on stderr; the redirection is transparent to the exact-match permission rule. Read-only is structural: **no** `--allow-all-tools`/`--yolo` is passed, so no edit/shell tool is ever pre-approved. The leading `[ -s "<INPUT>" ] &&` is the same empty-input guard used for `agy`/`devin` — copilot is session-backed, so never hand it an empty prompt. The base invocation pins **no** `--model` (copilot's `auto` routing; add `--model <m>` only via the config object form, which re-prompts the approval). copilot needs network + a GitHub Copilot login, so this line must run **unsandboxed** in the Bash tool. **Never** add `--continue`/`-r`/`--resume`/`--session-id` — every review is a fresh session. `<owner>/<name>` is the repo resolved in SKILL.md step 2 — never `cwd`'s by default.
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> --repo <owner>/<name> >> "<INPUT>"; [ -s "<INPUT>" ] && cat "<INPUT>" | copilot -s -p "<POINTER>" --no-ask-user 2>&1`
- **`--local` mode** → swap the `gh pr diff …` segment for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

`copilot` reads `<INPUT>` from the `cat "<INPUT>" |` pipe, so the path stays out of the command string (the `2>&1` redirection is transparent to matching).

## Permission allow-rule (exact-match, approve once)

Merge into the `permissions.allow` array (see [`../references/permissions.md`](../references/permissions.md)). There is **no probe rule** — copilot has no `auth status` command; failures are caught from output:

```json
"Bash(copilot -s -p \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\" --no-ask-user)"
```

The rule pins `--no-ask-user` **with no** `--allow-all-tools`/`--yolo` (so no write/shell tool is pre-approved) and no `--continue`/`--resume`/`--session-id`. The pointer string and flag set must match **byte-for-byte** between the command and the rule; the trailing `2>&1` is redirection and is transparent to matching. If you pin a `--model` or your version uses different flags, update both.
