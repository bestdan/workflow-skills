# co-review reviewer — `grok` (xAI Grok Build CLI)

`grok` is a built-in default reviewer. It is xAI's official terminal coding agent ([`xai-org/grok-build`](https://github.com/xai-org/grok-build), Apache-2.0), pinned here to **`grok-4.6`** — so its voice is xAI's, distinct from Claude (the main agent), OpenAI (`codex`), Gemini (`agy`), Cognition (`devin`), GitHub-routed (`copilot`), and open-weight Kimi (`crush`). It is the seventh vendor lineage in the pool, which is the whole reason it earns a slot.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the gates and invocation below.

If `grok` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

> **Provenance.** The invocation, both gates and every claim marked _measured_ below were run against **grok 1.0.34 (3736acbc8658) [stable]**, macOS arm64, on **2026-09-20**, including a full review of this file's own diff that returned findings and a terminal `REVIEW_COMPLETE: FINDINGS`. The incident history in the next section is **not** ours — it is third-party wire captures on 0.2.93, cited individually. Where a source and a measurement disagree, this file says so and follows the measurement, which is the reason several statements here contradict xAI's own documentation.

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

**The good news, and it is the reason this reviewer is addable at all:** the kill switches are real, and the layer ordering was verified by a third party as **env > config > remote**. The toggle that failed was the _remote_ layer, and a local config pin beats it. Be exact about provenance, because an operator who pins only what xAI documents will omit the upload veto: `[features] telemetry`, `[telemetry] trace_upload` and `[cli] auto_update` **are** in xAI's configuration reference, while `[harness] disable_codebase_upload` **is not** — it is an undocumented key whose effect only the third-party harness establishes. ([config reference][config-ref], [hardening harness][hardening])

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

**Merge these into `~/.grok/config.toml`, table by table — do not paste the block wholesale.** A grok installed by its own installer already writes a `[cli]` table, and TOML rejects a duplicate table, so appending a second `[cli]` breaks the whole config rather than adding a key. Put `auto_update` inside whichever `[cli]` table is already there, and append the rest only if their tables are absent:

```toml
[features]
telemetry = false

[telemetry]
trace_upload = false

[harness]
disable_codebase_upload = true

# into the EXISTING [cli] table, if one is present:
# auto_update = false
```

Confirm the merge parsed before trusting it: `grok inspect` exits 0 and lists `User: ~/.grok/config.toml` under **Config Sources**. Gate 1 below only counts lines, so it cannot tell a parsed key from one stranded in a broken file.

What each one is for:

- **`[features] telemetry`** is the product-analytics master switch, and `[telemetry] trace_upload` inherits it when unset. Both are pinned explicitly so neither depends on the other's default. ([config reference][config-ref])
- **`[harness] disable_codebase_upload`** is the hard veto on whole-repo upload — the third-party harness confirmed it intercepts at the end of the pipeline even with the telemetry env vars forced on. **It does not appear in xAI's configuration reference**, so it is an undocumented key with no stability contract. That asymmetry is exactly why the version gate below exists. ([hardening harness][hardening])
- **`[cli] auto_update`** off is what makes the version gate meaningful. Auto-update is on by default, so without this the binary you validated is not the binary that runs next week.

**Why config and not the environment variables.** `GROK_TELEMETRY_ENABLED` and `GROK_TELEMETRY_TRACE_UPLOAD` are documented and sit one layer higher, but an env-var prefix changes the command string and so stops it matching the exact-match allow-rule — under `--non-interactive` that is a silent denial. Config is the layer that is both durable and invisible to the matcher. If you want the env layer too, set it in your shell profile rather than in the dispatch.

> **Known gap: cross-session memory.** `GROK_MEMORY=0` disables it and there is **no documented config key** for it, so the same env-prefix problem applies and this file does not pin it. The exposure is bounded — `--cwd` points grok at a directory that only ever holds co-review input — but it is not closed. Set `GROK_MEMORY=0` in your shell profile if you want it closed. This is the `agy` stale-conversation failure mode (see [`agy.md`](agy.md)), and the rubric's "do not retrieve any prior conversation" clause is the only thing standing against it here.

## Pre-flight gates — both are skips, never fatal

