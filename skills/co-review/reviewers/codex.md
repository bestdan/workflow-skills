# co-review reviewer — `codex` (OpenAI Codex CLI)

`codex` is a built-in default reviewer. Unlike `agy`/`devin`/`copilot` it is **stateless and sandboxed**: `codex exec --sandbox read-only` runs the model with no cross-session memory and no write access. So it needs **no pre-flight auth probe** and **no empty-input guard** — it's the simplest reviewer to drive. Its voice is OpenAI, distinct from Claude (the main agent), Gemini (`agy`), and Cognition (`devin`).

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the invocation below.

If `codex` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | codex exec --sandbox read-only "<POINTER>"`
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | codex exec --sandbox read-only "<POINTER>"` (use whatever read-only/sandbox flag your codex version supports).
- **`--local` mode** → swap `gh pr diff <n>` for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

`codex` reads `<INPUT>` from the `cat "<INPUT>" |` pipe, so the path stays out of the command string. `--sandbox read-only` is codex's own read-only enforcement — keep it in **both** the command and the allow-rule.

## Permission allow-rule (exact-match, approve once)

Merge into the `permissions.allow` array (see SKILL.md → Permissions):

```json
"Bash(codex exec --sandbox read-only \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")"
```

The pointer string must match **byte-for-byte** between the command and the rule. If your codex version uses a different read-only flag, update both.
