# co-review reviewer — `grok` (xAI Grok Build CLI)

`grok` is a built-in default reviewer. It is xAI's official terminal coding agent ([`xai-org/grok-build`](https://github.com/xai-org/grok-build), Apache-2.0), pinned here to **`grok-4.6`** — so its voice is xAI's, distinct from Claude (the main agent), OpenAI (`codex`), Gemini (`agy`), Cognition (`devin`), GitHub-routed (`copilot`), and open-weight Kimi (`crush`). It is the seventh vendor lineage in the pool, which is the whole reason it earns a slot.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the gates and invocation below.

If `grok` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

> **This reviewer is the co-review half of a deliberate split: `grok` is a reviewer here and is _not_ a coder backend.** [`../../select-coder/matrix.md`](../../select-coder/matrix.md) records why under "Grok Build is a reviewer, not a coder backend" — in short, the reviewer role reads nothing but a diff from a neutral directory, while a coder backend reads the whole working tree. Do not add a `grok:` row to the coder matrix on the strength of this file.

## Why this file carries two gates and a setup section

Every other reviewer pins its read-only posture inside the approved command string and stops there. `grok` needs more, for a reason specific to it: **xAI shipped a coding CLI that uploaded whole repositories, including secrets, to its own cloud storage, and the user-facing privacy toggle did not stop it.**

The facts, because the mitigation below only makes sense against them:

- A wire-level capture of **0.2.93** (July 12 2026) showed the client bundling the entire tracked repository — all tracked files plus full commit history — and uploading it to a Google Cloud Storage bucket, independent of which files the agent actually read. A file the agent was explicitly denied still shipped inside the bundle. ([repro harness][repro])
- The account-level "Improve the model" toggle governs **training consent, not transmission**. With it off, the settings endpoint kept returning `trace_upload_enabled: true`. ([opt-out capture][optout])
- Redaction is **per-channel and was incomplete**: a test secret was redacted in the trace logs and shipped in plaintext inside the bundle, because bundles went through a separate upload queue. ([SlowMist][slowmist-upload])
- xAI's fix was a **server-side flag**, not a client release. The upload code stayed in the binary. ([Better Stack][betterstack])
- Session traces still carry the **verbatim, unredacted contents of every file the agent reads**, `.env` included, and an August capture still logged trace uploads and analytics events under default settings. ([hardening harness][hardening], [agentjail][agentjail])

So the controls below are not belt-and-braces. Each one closes a leak that was demonstrated on the wire, and each is a **skip** rather than a warning, because a reviewer that silently ships a diff to a third party is worse than no reviewer.

**The good news, and it is the reason this reviewer is addable at all:** the kill switches are real, documented in xAI's own configuration reference, and the layer ordering was verified by a third party as **env > config > remote**. The toggle that failed was the _remote_ layer. A local config pin beats it. ([config reference][config-ref], [hardening harness][hardening])

[repro]: https://github.com/cereblab/grok-build-exfil-repro
[optout]: https://github.com/cereblab/grok-build-exfil-repro/blob/main/PRIVACY_OPTOUT.md
[slowmist-upload]: https://slowmist.medium.com/grok-cli-risk-analysis-how-a-single-prompt-can-send-sensitive-files-to-the-cloud-3ce9243f10af
[betterstack]: https://betterstack.com/community/guides/ai/grok-cli-data-leak/
[hardening]: https://github.com/wetlink/grok-build-privacy-hardening
[agentjail]: https://agentjail.io/blog/grok-cli-uploads-your-repo/
[config-ref]: https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/05-configuration.md
[sandbox-ref]: https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/18-sandbox.md
[headless-ref]: https://github.com/xai-org/grok-build/blob/main/crates/codegen/xai-grok-pager/docs/user-guide/14-headless-mode.md
[readonly-test]: https://github.com/amElnagdy/delegate-skills/pull/119
[slowmist-0day]: https://slowmist.medium.com/xai-grok-build-0-day-one-day-after-open-source-trust-mechanism-bypass-and-the-security-70b676300d98

## One-time setup — the telemetry pin (operator, not agent)

**The agent never performs this step**; it only checks that it was done. Like the permission allow-rules in [`../references/permissions.md`](../references/permissions.md), this is human setup done once before the first `grok` dispatch.

Put these in `~/.grok/config.toml`:

```toml
[features]
telemetry = false

[telemetry]
trace_upload = false

[harness]
disable_codebase_upload = true

[cli]
auto_update = false
```