Run both before dispatching. Either one failing means **skip `grok`** (noted in the run summary, never fatal). Neither is an auth check.

> **There is a cheap auth probe if you want one, and it is not currently wired in.** `grok models` prints `You are logged in with grok.com`, the default model, and the servable roster, in about a second and with no inference — the same shape as `agy models`. It was not added as a third gate because grok's auth failure is caught from dispatch output like `copilot`'s and does not hang the way `agy`'s does, so a third gate would buy latency, not correctness. It is the right command to reach for by hand when a dispatch comes back empty, and it is also how to confirm the `--model` pin: on this account the roster is exactly `grok-4.6 (default)`, so the pin names the only model available.

**Gate 1 — the telemetry pin is actually in effect.** This is the one that matters. Run:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/grok-telemetry-gate.py"
```

**Exit 0** means pinned. **Exit 1** means a key is missing, wrong, in the wrong table, or overridden by the environment. **Exit 2** means the config is absent or unparseable. On anything but 0, **skip grok** and carry the script's one-line reason into the run summary. Failing closed costs one reviewer. Failing open ships the reviewed diff, and the contents of anything grok reads, to xAI's trace channel.

> **This started as a `grep` one-liner and had two fail-opens, both found by review.** The first counted **commented-out** lines, so `# trace_upload = false` satisfied it. Anchoring the pattern fixed that and left the second: a `grep` cannot tell which **table** an assignment landed in, so a real, uncommented `trace_upload = false` under the wrong table also satisfied it, while grok went on using its uploading default. Both are regression cases in [`../../../scripts/test-grok-telemetry-gate.sh`](../../../scripts/test-grok-telemetry-gate.sh). Parsing the file is what closes them, which is why this gate is a script and not a fenced one-liner.
>
> It also checks the **environment**, which outranks the file: the layer order is env > config > remote, so a truthy `GROK_TELEMETRY_ENABLED` or `GROK_TELEMETRY_TRACE_UPLOAD` in your shell profile re-enables uploads over a correct config. Set them to `0` or leave them unset.

It is still a **configuration** check, not a wire check. It proves the file says the right thing and nothing in the environment contradicts it; it cannot prove the binary honoured it. Only a proxy capture could, and co-review is not going to run one per dispatch. Read a pass as "this build is configured not to upload", not as "nothing is uploading".

**Gate 2 — version.** Run bare `grok --version` and compare its **version field** to the pinned **`1.0.34`**. On a mismatch, skip with the reason: `grok <version> is not validated — re-verify the telemetry pin and the tool allowlist, then bump the pin in reviewers/grok.md`. The gate exists because `disable_codebase_upload` is undocumented (an update may rename or drop it), because `--disallowed-tools` was measured failing open (see Driving rules), and because this tool's history is precisely that of behaviour changing underneath a stable-looking surface. It is the same argument as `crush`'s version gate, for stronger reasons.

> **Match the version field, not the whole line.** Unlike `crush`, whose entire `--version` line is stable, grok prints a build hash that moves independently of the version — measured output is `grok 1.0.34 (3736acbc8658) [stable]`. A byte-for-byte comparison against the full line would skip the reviewer after a rebuild of the same version, for no reason. Compare the second whitespace-separated field.

## Driving rules

