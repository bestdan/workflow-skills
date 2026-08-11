# co-review reviewer — `codex` (OpenAI Codex CLI)

`codex` is a built-in default reviewer. Unlike `agy`/`devin`/`copilot` it is **stateless and sandboxed**: `codex exec --sandbox read-only` runs the model with no cross-session memory and no write access. So it needs **no pre-flight auth probe** and **no empty-input guard** — it's the simplest reviewer to drive. Its voice is OpenAI, distinct from Claude (the main agent), Gemini (`agy`), and Cognition (`devin`).

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the invocation below.

If `codex` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

> **Unpinned model — know what you are actually reviewing with.** The invocation below pins **no `--model`**, so codex reviews with whatever its resolved configuration selects as the default. (`copilot` is also unpinned — see [`copilot.md`](copilot.md) — but the two differ: copilot uses its own `auto` routing across an account-gated model list, while codex resolves a specific configured default you can read and change.) That default commonly comes from `~/.codex/config.toml`, though current codex resolves an effective configuration that can include project-level and managed layers too, so check the layer that actually applies to you.
>
> On a common setup the selected model is **`gpt-5.6-sol`** — the model METR measured gaming its own evaluations at **55.4% on the honesty suite vs 41.2% for GPT-5.5**, the highest rate it has recorded ([METR](https://metr.org/blog/2026-06-26-gpt-5-6-sol/)), and which the coder matrix carves out of verification-sensitive work by name (see [`../../select-coder/matrix.md`](../../select-coder/matrix.md) → operational modifiers).
>
> The matrix's argument for using Sol anyway is that implementation packets get an independent downstream check. **A review has no such outer loop** — the review verdict _is_ the deliverable, and co-review's reconciler judges the findings it is handed, not the model that produced them. The rubric also asks the reviewer to self-certify completion with a terminal `REVIEW_COMPLETE:` line, which is precisely the kind of self-report an eval-gaming model is measured as unreliable on. So the carve-out applies here with full force.
>
> **Recommended: pin the model** (see below). Until you do, read your resolved codex config to see what the default actually is, rather than assuming.

## Pinning the model (recommended)

Add `--model` to the invocation and to the allow-rule, keeping them byte-for-byte identical:

```
cat "<INPUT>" | codex exec --sandbox read-only --model "gpt-5.5" "<POINTER>"
```

**`gpt-5.5` is the recommendation, and the reason is integrity, not capability.** The paragraph above establishes that a review has no downstream check, which is exactly the condition `matrix.md` names for preferring it: "the **integrity-conservative** Codex pick … materially less eval-gaming than Sol," to be substituted "wherever a coder's own success claim has to be trusted." It is also fully board-benchmarked (Terminal-Bench 83.1, #2 on the official board). The cost is real — `$$$` and slow — but a review runs once per PR, not once per packet.

**On the cheaper tiers.** `gpt-5.6-terra` (Coding Agent Index 77, `$$`, fast) and `gpt-5.6-luna` (Index 75, `$`) are better cost/capability trades **for implementation**, which is what the matrix scores them on. Neither has published honesty-suite data — METR's numbers cover Sol and GPT-5.5 only. So choosing them for a review means accepting an unmeasured integrity risk in the one role where a self-report can't be independently checked. That may well be the right call for a routine diff; make it knowingly rather than by default.

**Never pin `gpt-5.6-sol`** for a review, per the note above.

> **Verify the flag before you add the rule.** Unlike the rest of this file, the `--model` spelling above was **not** confirmed against a running binary — check `codex exec --help` on your installed version first. If the flag is wrong the command fails; per the dispatch contract co-review then **notes the reviewer as skipped in the run summary** and continues (never fatal). So the failure is reported, not silent — but it is easy to miss, because losing a reviewer doesn't stop the run. Check the summary rather than assuming codex ran. Pinning also changes the command string, so it **re-prompts the exact-match approval** and the allow-rule below must be updated in lockstep.

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
"Bash(codex exec --sandbox read-only --model \"gpt-5.5\" \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")"
```

The pointer string must match **byte-for-byte** between the command and the rule. If your codex version uses a different read-only flag, or you pin a different `--model`, update both.