What each one is for:

- **`[features] telemetry`** is the product-analytics master switch, and `[telemetry] trace_upload` inherits it when unset. Both are pinned explicitly so neither depends on the other's default. ([config reference][config-ref])
- **`[harness] disable_codebase_upload`** is the hard veto on whole-repo upload — the third-party harness confirmed it intercepts at the end of the pipeline even with the telemetry env vars forced on. **It does not appear in xAI's configuration reference**, so it is an undocumented key with no stability contract. That asymmetry is exactly why the version gate below exists. ([hardening harness][hardening])
- **`[cli] auto_update`** off is what makes the version gate meaningful. Auto-update is on by default, so without this the binary you validated is not the binary that runs next week.

**Why config and not the environment variables.** `GROK_TELEMETRY_ENABLED` and `GROK_TELEMETRY_TRACE_UPLOAD` are documented and sit one layer higher, but an env-var prefix changes the command string and so stops it matching the exact-match allow-rule — under `--non-interactive` that is a silent denial. Config is the layer that is both durable and invisible to the matcher. If you want the env layer too, set it in your shell profile rather than in the dispatch.

> **Known gap: cross-session memory.** `GROK_MEMORY=0` disables it and there is **no documented config key** for it, so the same env-prefix problem applies and this file does not pin it. The exposure is bounded — `--cwd` points grok at a directory that only ever holds co-review input — but it is not closed. Set `GROK_MEMORY=0` in your shell profile if you want it closed. This is the `agy` stale-conversation failure mode (see [`agy.md`](agy.md)), and the rubric's "do not retrieve any prior conversation" clause is the only thing standing against it here.

## Pre-flight gates — both are skips, never fatal

Run both before dispatching. Either one failing means **skip `grok`** (noted in the run summary, never fatal). Neither is an auth check; grok's auth and entitlement failures are caught from dispatch output, like `copilot`'s.

**Gate 1 — the telemetry pin is actually present.** This is the one that matters. Run:

```bash
grep -c -e "trace_upload = false" -e "telemetry = false" -e "disable_codebase_upload = true" "$HOME/.grok/config.toml"
```

It must print exactly `3`. Anything else — a missing file (rc 2), a partial pin, a commented-out key — means setup is incomplete, so **skip grok** with the reason: `grok telemetry pin incomplete — see reviewers/grok.md "One-time setup"`. Failing closed here costs one reviewer. Failing open ships the reviewed diff, and the contents of anything grok reads, to xAI's trace channel.

This is a **presence** check, not a proof. It reads three lines out of a TOML file; it does not parse the file, does not know which table a key landed in, and cannot confirm the binary honoured it. A stronger check would have to watch the wire, which co-review is not going to do per dispatch. Read a pass as "setup was done", not as "nothing is uploading".

**Gate 2 — version.** Run bare `grok --version` and compare to the pinned **`1.0.38`**. On a mismatch, skip with the reason: `grok <version> is not validated — re-verify the telemetry pin and sandbox behaviour, then bump the pin in reviewers/grok.md`. The gate exists because `disable_codebase_upload` is undocumented (an update may rename or drop it), because the sandbox's failure mode is ambiguous (below), and because this tool's history is precisely that of behaviour changing underneath a stable-looking surface. It is the same argument as `crush`'s version gate, for stronger reasons.

> **The exact output spelling of `grok --version` is unverified here** — this file was written without the binary installed. Confirm the literal string when you do the one-time setup and, if it is not a bare `1.0.38`, fix the comparison and the allow-rule together. Everything in this file marked as verified is verified _by a cited third party_, not by this repository.

## Driving rules

