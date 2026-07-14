# Coder capability matrix

**Cached: 2026-07-13.** Benchmarks and pricing move fast. If this date is
older than ~2 months, or a model in play isn't listed, refresh before
recommending: web-search current SWE-bench Pro (Scale leaderboard),
Terminal-Bench (tbench.ai), and vendor pricing pages — `firecrawl` (CLI) or
WebFetch work for pages that block plain fetches — and update this file with
a new cache date. Prefer Scale and tbench.ai over aggregator sites; treat
vendor-self-reported numbers as upper bounds — including for OpenAI, whose own
model guidance and pricing pages are the only source for the GPT-5.6 tiers
below until those tiers land on Scale/tbench.ai. Where a row rests on vendor
guidance alone, say so in the row rather than letting it read like a
benchmarked number.

## Dimensions

| Dimension         | What it measures                                                         | Scope       |
| ----------------- | ------------------------------------------------------------------------ | ----------- |
| **Correctness**   | Multi-file coding accuracy (SWE-bench Pro, Terminal-Bench)               | per model   |
| **Speed**         | Wall-clock to a finished packet (model tok/s + harness overhead)         | per model   |
| **Cost**          | `$` cheap · `$$` mid · `$$$` frontier (per-token or quota draw)          | per model   |
| **Context**       | Usable context window — what fits in one packet without chunking         | per model   |
| **Creativity**    | Design taste, frontend/visual work, naming, API ergonomics               | per model   |
| **Autonomy**      | Long-horizon multi-step work without steering                            | per model   |
| **Verification**  | Does the coder honestly run/report checks in our harness?                | per backend |
| **Data handling** | Does your code train the vendor's models, and what else leaves the box?  | per backend |
| **Containment**   | Is the workspace boundary OS-enforced, prompt-enforced, or not enforced? | per backend |

**Scope is the point of that third column.** Data handling and containment are
properties of the **harness**, not the weights: every Claude model inherits
Claude Code's sandbox and Anthropic's commercial terms, every Gemini model
inherits Antigravity's ToS. Swapping `agy:Gemini 3.5 Flash` for
`agy:Gemini 3.1 Pro` changes the model's correctness and speed; it changes
nothing about who may train on your code. So those two live in one
cross-backend table below, while context — which really is per-model — sits in
the per-model tables.

**These two can veto a pick outright; the others trade off.** A backend that
trains on your code, or that can't be confined to the worktree you gave it, is
disqualified for the affected work no matter how it benchmarks. Score them
first, then rank what survives.

## Data handling and containment (per backend)

_Researched 2026-07-13, verified 2026-07-14 against each vendor's binding terms
and its own docs — cited inline. Every backend here except opus trains on your
code **by default**; what differs is whether an opt-out exists and who can
exercise it. Read the row, not the reputation._

