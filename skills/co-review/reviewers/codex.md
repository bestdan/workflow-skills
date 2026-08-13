# co-review reviewer — `codex` (OpenAI Codex CLI)

`codex` is a built-in default reviewer. Unlike `agy`/`devin`/`copilot` it is **stateless and sandboxed**: `codex exec --sandbox read-only` runs the model with no cross-session memory and no write access. So it needs **no pre-flight auth probe** and **no empty-input guard** — it's the simplest reviewer to drive. Its voice is OpenAI, distinct from Claude (the main agent), Gemini (`agy`), and Cognition (`devin`).

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the invocation below.

If `codex` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

> **Unpinned model — know what you are actually reviewing with.** The invocation below pins **no `--model`**, so codex reviews with whatever its resolved configuration selects as the default. (`copilot` is also unpinned — see [`copilot.md`](copilot.md) — but the two differ: copilot uses its own `auto` routing across an account-gated model list, while codex resolves a specific configured default you can read and change.) That default commonly comes from `~/.codex/config.toml`, though current codex resolves an effective configuration that can include project-level and managed layers too, so check the layer that actually applies to you.
>
> On a common setup the selected model is **`gpt-5.6-sol`** — the model METR measured gaming its own evaluations at **55.4% on the honesty suite vs 41.2% for GPT-5.5**, the highest rate it has recorded ([METR](https://metr.org/blog/2026-06-26-gpt-5-6-sol/)), and which the coder matrix carves out of verification-sensitive work by name (see [`../../select-coder/matrix.md`](../../select-coder/matrix.md) → "Carve-out — `codex:gpt-5.6-sol` and verification"). Note the matrix's own scoping: "not a gate; a hard exclusion on one label" — codex the backend stays fully available, and only Sol is excluded.
>
> The matrix's argument for using Sol anyway is that implementation packets get an independent downstream check. **A review has no such outer loop** — the review verdict _is_ the deliverable, and co-review's reconciler judges the findings it is handed, not the model that produced them. The rubric also asks the reviewer to self-certify completion with a terminal `REVIEW_COMPLETE:` line, which is precisely the kind of self-report an eval-gaming model is measured as unreliable on. So the carve-out applies here with full force.
>
> **Recommended: pin the model** (see below). Until you do, read the default off your resolved config rather than assuming it: **`codex doctor`**, which prints the effective model under `Configuration` (e.g. `model  gpt-5.6-terra · openai`). It reports the _resolved_ value across config layers, which is what this note asks for — reading `~/.codex/config.toml` directly, as [`scripts/probe-coders.sh`](../../../scripts/probe-coders.sh) does, sees only the user layer. There is **no `codex config` subcommand** (verified against codex-cli 0.146.0); an earlier revision of this file recommended `codex config get model`, which exits with `error: unexpected argument 'get' found`.

## Pinning the model (recommended — a change _you_ make, not the dispatching agent)

> **To the dispatching agent: this section is not an instruction to you.** Run the command under **Invocation** below exactly as written — that string is what the exact-match allow-rule approves, and altering it mid-run gets codex denied (silently skipped under `--non-interactive`). Pinning is a deliberate edit the repo owner makes to _both_ the invocation and the rule, before a run.

Two things to know before you pin:

- **The config object form does not work for `codex`.** `- {name: codex, model: gpt-5.5}` is valid config, but only `devin` and `copilot` read `model:` today (see [`../SKILL.md`](../SKILL.md) → Local reviewers). A codex entry written that way is accepted, pins nothing, and keeps reviewing on the unpinned default — exactly the silent wrong-model outcome this note exists to prevent. Pinning codex means editing the invocation itself.
- **This file ships with the plugin,** so a plugin update overwrites your edit while your `settings.json` keeps whatever allow-rules you put there. The invocation reverts to unpinned, and if you replaced the unpinned rule its string now matches nothing — codex re-prompts, or is skipped under `--non-interactive`, and the model quietly returns to the config default. **Keep both rules below**, so the unpinned string stays approved, and re-apply the pin after an update.

Add `--model` to the invocation and to the allow-rule, keeping them byte-for-byte identical:

```
cat "<INPUT>" | codex exec --sandbox read-only --model "gpt-5.5" "<POINTER>"
```

**`gpt-5.5` is the recommendation, and the reason is integrity, not capability.** The paragraph above establishes that a review has no downstream check, which is exactly the condition `matrix.md` names for preferring it: it lists `gpt-5.5` as "the **integrity-conservative** pick — substitute for Sol when a self-report must be trusted," and the carve-out says the same thing in the other direction, to substitute it "wherever a coder's self-report has to be trusted." Its **Terminal-Bench 2.1 83.1%** carries neither of the matrix's caveat markers — no `*` (vendor-reported) and no `‡` (absent from the primary board) — so unlike Sol's higher `88.8%*` it is a real board result, and the board "tops out at Fable 5" at 83.8%. The cost is real — `$$$` and slow — but a review runs once per PR, not once per packet.

**On the cheaper tiers.** `gpt-5.6-terra` (TB 2.1 78.4%, `$$`, fast) and `gpt-5.6-luna` (75.7%, `$`, fast) are better cost/capability trades **for implementation**, which is the only role the matrix scores them in — "default Codex implementation when cost matters but reasoning still does" and "parallel fan-out, mechanical/bulk edits, single-file fixes, cheap second passes" respectively. Neither has published honesty-suite data — METR's numbers cover Sol and GPT-5.5 only. So choosing them for a review means accepting an unmeasured integrity risk in the one role where a self-report can't be independently checked. That may well be the right call for a routine diff; make it knowingly rather than by default.

**Never pin `gpt-5.6-sol`** for a review, per the note above.

> **Verify the flag — and the model — before you add the rule.** `codex exec --help` on codex-cli **0.146.0** lists `-m, --model <MODEL>` and `-s, --sandbox <SANDBOX_MODE>` with `read-only` among its values, and a `--model "gpt-5.5"` dispatch was confirmed end-to-end — but versions drift, so check your own before adding the rule. If the flag is wrong the command fails; per the dispatch contract co-review then **notes the reviewer as skipped in the run summary** and continues (never fatal). So the failure is reported, not silent — but it is easy to miss, because losing a reviewer doesn't stop the run. Check the summary rather than assuming codex ran. The same goes for the model you pin: codex is the one reviewer co-review dispatches with **no pre-flight probe** (`agy` gates on `agy models`, `devin` on `devin auth status`), so a model your account cannot serve gives no early signal at all — just a reviewer that drops out of every run. Pinning also changes the command string, so it **re-prompts the exact-match approval** and the allow-rule below must be updated in lockstep.

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