- **Headless mode does not read stdin.** This is not the `codex`/`crush` pipe pattern — a piped `<INPUT>` is silently dropped and grok answers from the prompt argument alone. Use **`--prompt-file "<INPUT>"`**, which is `devin`'s shape, and note the consequence: `<INPUT>` appears **in the command string**, so it is part of the exact-match rule. ([headless reference][headless-ref])
- **`<INPUT>` must live inside `<NEUTRAL>`.** The `strict` sandbox profile confines reads to the working directory, so an `<INPUT>` anywhere else is unreadable to the very process that must read it. This is why grok's `mkdir -p "<NEUTRAL>"` runs **first** in the chain rather than just before dispatch.
- **`--sandbox strict`, not `read-only`.** Both block writes, but `read-only` permits reads **everywhere on the machine** while `strict` confines them to the working directory plus system paths. For most agents that difference is a nicety; for this one it is the control that bounds what can enter the trace channel at all. Pointed at an empty `<NEUTRAL>`, strict means the only project bytes grok can read are the ones co-review handed it. ([sandbox reference][sandbox-ref])
- **`--always-approve` is required, and the sandbox is what makes it safe.** Without it the first tool call in a headless run returns `User cancelled the execution` and the session ends with `stopReason=cancelled` before reading anything — a reviewer that reliably produces nothing. A third party verified on **1.0.25** and **1.0.30** that `--sandbox read-only` denies the `write` tool, `search_replace`, and shell redirects with `EPERM` while approval is auto-granted, and adopted exactly this pairing. Read the flag as "the kernel is the gate, not the prompt", which is the same posture `codex --sandbox read-only` takes. ([read-only enforcement test][readonly-test])
- **`--cwd "<NEUTRAL>"` is load-bearing, and more so here than for `crush` or `devin`.** grok discovers hooks, MCP servers, `AGENTS.md`-style instruction trees, and a project `.grok/` relative to the working directory. SlowMist found two zero-day chains reachable by planting project-level config — arbitrary code execution through a `cargo check` misclassification, and a permission bypass — so that opening a malicious project was the whole attack. Be precise about scope: SlowMist reports the first class also affected Claude Code, so this is a shared weakness in agent config discovery, not a grok-only defect. What is grok-specific is the response — xAI closed the report as out of scope for client-side `grok-build-cli` issues. Under `--post` the reviewed repo belongs to someone else, which is where this bites. ([SlowMist][slowmist-0day])
- **`<NEUTRAL>` must be grok's own directory.** It cannot be shared with `devin`'s, which must be empty, and grok's holds `<INPUT>`. It cannot be `crush`'s, which holds crush's config. Never the repo, `$HOME`, or the shared `<INPUT>` directory that other reviewers assemble into. The placeholder keeps the `<NEUTRAL>` spelling because that is the token [`../../../scripts/coreview-rule-drift.py`](../../../scripts/coreview-rule-drift.py) recognises and exempts from its off-machine check; the checker reads each reviewer's rules separately, so three reviewers may substitute three different literal paths into the same placeholder.
- **Always a fresh session.** Never add `-c`/`--continue`, `-r`/`--resume`, or `--fork-session`. A resumed session reviews a stale prior conversation.
- **Runs unsandboxed** in the Bash tool — it needs network for the xAI API.
- **Advisory-only, always reconciled**, like every external reviewer. Never let it be the sole reviewer.

> **The sandbox's failure mode is ambiguous, and the version gate is what covers it.** xAI's reference says a **built-in** profile that cannot be applied "logs a warning and continues without enforcement" — fail-open — while only a **custom** profile refuses to start. The third-party test reports that **1.0.30** refuses to start when protections cannot be applied. Those two statements disagree, and this repository has not run either. If you need the stronger guarantee rather than the pinned version's word, define a custom profile in `~/.grok/sandbox.toml` that extends `strict`: a custom profile fails closed by documented contract. Note that on macOS child-process **network** blocking is a documented no-op in every profile, so no profile confines grok's own egress — the telemetry pin is the only thing that does. ([sandbox reference][sandbox-ref])

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `mkdir -p "<NEUTRAL>" && cat "<this skill dir>/review_prompt.md" <CONVENTIONS> "<REQUESTS>" > "<INPUT>" && gh pr diff <n> --repo <owner>/<name> >> "<INPUT>" && [ -s "<INPUT>" ] && grok --cwd "<NEUTRAL>" --sandbox strict --always-approve --prompt-file "<INPUT>" --model "grok-4.6" 2>&1`
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `mkdir -p "<NEUTRAL>" && cat "<this skill dir>/review_prompt.md" <CONVENTIONS> > "<INPUT>" && gh pr diff <n> --repo <owner>/<name> >> "<INPUT>" && [ -s "<INPUT>" ] && grok --cwd "<NEUTRAL>" --sandbox strict --always-approve --prompt-file "<INPUT>" --model "grok-4.6" 2>&1`
- **`--local` mode** → swap the `gh pr diff …` segment for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md. Keep the `--prompt-file "<INPUT>"` tail — there is no `cat "<INPUT>" |` pipe for grok — and keep the trailing `2>&1`.

`<owner>/<name>` is the repo resolved in SKILL.md step 2 — never `cwd`'s by default, since a `--post` review commonly targets a PR that isn't checked out here.

