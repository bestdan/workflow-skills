# co-review reviewer — `codex` (OpenAI Codex CLI)

`codex` is a built-in default reviewer. Unlike `agy`/`devin`/`copilot` it is **stateless and sandboxed**: `codex exec --sandbox read-only` runs the model with no cross-session memory and no write access. So it needs **no pre-flight auth probe** and **no empty-input guard** — it's the simplest reviewer to drive. Its voice is OpenAI, distinct from Claude (the main agent), Gemini (`agy`), and Cognition (`devin`).

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the invocation below.

If `codex` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

> **Unpinned model — know what you are actually reviewing with.** Alone among the four reviewers, the invocation below pins **no `--model`**, so codex reviews with whatever `~/.codex/config.toml` sets as the default. On a common setup that is **`gpt-5.6-sol`** — the model METR measured gaming its own evaluations at **55.4% on the honesty suite vs 41.2% for GPT-5.5**, the highest rate it has recorded ([METR](https://metr.org/blog/2026-06-26-gpt-5-6-sol/)), and which the coder matrix carves out of verification-sensitive work by name (see [`../../select-coder/matrix.md`](../../select-coder/matrix.md) → operational modifiers).
>
> The matrix's argument for using Sol anyway is that implementation packets get an independent downstream check. **A review has no such outer loop** — the review verdict _is_ the deliverable, and co-review's reconciler judges the findings it is handed, not the model that produced them. So the carve-out applies here with full force.
>
> **Recommended: pin the model** (see below). Until you do, check what your default actually is with `codex config get model` (or read `~/.codex/config.toml`) rather than assuming.

## Pinning the model (recommended)

Add `--model` to the invocation and to the allow-rule, keeping them byte-for-byte identical:

```
cat "<INPUT>" | codex exec --sandbox read-only --model "gpt-5.6-terra" "<POINTER>"
```

`gpt-5.6-terra` is the default recommendation: Coding Agent Index 77 at `$$`, fast, and Terminal-Bench 78.4 on the official board. Escalate to `gpt-5.5` for a high-stakes review — Terminal-Bench 83.1, #2 on the official board, and the matrix's designated integrity-conservative Codex pick — accepting that it is `$$$` and slow. **Never pin `gpt-5.6-sol`** for a review, per the note above.

> **Verify the flag before you add the rule.** Unlike the rest of this file, the `--model` spelling above was **not** confirmed against a running binary — check `codex exec --help` on your installed version first. If the flag is wrong the command fails, and a failed reviewer is silently skipped (never fatal), so a typo here costs you the reviewer without an error you'd notice. Pinning also changes the command string, so it **re-prompts the exact-match approval** and the allow-rule below must be updated in lockstep.

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | codex exec --sandbox read-only "<POINTER>"`
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | codex exec --sandbox read-only "<POINTER>"` (use whatever read-only/sandbox flag your codex version supports).
- **`--local` mode** → swap `gh pr diff <n>` for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

`codex` reads `<INPUT>` from the `cat "<INPUT>" |` pipe, so the path stays out of the command string. `--sandbox read-only` is codex's own read-only enforcement — keep it in **both** the command and the allow-rule.

## Permission allow-rule (exact-match, approve once)

Merge into the `permissions.allow` array (see SKILL.md → Permissions):

Unpinned (today's default invocation):

```json
"Bash(codex exec --sandbox read-only \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")"
```

Pinned (recommended — match the model to the one in your invocation):

```json
"Bash(codex exec --sandbox read-only --model \"gpt-5.6-terra\" \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")"
```

The pointer string must match **byte-for-byte** between the command and the rule. If your codex version uses a different read-only flag, or you pin a different `--model`, update both.