- **A pipe is silently dropped, so never use the `codex`/`crush` shape.** grok reads stdin only when it is a **regular file**; a pipe is ignored without a word. Measured against 1.0.34 with one canary per run, the canary file kept outside grok's readable area so a hit could only have come through stdin: `< file` → the model saw it, `| pipe` → it did not, `< /dev/null` → it did not. So `cat "<INPUT>" | grok …` produces a **confident review of a rubric with no diff** — the exact failure the empty-input guard exists to prevent, arriving by a route the guard cannot see. Use **`--prompt-file "<INPUT>"`**, which is `devin`'s shape, and note the consequence: `<INPUT>` appears **in the command string**, so it is part of the exact-match rule. (xAI's headless reference states flatly that headless "does not read piped stdin"; that is right about the pipe and wrong about the redirect, which is why this bullet cites a measurement rather than the doc.)
- **`<INPUT>` must live inside `<NEUTRAL>`.** The `strict` sandbox profile confines reads to the working directory, so an `<INPUT>` anywhere else is unreadable to the very process that must read it. This is why grok's `mkdir -p "<NEUTRAL>"` runs **first** in the chain rather than just before dispatch.
- **`--sandbox strict`, not `read-only` — this is the single most load-bearing flag, and the difference is measured.** Both block writes outside the working directory. The difference is reads, and it is not subtle: with a canary planted in a checkout, `--sandbox read-only` **read it and printed it**, while `--sandbox strict` returned `READ-BLOCKED`, same prompt, same run of tests. For most reviewers that gap is a nicety; for this one it is the control that bounds what can enter xAI's trace channel at all, since the trace carries the contents of every file the agent reads. Pointed at `<NEUTRAL>`, strict means the only project bytes grok can reach are the ones co-review handed it. A write into the checkout under strict was likewise refused, with no file created. ([sandbox reference][sandbox-ref])
- **Strict is not a total read confinement — `/tmp` and `/var/tmp` stay readable.** The profile permits the working directory, system paths, `~/.grok`, and the temp directories. A first attempt at the test above planted its canary in `/tmp` and grok read it straight out, which looks like a failure of the profile and is not. Put a canary somewhere strict actually excludes, such as a checkout, or you will re-derive the wrong conclusion.
- **Pass `--permission-mode auto`, and do not pass `--always-approve`.** The tool layer here is not devin's: grok's `auto` permits **writes and shell execution** (both measured), so it is not a second containment layer and must not be described as one. It earns its place for a different reason — reliability. Under the **default** mode a tool call that would need approval cannot be answered headless, and the run dies at exit **1** with no verdict line and no findings, which co-review would record as a skipped reviewer. Measured: the identical write prompt completed under `auto` and killed the session under the default. So `auto` is what makes the reviewer finish; the sandbox is what makes finishing safe. `--always-approve` (the `--yolo` alias) is **not needed** — reads complete without it — and grants strictly more than `auto`, so it is dropped.
- **Pin the tool allowlist with `--tools read_file`; `--disallowed-tools` fails open.** Measured against 1.0.34: `--disallowed-tools run_terminal_command` left the shell tool fully working, and an invented tool name raised no error at all, so a typo in a denylist is indistinguishable from a working one. The positive form does work — `--tools read_file` turned the same shell probe into `SHELL-BLOCKED`. This is `crush`'s allowlist-versus-denylist lesson with the polarity reversed, and it is why the allowlist is in the command string rather than a denylist. A reviewer needs no tool beyond reading its own input, and under strict the only thing `read_file` can reach is `<NEUTRAL>` and the temp directories.
- **`--cwd "<NEUTRAL>"` is load-bearing, and more so here than for `crush` or `devin`.** grok discovers hooks, MCP servers, `AGENTS.md`-style instruction trees, and a project `.grok/` relative to the working directory. SlowMist found two zero-day chains reachable by planting project-level config — arbitrary code execution through a `cargo check` misclassification, and a permission bypass — so that opening a malicious project was the whole attack. Be precise about scope: SlowMist reports the first class also affected Claude Code, so this is a shared weakness in agent config discovery, not a grok-only defect. What is grok-specific is the response — xAI closed the report as out of scope for client-side `grok-build-cli` issues. Under `--post` the reviewed repo belongs to someone else, which is where this bites. ([SlowMist][slowmist-0day])
- **`<NEUTRAL>` must be grok's own directory.** It cannot be shared with `devin`'s, which must be empty, and grok's holds `<INPUT>`. It cannot be `crush`'s, which holds crush's config. Never the repo, `$HOME`, or the shared `<INPUT>` directory that other reviewers assemble into. The placeholder keeps the `<NEUTRAL>` spelling because that is the token [`../../../scripts/coreview-rule-drift.py`](../../../scripts/coreview-rule-drift.py) recognises and exempts from its off-machine check; the checker reads each reviewer's rules separately, so three reviewers may substitute three different literal paths into the same placeholder.
- **`--cwd` does not remove your _user-level_ agent config, and grok reads Claude Code's.** `grok inspect` on this machine lists the user's `~/.claude` skills, agents and plugins as discovered context, tagged `[claude]`. `--cwd` takes the _reviewed repo's_ config off the discovery path, which is the untrusted input this file is written against; it does nothing about your own global config, which only you can arm. This is the same residue `crush` documents for MCP servers, and the same disposition applies: documented, not gated on. A pre-flight that skipped grok whenever a user-level skill existed would remove the reviewer from every run on a normally configured machine.
- **Always a fresh session.** Never add `-c`/`--continue`, `-r`/`--resume`, or `--fork-session`. A resumed session reviews a stale prior conversation.
- **Runs unsandboxed** in the Bash tool — it needs network for the xAI API.
- **Advisory-only, always reconciled**, like every external reviewer. Never let it be the sole reviewer.

> **The sandbox fails closed on 1.0.34 — measured, settling a contradiction in the sources.** xAI's reference says a **built-in** profile that cannot be applied "logs a warning and continues without enforcement", and only a **custom** profile refuses to start. The third-party test reported the opposite for 1.0.30. The measurement here agrees with the test and not the doc: a dispatch naming an unappliable profile printed `error: could not apply the … sandbox profile … Refusing to start with its protections missing.` and exited **1**, with no model call. That is the behaviour to rely on for the pinned version only, which is part of what the version gate is for — the doc still describes fail-open, so a different build may do it.
>
> **This also means grok cannot run inside the Bash tool's sandbox.** Applying a profile needs to write `~/.grok/hooks`; sandboxed, that fails with `Operation not permitted` and grok refuses to start. So the dispatch runs unsandboxed for two reasons, network and its own home directory, exactly like `crush`.
>
> **No profile confines grok's own network egress on macOS** — child-process network blocking is a documented no-op there. The telemetry pin is the only thing standing between a read file and xAI's trace channel, which is why gate 1 is a skip rather than a warning.

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `mkdir -p "<NEUTRAL>" && cat "<this skill dir>/review_prompt.md" <CONVENTIONS> "<REQUESTS>" > "<INPUT>" && gh pr diff <n> --repo <owner>/<name> >> "<INPUT>" && [ -s "<INPUT>" ] && grok --cwd "<NEUTRAL>" --sandbox strict --permission-mode auto --tools read_file --prompt-file "<INPUT>" --model "grok-4.6" 2>&1`
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `mkdir -p "<NEUTRAL>" && cat "<this skill dir>/review_prompt.md" <CONVENTIONS> > "<INPUT>" && gh pr diff <n> --repo <owner>/<name> >> "<INPUT>" && [ -s "<INPUT>" ] && grok --cwd "<NEUTRAL>" --sandbox strict --permission-mode auto --tools read_file --prompt-file "<INPUT>" --model "grok-4.6" 2>&1`
- **`--local` mode** → swap the `gh pr diff …` segment for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md. Keep the `--prompt-file "<INPUT>"` tail — there is no `cat "<INPUT>" |` pipe for grok — and keep the trailing `2>&1`.

`<owner>/<name>` is the repo resolved in SKILL.md step 2 — never `cwd`'s by default, since a `--post` review commonly targets a PR that isn't checked out here.

There is **no `-p "<POINTER>"` argument**: `--prompt-file` carries the whole assembled `rubric + conventions + requests + diff`, and the rubric itself carries the read-only instruction, exactly as for `devin`. The `[ -s "<INPUT>" ] &&` empty-input guard is the same one `agy` and `devin` use — never hand an agentic reviewer an empty prompt file.

**The whole chain is `&&`, not `;`.** A failed `mkdir` would otherwise leave `<INPUT>` unwritable and dispatch grok against a stale or missing file, and a failed `gh pr diff` would produce a confident review of a rubric with no diff attached (the `[ -s ]` guard cannot catch that — the rubric is written first, so `<INPUT>` is non-empty either way). `&&` is in the matcher's splitter set, so the approve-once rules are unaffected.

`--model` stays last, the convention `agy` and `devin` follow. Order matters elsewhere in the line too: `gh pr diff` runs while the shell's cwd is still the repo, and `--cwd` retargets grok alone, not the shell — which is why grok needs no `cd` segment where `devin` does.

The trailing `2>&1` folds stderr into the captured output. grok exits `0` on success and `1` on an auth, network, or runtime error; which stream carries a given failure is **unverified here**, so take the redirection as precautionary and consistent with `copilot`, `crush`, and `devin` rather than as evidence about grok. The redirection is transparent to the permission matcher.

## Reading the result

Check the output for the rubric's terminal `REVIEW_COMPLETE: PASS` / `REVIEW_COMPLETE: FINDINGS` line — that, not the exit code, is what proves a review happened. Missing it means **incomplete, not PASS**: treat it as a skipped reviewer (noted, never fatal). See the "Long reviews" note in [`../SKILL.md`](../SKILL.md) for the backgrounding pattern.

> **Background it, and expect a large diff to hit the 15-minute bound.** grok buffers: `--output-format plain` writes **nothing** until the run ends, so a partially-complete review is indistinguishable from a hung one by byte count alone. Two measurements on the same machine and model: a **24KB** input (rubric plus a two-file diff) returned findings in a few minutes, while a **93KB** input (rubric, conventions and this PR's full diff) was still at **zero bytes past 25 minutes** — comfortably beyond the **15-min** CLI bound in [`../SKILL.md`](../SKILL.md), so `--non-interactive` would record it as timed-out. This is a latency limit, not the context window: 93KB is a fraction of grok's 500K. Treat grok as the slowest reviewer in the pool on a large PR, always dispatch it with `run_in_background: true`, and read a timeout as a skip rather than as a failure of the pin or the config.

Two failures worth naming before you reach for the pin or the config:

- **Exit 1 with narration but no verdict line** means `--permission-mode auto` is missing, not that grok refused the review: the model reached for a tool the default mode wanted approval for, and headless has no way to grant it. See the driving rule above.
- **grok's context window is 500K**, the largest in the pool, so an oversized `<INPUT>` is a less likely diagnosis here than it is for `crush` (262K) or `devin` (200K). Whether grok truncates or fails closed on an over-window request is untested — so, as everywhere else, never read an oversize failure as a passing review. The missing `REVIEW_COMPLETE` line already settles that.

## Permission allow-rules (exact-match, approve once)

Merge into the `permissions.allow` array (see [`../references/permissions.md`](../references/permissions.md)). The first is gate 2, the second prepares the neutral cwd, and the third is the reviewer command. **Gate 1 needs no rule of its own** — it is a `python3` script shipped by this plugin, so the shared `Bash(python3 <PLUGIN-CACHE>/…:*)` prefix in [`../references/permissions.md`](../references/permissions.md) already covers it, the same way it covers the allow-rule pre-flight. That is a side benefit of moving the gate out of a one-liner: one fewer exact-match rule to keep in sync.

Replace `<NEUTRAL>` with a fixed absolute path — **grok's own**, so this `mkdir` rule is a third entry beside `devin`'s and `crush`'s rather than the same one — and `<INPUT>` with the fixed absolute path the invocation writes to, which must sit **inside** `<NEUTRAL>` (e.g. `$HOME/.claude/co-review-grok/in.grok`). **Spell the home-rooted part `$HOME/…` — required, not merely permitted** — and spell it identically in the rule and the invocation: `/Users/you/…` on one side and `$HOME` on the other do not match, and under `--non-interactive` a non-matching dispatch is denied silently. Why the literal `$HOME` is what matches: [`../references/permissions.md`](../references/permissions.md#home-rooted-paths).

```json
"Bash(grok --version)",
"Bash(mkdir -p \"<NEUTRAL>\")",
"Bash(grok --cwd \"<NEUTRAL>\" --sandbox strict --permission-mode auto --tools read_file --prompt-file \"<INPUT>\" --model \"grok-4.6\")"
```

The `--prompt-file "<INPUT>"` path plus the full flag set, including the exact pinned `--model`, must match **byte-for-byte** between the command and the rule. **Do not wildcard at any flag.** Claude Code's Bash permission patterns match any suffix after the fixed prefix, not just the next token, so a wildcard approves everything beyond it — `--sandbox:*` would admit `off`, and a wildcard on any earlier flag swallows the sandbox and model pins along with it. If you change the model, the sandbox profile, or the neutral path, add a new exact-match rule rather than widening one.

**This file ships with the plugin,** so a plugin update overwrites your edits while your `settings.json` keeps whatever rules you put there. If you have pinned a different model or profile, re-apply the edit after an update and keep the shipped rule approved alongside your own — otherwise the command and the rule drift apart and grok drops out of every run silently.