There is **no `-p "<POINTER>"` argument**: `--prompt-file` carries the whole assembled `rubric + conventions + requests + diff`, and the rubric itself carries the read-only instruction, exactly as for `devin`. The `[ -s "<INPUT>" ] &&` empty-input guard is the same one `agy` and `devin` use — never hand an agentic reviewer an empty prompt file.

**The whole chain is `&&`, not `;`.** A failed `mkdir` would otherwise leave `<INPUT>` unwritable and dispatch grok against a stale or missing file, and a failed `gh pr diff` would produce a confident review of a rubric with no diff attached (the `[ -s ]` guard cannot catch that — the rubric is written first, so `<INPUT>` is non-empty either way). `&&` is in the matcher's splitter set, so the approve-once rules are unaffected.

`--model` stays last, the convention `agy` and `devin` follow. Order matters elsewhere in the line too: `gh pr diff` runs while the shell's cwd is still the repo, and `--cwd` retargets grok alone, not the shell — which is why grok needs no `cd` segment where `devin` does.

The trailing `2>&1` folds stderr into the captured output. grok exits `0` on success and `1` on an auth, network, or runtime error; which stream carries a given failure is **unverified here**, so take the redirection as precautionary and consistent with `copilot`, `crush`, and `devin` rather than as evidence about grok. The redirection is transparent to the permission matcher.

## Reading the result

Check the output for the rubric's terminal `REVIEW_COMPLETE: PASS` / `REVIEW_COMPLETE: FINDINGS` line — that, not the exit code, is what proves a review happened. Missing it means **incomplete, not PASS**: treat it as a skipped reviewer (noted, never fatal). See the "Long reviews" note in [`../SKILL.md`](../SKILL.md) for the backgrounding pattern.

Two failures worth naming before you reach for the pin or the config:

- **`stopReason=cancelled` with no findings** means `--always-approve` is missing from the dispatch, not that grok refused the review. See the driving rule above.
- **grok's context window is 500K**, the largest in the pool, so an oversized `<INPUT>` is a less likely diagnosis here than it is for `crush` (262K) or `devin` (200K). Whether grok truncates or fails closed on an over-window request is untested — so, as everywhere else, never read an oversize failure as a passing review. The missing `REVIEW_COMPLETE` line already settles that.

## Permission allow-rules (exact-match, approve once)

Merge into the `permissions.allow` array (see [`../references/permissions.md`](../references/permissions.md)). The first two are the pre-flight gates, the third prepares the neutral cwd, and the fourth is the reviewer command.

Replace `<NEUTRAL>` with a fixed absolute path — **grok's own**, so this `mkdir` rule is a third entry beside `devin`'s and `crush`'s rather than the same one — and `<INPUT>` with the fixed absolute path the invocation writes to, which must sit **inside** `<NEUTRAL>` (e.g. `$HOME/.claude/co-review-grok/in.grok`). **Spell the home-rooted part `$HOME/…` — required, not merely permitted** — and spell it identically in the rule and the invocation: `/Users/you/…` on one side and `$HOME` on the other do not match, and under `--non-interactive` a non-matching dispatch is denied silently. Why the literal `$HOME` is what matches: [`../references/permissions.md`](../references/permissions.md#home-rooted-paths).

```json
"Bash(grep -c -e \"trace_upload = false\" -e \"telemetry = false\" -e \"disable_codebase_upload = true\" \"$HOME/.grok/config.toml\")",
"Bash(grok --version)",
"Bash(mkdir -p \"<NEUTRAL>\")",
"Bash(grok --cwd \"<NEUTRAL>\" --sandbox strict --always-approve --prompt-file \"<INPUT>\" --model \"grok-4.6\")"
```

The `--prompt-file "<INPUT>"` path plus the full flag set, including the exact pinned `--model`, must match **byte-for-byte** between the command and the rule. **Do not wildcard at any flag.** Claude Code's Bash permission patterns match any suffix after the fixed prefix, not just the next token, so a wildcard approves everything beyond it — `--sandbox:*` would admit `off`, and a wildcard on any earlier flag swallows the sandbox and model pins along with it. If you change the model, the sandbox profile, or the neutral path, add a new exact-match rule rather than widening one.

**This file ships with the plugin,** so a plugin update overwrites your edits while your `settings.json` keeps whatever rules you put there. If you have pinned a different model or profile, re-apply the edit after an update and keep the shipped rule approved alongside your own — otherwise the command and the rule drift apart and grok drops out of every run silently.