| Backend | Trains on your code?                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | Other egress                                                                                                                                                                            | Containment                                                                                                                                                                                                                                                                                                                                                                                               |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| opus    | **No**, under commercial/API terms ("Anthropic may not train models on Customer Content"). Consumer Pro/Max only if you turn the data-improvement toggle **on** — opt-in. 30-day retention; ZDR is Enterprise-only.                                                                                                                                                                                                                                                                                                                                                                                                                                 | Telemetry + error reporting **on by default** (`DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`); no repo upload. Transcripts cached locally in plaintext under `~/.claude/projects`. | **OS-enforced** (Seatbelt / bubblewrap), but Bash-only — Read/Edit/Write go through the permission system instead. Writes default to the cwd subtree. Anthropic's own docs call it "not a complete isolation boundary"; sandboxed **reads** still span the machine (`~/.ssh`, `~/.aws`) unless denied.                                                                                                    |
| codex   | **Depends on the plan, and this is the trap.** API-billed: no training, [default since 2023][oai-data]. **Consumer ChatGPT plans (Free/Plus/Pro) inherit _ChatGPT_ data controls — training is ON by default** unless you opted out in Settings → Data Controls. ChatGPT **Business/Enterprise** are also ChatGPT plans but do **not** train on business data by default ([Codex admin FAQ][oai-codex]). So "ChatGPT-billed" alone doesn't settle it — the tier does.                                                                                                                                                                               | Anonymous usage/health telemetry **on by default** (`[analytics] enabled = false`). OTel export off by default. No repo indexing found.                                                 | **Strongest of the four.** OS-enforced: Seatbelt (macOS), bubblewrap + seccomp (Linux). `read-only` / `workspace-write` / `danger-full-access`; **network egress blocked by default**; `.git`, `.codex`, `.agents` stay read-only even under `workspace-write`. Researchers report bypasses exist — it's a boundary, not a vault.                                                                         |
| agy     | **Yes — by default, and a paid Google AI subscription does not exempt you.** Antigravity ships [its own ToS][agy-tos]: Interactions are used to "evaluate, develop, and improve" Google's products and ML, and Google "employees and contractors may access, view, review and use" them. The Gemini API's paid-tier no-training rule **does not carry over**. Only Gemini Enterprise / Workspace / Cloud access is exempt. **An opt-out exists** — the same ToS says to "navigate to settings to change your preference on how such data is used." No retention period is stated anywhere; deletion is by request (antigravity-support@google.com). | Telemetry default-on.                                                                                                                                                                   | **Effectively none for our dispatch.** A `--sandbox` flag exists, but it only imposes terminal restrictions — and [issue #36][agy-36] documents that `--dangerously-skip-permissions` auto-approves the bypass-sandbox prompt, making `--sandbox` inert. `--add-dir` adds workspace dirs; nothing pins a root. Our pilot's worktree escape is consistent with this; the precise mechanism is unconfirmed. |
| devin   | **Yes — by default.** [Platform ToS §3.3.1][cog-tos] and the [security docs][cog-sec] agree (they do not contradict each other): "we may use your data for model training purposes." **Opt-out on paid plans** via Data Controls, and opting out also enables ZDR with their model providers; on **Teams, only an administrator** can exercise it. **Enterprise customers are never trained on** without express written consent. No retention period published.                                                                                                                                                                                    | SOC 2 Type II, ISO 27001 claimed. Cloud handoff exists but is explicit/opt-in — the CLI itself is a local agent.                                                                        | **Real, and OS-level when asked for.** `accept-edits` confines edits to the workspace and prompts for anything outside it; `--sandbox` uses bubblewrap (network allowlist documented as unstable). `bypass` mode is documented as "lacks OS-level isolation." No escape reports found.                                                                                                                    |

**The passthrough hole.** Routing a packet through `devin:glm-5.2`,
`devin:deepseek-v4`, or `devin:kimi-k2.7` sends your code to a model served by
Zhipu, DeepSeek, or Moonshot. Cognition documents **nothing** about whether it
proxies those requests or you hit the origin labs' APIs under _their_ data
policies — and those labs are absent from Cognition's published subprocessor
list. That is an open question, not a cleared one: treat the open-weight
passthroughs as unsuitable for proprietary code until Cognition says otherwise.

**Practical upshot.** Only `opus` is no-training _by default_ on ordinary
commercial terms. `codex` is too when API-billed or on ChatGPT Business/
Enterprise. `agy` and `devin` both train by default and require you to have
**actually exercised** their opt-out — an opt-out that exists but that neither
CLI reports, so the skill cannot verify it for you. Unexercised, they are
disqualified for code that must not train a model. Separately, `agy` is the one
backend here that also cannot be confined to its worktree.

[oai-data]: https://platform.openai.com/docs/guides/your-data
[oai-codex]: https://developers.openai.com/codex/enterprise/work-admin-faq
[agy-tos]: https://antigravity.google/terms
[agy-36]: https://github.com/google-antigravity/antigravity-cli/issues/36
[cog-tos]: https://cognition.com/legal/platform-terms-of-service
[cog-sec]: https://docs.devin.ai/admin/security
[claude-data]: https://code.claude.com/docs/en/data-usage

## Models by backend

### opus (native Claude subagent — always available)

| Spec                     | $/Mtok in/out | Cost | Speed  | Context  | Correctness                                        | Best for                                                                                                                                  |
| ------------------------ | ------------- | ---- | ------ | -------- | -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| `opus:claude-opus-4-8`   | $5 / $25      | $$$  | medium | 1M       | SWE-bench Pro ~69% · Terminal-Bench 2.1 ~79%       | Architecture, long-horizon, hardest packets; honest self-verification                                                                     |
| `opus:claude-sonnet-5`   | $3 / $15      | $$   | fast   | 1M       | ~63% Pro — "~79% of Fable capability at 30% price" | Default PR-sized implementation; best cost/quality balance                                                                                |
| `opus:claude-sonnet-4-6` | $3 / $15      | $$   | fast   | 1M       | Previous-gen Sonnet; below Sonnet 5 on coding      | Fallback when Sonnet 5 is unavailable — note Sonnet 5's intro pricing ($2/$10 through 2026-08) currently undercuts it                     |
| `opus:claude-haiku-4-5`  | $1 / $5       | $    | fast   | **200K** | Modest; cheapest per solved task (~$0.13/point)    | Mechanical edits, renames, config churn, high-volume simple packets                                                                       |
| `opus:claude-fable-5`    | $10 / $50     | $$$  | slow   | 1M       | SWE-bench Pro ~80% · Terminal-Bench 2.1 88% (SOTA) | Hardest problems. Ask before selecting in most cases; may be picked without asking when the task is hard-but-small and confidence is high |

Context is GA 1M on every Claude model here **except Haiku (200K)** — the one to
watch when fanning Haiku out over large files. Max output 128K (Haiku 64K).

Claude models carry the strongest practitioner consensus on **creativity/design
taste** and convention-following; they are the default for frontend/visual
packets and for review-adjacent work.

### codex (OpenAI Codex CLI)

| Spec                  | $/Mtok in/out | Cost | Speed  | Context | Correctness / capability signal                                                                                     | Best for                                                                                        |
| --------------------- | ------------- | ---- | ------ | ------- | ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| `codex:gpt-5.6-sol`   | $5 / $30      | $$$  | medium | 1.05M*  | Official flagship/frontier GPT-5.6; Artificial Analysis (AA) places max-reasoning Sol just behind Fable 5           | Frontier agentic/terminal work, hard bugs, long-horizon Codex runs, cross-vendor second opinion |
| `codex:gpt-5.6-terra` | $2.50 / $15   | $$   | fast   | 1.05M*  | Official balanced GPT-5.6 tier and the `gpt-5.6` default; succeeds the workhorse tier (`gpt-5.4`) at the same price | Default Codex implementation when cost matters but task still needs strong reasoning            |
| `codex:gpt-5.6-luna`  | $1 / $6       | $    | fast   | 1.05M*  | Official cost-sensitive/high-volume GPT-5.6 tier; succeeds the cheap tier (`gpt-5.4-mini`)                          | Parallel packet fan-out, mechanical/bulk edits, single-file fixes, cheap second passes          |
| `codex:gpt-5.5`       | $5 / $30      | $$$  | slow   | 1.05M*  | Previous frontier; Terminal-Bench 2.1 ~83% (top harness) · Pro ~57–59%*                                             | Fallback if GPT-5.6 is unavailable                                                              |
| `codex:gpt-5.4`       | $2.50 / $15   | $$   | medium | 1.05M*  | Previous workhorse; SWE-bench Pro ~59% (tops Scale public set at cache time)                                        | Fallback if Terra is unavailable                                                                |
| `codex:gpt-5.4-mini`  | $0.75 / $4.50 | $    | fast   | 400K    | Previous cheap tier; Pro ~54% — strong for the price                                                                | Fallback if Luna is unavailable                                                                 |

*Those are the **API model-card** context windows. Multiple user reports say the
**Codex CLI serves a smaller window than the model card advertises** (figures
around 272–400K circulate for the 5.4/5.5 tiers, and an open issue claims Sol is
capped near 372K in-app). None of this is confirmed by OpenAI's own Codex docs —
so do **not** pick codex on the strength of a 1M window without measuring it
first. Treat these cells as an upper bound, unlike the Claude and Gemini rows,
which are the numbers actually served.

*OpenAI self-reports higher; independent harnesses score lower — treat
vendor numbers as upper bounds. The three GPT-5.6 rows carry no SWE-bench Pro
or Terminal-Bench score because none exists for them at this cache date: their
capability, speed, and tier ordering are OpenAI's own claims, not measured
here. Official guidance recommends `gpt-5.6` for most coding tasks and says
GPT-5.6 improves token efficiency and frontend aesthetics; re-check against
Scale/tbench.ai at the next refresh before trusting the ordering. Prefer the
benchmarked `gpt-5.5`/`gpt-5.4` rows when a routing decision turns on a
correctness margin. Luna costs more per token than the `gpt-5.4-mini` it
succeeds, but wins on correctness-per-packet at bulk volume — which is what
`mechanical-bulk` is actually optimizing. Codex CLI remains the most
**token-efficient** harness
(~4× fewer tokens than Claude Code in practitioner tests) and has the best
sandboxing; slightly weaker design taste than Claude.

### agy (Google Antigravity CLI — subscription quota, not per-token)

| Spec                          | Cost basis                    | Cost | Speed | Context | Correctness                                       | Best for                                                                                                |
| ----------------------------- | ----------------------------- | ---- | ----- | ------- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `agy:Gemini 3.5 Flash (High)` | quota (≈$1.50/$9 API-equiv)   | $    | fast  | 1M      | SWE-bench Verified ~79% · Terminal-Bench 2.1 ~76% | Fast agentic default; unusually strong for a "fast" model — beats its own Pro sibling on coding benches |
| `agy:Gemini 3.1 Pro (High)`   | quota (≈$2/$12; heavier draw) | $$   | slow  | 1M      | Verified ~81%, but loses to Flash on agentic work | Deliberate reasoning/debugging where thinking depth beats speed                                         |

Quota caveat: 5-hour refresh windows; background sub-agents burn quota
independently. Both models are 1,048,576-token context (Gemini 3.1 Pro is still
`-preview`, not GA).

**1M context is no longer agy's differentiator.** It was, when the Claude models
were 200K — it is why the `whole-codebase` row routed here. Claude is now 1M
across the board and GPT's model cards claim 1.05M, so a large context buys agy
nothing it doesn't have to win on other axes. Given its data-handling posture
(Google trains on Antigravity interactions by default, paid subscription
included) and its absent containment, **agy is no longer the default for
whole-codebase work** — see the routing table.

### devin (Cognition Devin CLI)

| Spec                 | Cost basis                | Cost | Speed                | Context      | Correctness                                                                                             | Best for                               |
| -------------------- | ------------------------- | ---- | -------------------- | ------------ | ------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `devin:swe-1.6`      | free w/ plan (0× credits) | $    | fast (~200 tok/s)    | undocumented | Upper-mid tier; Cognition publishes only relative deltas (+10% over 1.5) — absolute standing UNVERIFIED | Cheap scoped edits when cost dominates |
| `devin:swe-1.6-fast` | $0.30/$1.50 (pro, 0.5×)   | $    | fastest (~950 tok/s) | undocumented | Same weights as swe-1.6                                                                                 | Latency-critical iteration loops       |
| `devin:swe-1.6-slow` | free-tier pin             | $    | slow                 | undocumented | Same family                                                                                             | Only option on the free tier           |

Cognition publishes throughput for the swe-1.6 family but **no context window at
all** — for any of the three. Don't assume it matches the passthroughs; if a
packet's viability depends on the window, don't route it here.

Devin also exposes passthrough models at credit multipliers. For frontier
vendors (Opus/Sonnet/GPT/Gemini) that's usually worse economics than the
native backend — don't route through devin for them. It is the **only route here
to its open-weight passthroughs** — GLM-5.2 (open-weights leader, ~62% SWE-bench
Pro, 1M ctx), DeepSeek V4 (1M), Kimi K2.6/K2.7 (256K) — which on price alone
would suit cost-dominated bulk work.

**Read the passthrough hole above before using them.** Those models are served by
Zhipu, DeepSeek, and Moonshot, and Cognition documents nothing about whose
servers your code reaches or under what data policy. Cheap bulk work on a public
repo, fine. Proprietary code, no — not until that's answered.

## Routing table (task profile → ranked candidates)

The **`label`** column is the routing label emitted by the **assess-task**
skill (`skills/assess-task/SKILL.md`) — select-coder consumes that profile
rather than re-deriving one, so the label rows below key off `task_profile.label`.
The final `cross-vendor` row is a select-coder routing modifier, not an
assess-task label — apply it when the task's value is a second opinion.

*Rank order on `whole-codebase` is by the window **actually served**, not the one
advertised. agy is 2nd despite its gates because 1,048,576 is vendor-documented
and served; codex is 3rd despite a 1.05M model card because the CLI reportedly
serves 272–400K (see the codex table). If the data-handling gate removes agy, the
2nd slot is empty rather than filled by codex — say so instead of routing a
1M-token read to an unverified window.

**Two gates run before this table, and they can overrule every row in it:**

1. **Data handling.** Every backend except `opus` trains on your code by default;
   `agy` and `devin` offer an opt-out that neither CLI reports, so the skill
   cannot confirm it was exercised. If the repo holds code that must not train a
   vendor's model, the surviving candidates are `opus`, and `codex` **only when
   `availability.codex.auth` is `api-key`, or you confirm a ChatGPT
   Business/Enterprise workspace**. Drop `agy`, `devin`, and the devin
   open-weight passthroughs (whose destination is undocumented) unless the user
   states the opt-out is in force.
2. **Containment.** If packets run unattended or in parallel near the main
   checkout, drop `agy`: its workspace boundary is not enforceable, and
   `--sandbox` is inert under `--dangerously-skip-permissions`.

Rank what survives. A backend that fails a gate does not get ranked lower — it
gets removed, and the report names the gate that removed it.

**Non-interactive default (auto-pilot, orchestrate-coders):** the skill never
prompts, so it cannot ask whether the repo is sensitive or whether an opt-out was
exercised. Assume the **conservative** answer — repo is sensitive, opt-outs are
not in force — apply both gates, and state that assumption in the report. A caller
that knows better overrides it via `data_policy.repo_may_train: true` in
`.coders.yml`.

| `label`                  | Task profile                                    | 1st                                                              | 2nd                           | 3rd                           |
| ------------------------ | ----------------------------------------------- | ---------------------------------------------------------------- | ----------------------------- | ----------------------------- |
| `architecture`           | Architecture / multi-file refactor / hard bug   | `opus:claude-opus-4-8`                                           | `codex:gpt-5.6-sol`           | `agy:Gemini 3.1 Pro (High)`   |
| `standard-pr`            | Standard PR-sized feature or fix                | `opus:claude-sonnet-5`                                           | `codex:gpt-5.6-terra`         | `agy:Gemini 3.5 Flash (High)` |
| `mechanical-bulk`        | Mechanical / bulk / high-volume simple packets  | `codex:gpt-5.6-luna`                                             | `opus:claude-haiku-4-5`       | `devin:swe-1.6`               |
| `frontend-creative`      | Frontend / design / creative naming & API shape | `opus:claude-opus-4-8`                                           | `opus:claude-sonnet-5`        | `codex:gpt-5.6-sol`           |
| `latency-loop`           | Latency-critical tight loop                     | `devin:swe-1.6-fast`                                             | `agy:Gemini 3.5 Flash (High)` | `codex:gpt-5.6-luna`          |
| `whole-codebase`         | Whole-codebase context (1M-token reads)         | `opus:claude-sonnet-5`                                           | `agy:Gemini 3.5 Flash (High)` | `codex:gpt-5.6-terra`*        |
| `verification-sensitive` | Verification-sensitive (the check IS the task)  | `opus:claude-opus-4-8`                                           | `opus:claude-sonnet-5`        | — (avoid devin/codex-sandbox) |
| `long-horizon`           | Long-horizon autonomous (overnight-scale)       | `opus:claude-opus-4-8`                                           | `codex:gpt-5.6-sol`           | —                             |
| `cross-vendor`           | Cross-vendor diversity (2nd opinion / review)   | pick a different vendor than the 1st author — codex ↔ opus ↔ agy |                               |                               |

## Operational modifiers (from pilot runs — outrank benchmark deltas)

| Backend | Observed behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Selection impact                                                                                                                        |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| devin   | **Yes — by default.** [Platform ToS §3.3.1][cog-tos] and the [security docs][cog-sec] agree (they do not contradict each other): "we may use your data for model training purposes." **Opt-out on paid plans** via Data Controls, and opting out also enables ZDR with their model providers; on **Teams, only an administrator** can exercise it. **Enterprise customers are never trained on** without express written consent. No retention period published.                                                                                                                                                                                    | SOC 2 Type II, ISO 27001 claimed. Cloud handoff exists but is explicit/opt-in — the CLI itself is a local agent.                        |
| codex   | **Depends on the plan, and this is the trap.** API-billed: no training, [default since 2023][oai-data]. **Consumer ChatGPT plans (Free/Plus/Pro) inherit _ChatGPT_ data controls — training is ON by default** unless you opted out in Settings → Data Controls. ChatGPT **Business/Enterprise** are also ChatGPT plans but do **not** train on business data by default ([Codex admin FAQ][oai-codex]). So "ChatGPT-billed" alone doesn't settle it — the tier does.                                                                                                                                                                               | Anonymous usage/health telemetry **on by default** (`[analytics] enabled = false`). OTel export off by default. No repo indexing found. |
| codex   | **Depends on the plan, and this is the trap.** API-billed: no training, [default since 2023][oai-data]. **Consumer ChatGPT plans (Free/Plus/Pro) inherit _ChatGPT_ data controls — training is ON by default** unless you opted out in Settings → Data Controls. ChatGPT **Business/Enterprise** are also ChatGPT plans but do **not** train on business data by default ([Codex admin FAQ][oai-codex]). So "ChatGPT-billed" alone doesn't settle it — the tier does.                                                                                                                                                                               | Anonymous usage/health telemetry **on by default** (`[analytics] enabled = false`). OTel export off by default. No repo indexing found. |
| agy     | **Yes — by default, and a paid Google AI subscription does not exempt you.** Antigravity ships [its own ToS][agy-tos]: Interactions are used to "evaluate, develop, and improve" Google's products and ML, and Google "employees and contractors may access, view, review and use" them. The Gemini API's paid-tier no-training rule **does not carry over**. Only Gemini Enterprise / Workspace / Cloud access is exempt. **An opt-out exists** — the same ToS says to "navigate to settings to change your preference on how such data is used." No retention period is stated anywhere; deletion is by request (antigravity-support@google.com). | Telemetry default-on.                                                                                                                   |
| opus    | Self-verifies honestly, including flagging its own workarounds                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | Prefer for verification-sensitive and review-adjacent packets                                                                           |
