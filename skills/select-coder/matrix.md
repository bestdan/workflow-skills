# Coder capability matrix

**Cached: 2026-07-28.** Benchmarks and pricing move fast. If this date is
older than ~2 months, or a model in play isn't listed, refresh before
recommending: web-search current SWE-bench Pro (Scale leaderboard),
Terminal-Bench (tbench.ai), and vendor pricing pages — `firecrawl` (CLI) or
WebFetch work for pages that block plain fetches — and update this file with
a new cache date.

**Where the numbers here come from, and how much to trust them.** Two sources
now disagree in a way that matters:

- **Terminal-Bench 2.1 (tbench.ai)** is the trustworthy column. It is
  harness-explicit — every row names the agent _and_ the model, which is the
  only honest way to score a coder backend. Prefer it when a routing decision
  turns on a correctness margin.
- **SWE-bench Pro** is not what it was. Scale's public leaderboard has gone
  **stale** — at this cache date it still tops out at `gpt-5.4 (xHigh)` at
  59.1% and lists nothing from the Claude 5, GPT-5.6, or open-weights-2026
  generations. The Pro numbers below therefore come from **aggregators**, not
  Scale. Treat them as directional only: the frontier rows cluster inside ~1
  point, roughly a third of the public split is reported broken, and
  aggregators don't pin the harness. **Never break a tie on a SWE-bench Pro
  delta alone.**

Vendor-self-reported numbers are upper bounds — the Kimi K3 spread below
(88.3 vendor / 85 independent / 80.9 third-party, same model, same benchmark)
is the cleanest illustration in the table. Where a row rests on vendor
guidance alone, it says so rather than reading like a benchmarked number.

## Dimensions

| Dimension           | What it measures                                                         | Scope       |
| ------------------- | ------------------------------------------------------------------------ | ----------- |
| **Correctness**     | Multi-file coding accuracy (SWE-bench Pro, Terminal-Bench)               | per model   |
| **Speed**           | Wall-clock to a finished packet (model tok/s + harness overhead)         | per model   |
| **Cost**            | `$` cheap · `$$` mid · `$$$` frontier (per-token or quota draw)          | per model   |
| **Context**         | Usable context window — what fits in one packet without chunking         | per model   |
| **Creativity**      | Design taste, frontend/visual work, naming, API ergonomics               | per model   |
| **Autonomy**        | Long-horizon multi-step work without steering                            | per model   |
| **Verification**    | Does the coder honestly run/report checks in our harness?                | per backend |
| **Secret exposure** | If the agent reads a secret or PII, what happens to it?                  | per backend |
| **Containment**     | Is the workspace boundary OS-enforced, prompt-enforced, or not enforced? | per backend |

**Scope is the point of that third column.** Secret exposure and containment are
properties of the **harness**, not the weights: every Claude model inherits
Claude Code's sandbox, every Gemini model inherits Antigravity's ToS. Swapping
`agy:Gemini 3.6 Flash` for `agy:Gemini 3.1 Pro` changes the model's correctness
and speed; it changes nothing about who may read your `.env`. So those two live
in one cross-backend table below, while context — genuinely per-model — sits in
the per-model tables.

**The one place that scoping rule now strains is `devin`.** Devin has become a
model marketplace rather than a model vendor (37 families at this cache date,
spanning Anthropic, OpenAI, Google, xAI, Moonshot, Zhipu, DeepSeek, NVIDIA, and
Thinking Machines). Its harness properties are still uniform — but "who
ultimately serves this request" now varies _per model_ inside one backend, and
Cognition documents that for none of them. See the passthrough hole below.

**Assume the agent will read a secret eventually.** Not because it's malicious —
because `.env` files, fixtures with real PII, and hardcoded keys exist in real
repos, and an agent grepping for a config value will find them. The question this
dimension answers is not "will the vendor behave" but **"when that happens, how
bad is it"**: who sees it, how long it persists, and whether it becomes permanent.

**Training is a modifier here, not the trigger.** Being trained on is an IP
question, and reasonable people don't care much. It matters _for secrets_ because
it's the difference between a secret being transiently processed and being
durably absorbed into weights. Read the training column as "how permanent is the
leak," not "who owns my code."

**These two can veto a pick; the others trade off.** Score them first, then rank
what survives.

## Secret exposure and containment (per backend)

_Researched 2026-07-13, re-verified 2026-07-28 against each vendor's binding
terms and its own docs — cited inline. Nothing in this section improved enough
to move a gate; one Devin nuance changed and is flagged below._

**Read this first: the control that actually works is upstream of the vendor.**
No backend here promises not to _process_ a secret the agent hands it — they all
do, because that's what inference is. The reliable protection is that the agent
never reads the secret: keep `.env` and real-PII fixtures out of coder worktrees,
and set deny-read rules for `~/.ssh`, `~/.aws`, and `~/.config` on every backend.
This matters most where you'd least expect it — Claude Code's sandbox permits
**reads across the whole machine** by default, credential files included, unless
you deny them ([sandboxing docs][claude-sandbox]). Vendor choice is the second
line of defense, not the first.

| Backend | Who sees a secret the agent reads?                                                                                                                                                                                                                                                                                                                                    | Persistence / durability                                                                                                                                                                                                                                                                                                                                                                                                                     | Known secret-leak vectors                                                                                                                                                                                                                                              | Containment                                                                                                                                                                                                                                                                                                                                                 |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| opus    | Anthropic's inference path only. No human review of your content; no repo upload. Telemetry and error reporting are on by default but **carry no code or file paths** (`DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`). Note `/feedback` **does** send the transcript — don't run it after a secret was read.                                                     | **Lowest.** 30-day retention on commercial/API terms; no training on Customer Content, so a read secret stays transient. ZDR available (Enterprise). Local risk instead: transcripts sit in **plaintext** under `~/.claude/projects`.                                                                                                                                                                                                        | CVEs, all patched: a directory-escape (CVE-2025-54794) and config-injection chains that exfiltrated API keys via a malicious `ANTHROPIC_BASE_URL` in a repo's settings ([Check Point][cp-cc]). Prompt injection via PR comments demonstrated.                          | **OS-enforced** (Seatbelt / bubblewrap) but **Bash-only** — Read/Edit/Write go through the permission system instead. Writes default to the cwd subtree. Anthropic's own docs call it "not a complete isolation boundary," and sandboxed **reads span the whole machine** (`~/.ssh`, `~/.aws`) unless denied — the single most important thing to fix here. |
| codex   | OpenAI's inference path only. No human review, no repo indexing found. Telemetry is anonymous usage/health data — **no code** (`[analytics] enabled = false`).                                                                                                                                                                                                        | **Depends on the plan.** API-billed or ChatGPT Business/Enterprise: no training, so transient ([data policy][oai-data], [Codex admin FAQ][oai-codex]). Consumer ChatGPT (Free/Plus/Pro): training is ON by default — a read secret becomes **durable** unless you opted out in Data Controls. `probe-coders.sh` reports which via `codex.auth`.                                                                                              | CVE-2025-61260: command injection via crafted GitHub branch names during cloud tasks, used to retrieve GitHub tokens; patched. Researchers report sandbox bypasses exist.                                                                                              | **Strongest of the four.** OS-enforced: Seatbelt (macOS), bubblewrap + seccomp (Linux). **Network egress blocked by default** — the single best structural defense against exfiltration here. `.git`, `.codex`, `.agents` stay read-only even under `workspace-write`.                                                                                      |
| agy     | **Google employees and contractors.** Its [own ToS][agy-tos] states they "may access, view, review and use Interactions" — so a `.env` the agent reads is human-reviewable content, not just model input. This is the concrete secrets problem with agy, and a paid Google AI subscription does **not** exempt you (only Gemini Enterprise / Workspace / Cloud does). | **High, and unbounded.** Interactions feed training by default; **no retention period is stated anywhere**; deletion is by emailed request. An opt-out exists in settings — but the CLI never reports whether it's in force, so the skill cannot confirm it.                                                                                                                                                                                 | No agy-specific incident found. Its predecessor Gemini CLI had a confirmed exfiltration vuln (prompt injection + weak command allowlisting, [Tracebit][tracebit]) and a CVSS-10 RCE that ran **before the sandbox initialized**.                                       | **Effectively none for our dispatch.** `--sandbox` imposes only terminal restrictions, and [issue #36][agy-36] documents that `--dangerously-skip-permissions` auto-approves the bypass-sandbox prompt, making it inert. Nothing pins a workspace root. Our pilot's worktree escape is consistent with this.                                                |
| devin   | Cognition, plus **whoever serves the model you picked** — see the passthrough hole below, which is now the whole backend, not a corner of it. The CLI is a local agent; the cloud handoff is explicit/opt-in.                                                                                                                                                         | Trains on Customer Data **by default** ([ToS §3.3.1][cog-tos], [security docs][cog-sec] — they agree). **New since the last refresh:** opting out on a paid plan (admin-only on Teams) is documented to also enable **Zero Data Retention with Cognition's model providers** — a real improvement in _durability_, though still unverifiable by the CLI. Enterprise never trained on without written consent. No retention period published. | **The worst record here for your threat model.** Documented indirect prompt-injection attacks leaking **secrets and env vars** out of Devin via its shell and browser tools ([Embrace The Red][etr]) — reported Apr 2025, reportedly still unresolved 120+ days later. | **Real, and OS-level when asked for.** `accept-edits` confines edits to the workspace and prompts for anything outside it; `--sandbox` uses bubblewrap (network allowlist documented unstable). `bypass` mode "lacks OS-level isolation." No escape reports found.                                                                                          |

**The passthrough hole — now the defining fact about `devin`.** Devin serves 37
model families from at least nine distinct labs. Cognition's own [privacy
policy][cog-privacy] refers only to generic "Third Party Service Providers" and
**names not a single model vendor**; its [security docs][cog-sec] likewise
promise ZDR "with our model providers" without saying who they are. So for
_every_ Devin model — not just the China-lab ones — you cannot answer "whose
servers saw my code, under which jurisdiction." The exposure is starkest for
`glm-5.2` (Zhipu), `kimi-k3` / `kimi-k2.x` (Moonshot), and `deepseek-v4`
(DeepSeek), where the plausible answer is a jurisdiction you did not choose. But
note the same gap applies to routing `claude-opus-5` or `gpt-5.6-sol` through
Devin — you'd be adding an undocumented hop in front of a vendor you could
otherwise reach directly. **That is an open question, not a cleared one.**

**Practical upshot.** Only `opus` is no-training _by default_ on ordinary
commercial terms. `codex` is too when API-billed or on ChatGPT Business/
Enterprise. `agy` and `devin` both train by default, and their opt-outs — which
exist but which neither CLI reports — can't be verified by the skill. That
governs how _durable_ a leak is, not whether the gate fires: gate 1 drops both on
exposure grounds regardless (agy for human review, devin for its exfiltration
record and the unanswerable routing question). Separately, `agy` is the one
backend here that also cannot be confined to its worktree.

[oai-data]: https://platform.openai.com/docs/guides/your-data
[oai-codex]: https://developers.openai.com/codex/enterprise/work-admin-faq
[agy-tos]: https://antigravity.google/terms
[agy-36]: https://github.com/google-antigravity/antigravity-cli/issues/36
[cog-tos]: https://cognition.com/legal/platform-terms-of-service
[cog-sec]: https://docs.devin.ai/admin/security
[cog-privacy]: https://cognition.com/privacy-policy
[claude-data]: https://code.claude.com/docs/en/data-usage
[claude-sandbox]: https://code.claude.com/docs/en/sandboxing
[cp-cc]: https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/
[tracebit]: https://tracebit.com/blog/code-exec-deception-gemini-ai-cli-hijack
[etr]: https://embracethered.com/blog/posts/2025/devin-can-leak-your-secrets/
[tbench]: https://www.tbench.ai/leaderboard/terminal-bench/2.1
[scale-pro]: https://labs.scale.com/leaderboard/swe_bench_pro_public

## Models by backend

### opus (native Claude subagent — always available)

| Spec                    | $/Mtok in/out                            | Cost | Speed  | Context  | Correctness                                               | Best for                                                                                                                                                 |
| ----------------------- | ---------------------------------------- | ---- | ------ | -------- | --------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `opus:claude-opus-5`    | $5 / $25                                 | $$$  | medium | 1M       | SWE-bench Pro ~79%* · Terminal-Bench 2.1 ~85%†            | **New default for hard packets.** Architecture, long-horizon, agentic coding; honest self-verification                                                   |
| `opus:claude-fable-5`   | $10 / $50                                | $$$  | slow   | 1M       | SWE-bench Pro ~80%* · Terminal-Bench 2.1 **83.8% (SOTA)** | The hardest problems, and the only row here whose Terminal-Bench number is on the official board. Ask before selecting unless the task is hard-but-small |
| `opus:claude-sonnet-5`  | $2 / $10 intro ($3/$15 after 2026-08-31) | $$   | fast   | 1M       | SWE-bench Pro ~63%* · Terminal-Bench 2.1 74.6%            | Default PR-sized implementation; best cost/quality balance                                                                                               |
| `opus:claude-opus-4-8`  | $5 / $25                                 | $$$  | medium | 1M       | SWE-bench Pro ~69%* · Terminal-Bench 2.1 78.9%            | Previous-gen Opus. Fallback only — Opus 5 dominates it at identical price                                                                                |
| `opus:claude-haiku-4-5` | $1 / $5                                  | $    | fast   | **200K** | Modest; cheapest per solved task                          | Mechanical edits, renames, config churn, high-volume simple packets                                                                                      |

**Opus 5 is the headline change since the last refresh** — it lands at Opus 4.8's
exact price ($5/$25) with a ~10-point SWE-bench Pro jump and Terminal-Bench in
Fable's neighbourhood. There is no reason to route a new packet to Opus 4.8; it
survives in the table only as a fallback if Opus 5 is unavailable in the session.

*SWE-bench Pro figures are **aggregator** numbers — Scale's public board doesn't
list any of these models at this cache date. See the sourcing note at the top.

†Opus 5 is **not on the official tbench.ai board yet**. The ~85% is an
independent harness run; a vendor max-effort figure of ~89% also circulates.
Fable 5's 83.8% is the highest number on the _official_ board and is the one to
cite when the claim has to hold up.

Context is GA 1M on every Claude model here **except Haiku (200K)** — the one to
watch when fanning Haiku out over large files. Max output 128K (Haiku 64K).

Claude models carry the strongest practitioner consensus on **creativity/design
taste** and convention-following; they are the default for frontend/visual
packets and for review-adjacent work.

### codex (OpenAI Codex CLI)

| Spec                  | $/Mtok in/out | Cost | Speed  | Context | Correctness                                                                                 | Best for                                                                                           |
| --------------------- | ------------- | ---- | ------ | ------- | ------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `codex:gpt-5.6-sol`   | $5 / $30      | $$$  | medium | 1.05M*  | SWE-bench Pro ~65%; **no official Terminal-Bench entry** — its tiers below outrank it there | Frontier agentic/terminal work, hard bugs, long-horizon Codex runs, cross-vendor second opinion    |
| `codex:gpt-5.6-terra` | $2.50 / $15   | $$   | fast   | 1.05M*  | SWE-bench Pro ~63% · **Terminal-Bench 2.1 78.4%** — best benchmarked GPT-5.6 tier           | Default Codex implementation when cost matters but the task still needs strong reasoning           |
| `codex:gpt-5.6-luna`  | $1 / $6       | $    | fast   | 1.05M*  | SWE-bench Pro ~63% · Terminal-Bench 2.1 75.7% — remarkable for the price                    | Parallel packet fan-out, mechanical/bulk edits, single-file fixes, cheap second passes             |
| `codex:gpt-5.5`       | $5 / $30      | $$$  | slow   | 1.05M*  | **Terminal-Bench 2.1 83.1% — #2 on the official board**, behind only Fable 5                | Still the strongest _benchmarked_ Codex option. Prefer over Sol when the margin must be defensible |
| `codex:gpt-5.4`       | $2.50 / $15   | $$   | medium | 272K    | SWE-bench Pro 59.1% (tops Scale's public set, which is now stale)                           | Fallback if Terra is unavailable                                                                   |
| `codex:gpt-5.4-mini`  | $0.75 / $4.50 | $    | fast   | 400K    | Previous cheap tier                                                                         | Fallback if Luna is unavailable                                                                    |

*Those are the **API model-card** context windows. Multiple user reports say the
**Codex CLI serves a smaller window than the model card advertises** (figures
around 272–400K circulate, and an open issue claims Sol is capped near 372K
in-app). Unconfirmed by OpenAI's own Codex docs and **not re-verified at this
refresh** — so do **not** pick codex on the strength of a 1M window without
measuring it first. Treat these cells as an upper bound, unlike the Claude and
Gemini rows, which are the numbers actually served.

**The Sol-vs-5.5 inversion is worth internalising.** OpenAI positions Sol as the
flagship, and it does lead on SWE-bench Pro — but on the harness-explicit
Terminal-Bench board, `gpt-5.5` (83.1%) and even `gpt-5.6-terra` (78.4%) are
measured and Sol is absent. When a routing decision turns on a defensible
correctness margin, prefer `gpt-5.5`; when it turns on cost-per-solved-packet at
volume, prefer Luna.

Codex CLI remains the most **token-efficient** harness (~4× fewer tokens than
Claude Code in practitioner tests) and has the best sandboxing; slightly weaker
design taste than Claude.

### agy (Google Antigravity CLI — subscription quota, not per-token)

| Spec                          | Cost basis                     | Cost | Speed | Context | Correctness                                                              | Best for                                                                          |
| ----------------------------- | ------------------------------ | ---- | ----- | ------- | ------------------------------------------------------------------------ | --------------------------------------------------------------------------------- |
| `agy:Gemini 3.6 Flash (High)` | quota (≈$1.50/$7.50 API-equiv) | $    | fast  | 1M      | SWE-bench Pro 58.7% · Terminal-Bench 2.1 78.0%‡                          | **New — supersedes 3.5 Flash on every published benchmark.** Fast agentic default |
| `agy:Gemini 3.5 Flash (High)` | quota (≈$1.50/$9 API-equiv)    | $    | fast  | 1M      | SWE-bench Pro 55.1%                                                      | Fallback if 3.6 is unavailable                                                    |
| `agy:Gemini 3.1 Pro (High)`   | quota (≈$2/$12; heavier draw)  | $$   | slow  | 1M      | Terminal-Bench 2.1 65.8% — **loses to both Flash tiers on agentic work** | Deliberate reasoning/debugging where thinking depth beats speed                   |
| `agy:gpt-oss-120b (Medium)`   | quota                          | $    | fast  | —       | Unbenchmarked here                                                       | Not recommended; present in the CLI, no data to justify routing to it             |

‡Gemini 3.6 Flash's Terminal-Bench figure is **Google's own published number**,
not an entry on the tbench.ai board — an upper bound. Google also claims ~31%
lower effective cost per completed task from token-efficiency gains (up to ~71%
on agentic coding), which is a vendor claim we have not verified.

Quota caveat: 5-hour refresh windows; background sub-agents burn quota
independently. All Gemini tiers are 1,048,576-token context.

**1M context is not agy's differentiator.** It was, when the Claude models were
200K — it is why the `whole-codebase` row once routed here. Claude is now 1M
across the board and GPT's model cards claim 1.05M, so a large context buys agy
nothing it doesn't have to win on other axes. Given its secret-exposure posture
(Google staff may read the Interactions the agent sends, paid subscription
included) and its absent containment, **agy is not the default for
whole-codebase work.** It remains a fine pick on a repo with no live secrets,
which is most of them — and Gemini 3.6 Flash makes it a genuinely strong
cheap-and-fast option on those repos.

### devin (Cognition Devin CLI) — **now a model marketplace, not a model vendor**

This is the largest change in this refresh. Devin used to expose its own
`swe-1.x` family plus a handful of passthroughs. At this cache date `devin models
list` reports **37 model families** spanning Anthropic, OpenAI, Google, xAI,
Moonshot, Zhipu, DeepSeek, NVIDIA, and Thinking Machines — with explicit per-token
pricing rather than credit multipliers, and frontier models at or near their
native list price.

**Native Cognition models:**

| Spec                      | $/Mtok in/out  | Cost | Speed                | Context | Correctness                                                                   | Best for                                       |
| ------------------------- | -------------- | ---- | -------------------- | ------- | ----------------------------------------------------------------------------- | ---------------------------------------------- |
| `devin:swe-1.7`           | **free** (Pro) | $    | fast                 | 262K    | Unbenchmarked publicly; Cognition publishes only relative deltas — UNVERIFIED | Cheap scoped edits when cost dominates         |
| `devin:swe-1.7-lightning` | $2.50 / $12.50 | $$   | fastest              | 203K    | Same family                                                                   | Latency-critical iteration loops               |
| `devin:swe-1.6`           | **free**       | $    | fast (~200 tok/s)    | 200K    | Upper-mid tier, UNVERIFIED                                                    | Fallback                                       |
| `devin:swe-1.6-fast`      | $0.30 / $1.50  | $    | fastest (~950 tok/s) | 200K    | Same weights as swe-1.6                                                       | Fallback latency loop                          |
| `devin:adaptive`          | $0.50 / $2     | $    | —                    | —       | Router, not a model — picks a tier per request. Opaque; unbenchmarked         | Not recommended when you need to know what ran |

The swe-1.7 family is **beta** at this cache date and, like its predecessors,
carries **no published absolute benchmark standing**. The free tiers are
genuinely free on Pro, which makes them tempting for bulk work — but "free and
unmeasured" is exactly the combination that produces silent quality regressions
in a fan-out.

**Passthrough models — the ones worth knowing about:**

| Spec                     | $/Mtok in/out                                | Context    | Correctness                                                                                 | Notes                                                                                                                                                                     |
| ------------------------ | -------------------------------------------- | ---------- | ------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `devin:kimi-k3`          | $3 / $15                                     | 1M         | SWE-bench Verified 76.8% · Terminal-Bench 2.1 **80.9% independent / 85% AA / 88.3% vendor** | Moonshot's 2.8T MoE, released 2026-07-16. **The open-weights leader** and the strongest non-frontier option here — but read that three-way spread before quoting a number |
| `devin:glm-5.2`          | **free at 200K**; $0.70 / $2.20 for Max / 1M | 200K or 1M | SWE-bench Pro 62.1%                                                                         | Zhipu. Astonishing price/performance — Pro-tier free at 200K, and ~62% Pro is Sonnet-5-adjacent. Terminal-Bench data is for the older GLM-5.1 (58.7%)                     |
| `devin:grok-4.5`         | $2 / $6                                      | 500K       | SWE-bench Pro 64.7% · **Terminal-Bench 2.1 79.3% (Cursor CLI)**                             | xAI. Genuinely strong and cheap; the only non-Anthropic/OpenAI model in the official board's top 5                                                                        |
| `devin:inkling`          | $1.40 / $4.40                                | 1M         | SWE-bench Verified 77.6%                                                                    | Thinking Machines Lab, Apache 2.0, US-origin. 975B MoE, natively multimodal. Cheapest 1M-context option with a real score                                                 |
| `devin:deepseek-v4-pro`  | $1.74 / $3.48                                | 1M         | SWE-bench Pro ~55%                                                                          | DeepSeek. Cheapest output tokens at 1M context                                                                                                                            |
| `devin:nemotron-3-ultra` | $0.60 / $2.40                                | 262K       | Unbenchmarked here                                                                          | NVIDIA                                                                                                                                                                    |
| `devin:kimi-k2.6/2.7`    | $0.95 / $4                                   | 262K       | Superseded by K3                                                                            | Keep only as a cheaper Moonshot fallback                                                                                                                                  |

Devin also passes through `claude-opus-5`, `claude-fable-5`, `claude-sonnet-5`,
`gpt-5.6-sol/terra/luna`, and `gemini-3.6-flash` at prices at or near native
list. **Don't route those through Devin.** The economics are no longer the
argument they used to be — the argument now is that doing so inserts an
undocumented intermediary in front of a vendor you can reach directly, for zero
capability gain. Use the native backend.

**Read the passthrough hole above before using any of these.** The models in the
second table are served by Moonshot, Zhipu, xAI, DeepSeek, NVIDIA, and Thinking
Machines, and Cognition documents whose servers your code reaches for **none** of
them. Cheap bulk work on a public repo, fine. Proprietary code, no — not until
that's answered.

## Routing table (task profile → ranked candidates)

The **`label`** column is the routing label emitted by the **assess-task**
skill (`skills/assess-task/SKILL.md`) — select-coder consumes that profile
rather than re-deriving one, so the label rows below key off `task_profile.label`.
The final `cross-vendor` row is a select-coder routing modifier, not an
assess-task label — apply it when the task's value is a second opinion.

*Rank order on `whole-codebase` is by the window **actually served**, not the one
advertised. agy is 2nd despite its gates because 1,048,576 is vendor-documented
and served; codex is 3rd despite a 1.05M model card because the CLI reportedly
serves 272–400K (see the codex table). If the secret-exposure gate removes agy, the
2nd slot is empty rather than filled by codex — say so instead of routing a
1M-token read to an unverified window.

**Two gates run before this table, and they can overrule every row in it:**

1. **Secret exposure.** Ask one question: _could the agent read a live secret or
   real PII in this repo?_ (`.env` present, credential files, fixtures with real
   user data, a CI token in scope.) If yes:
   - Drop **`agy`** — Google staff may read what the agent read.
   - Drop **`devin`** — demonstrated prompt-injection secret exfiltration, and
     Cognition names no model provider for any of its 37 families, so "who saw
     it" is unanswerable for every model on the backend, not just the
     open-weights ones.
   - Keep `opus` and `codex`. Prefer `codex` when the packet has no reason to
     touch the network (egress is blocked by default there), and prefer
     `codex.auth: api-key` over a consumer ChatGPT login, which trains by default
     and so makes any leak durable.
   - **Whichever survives, deny reads of `~/.ssh`, `~/.aws`, and `~/.config`, and
     keep `.env` out of the coder's worktree.** The gate picks the safer vendor;
     this is what actually prevents the leak.
     If the repo is clean of secrets, this gate does not fire — training alone is
     not a disqualifier. Say so rather than silently filtering.
2. **Containment.** If packets run unattended or in parallel near the main
   checkout, drop `agy`: its workspace boundary is not enforceable, and
   `--sandbox` is inert under `--dangerously-skip-permissions`.

Rank what survives. A backend that fails a gate does not get ranked lower — it
gets removed, and the report names the gate that removed it.

**Non-interactive default (auto-pilot, orchestrate-coders):** the skill never
prompts, so it cannot ask whether the repo holds secrets. Check cheaply instead —
a tracked or untracked `.env`, or a `git check-ignore`'d credential file, is
enough to fire gate 1. If that check is inconclusive, assume it fires, and state
the assumption in the report. A caller that knows the repo is clean overrides it
with `data_policy.repo_has_secrets: false` in `.coders.yml`.

| `label`                  | Task profile                                    | 1st                                                              | 2nd                           | 3rd                           |
| ------------------------ | ----------------------------------------------- | ---------------------------------------------------------------- | ----------------------------- | ----------------------------- |
| `architecture`           | Architecture / multi-file refactor / hard bug   | `opus:claude-opus-5`                                             | `codex:gpt-5.5`               | `opus:claude-fable-5`         |
| `standard-pr`            | Standard PR-sized feature or fix                | `opus:claude-sonnet-5`                                           | `codex:gpt-5.6-terra`         | `agy:Gemini 3.6 Flash (High)` |
| `mechanical-bulk`        | Mechanical / bulk / high-volume simple packets  | `codex:gpt-5.6-luna`                                             | `opus:claude-haiku-4-5`       | `devin:glm-5.2`               |
| `frontend-creative`      | Frontend / design / creative naming & API shape | `opus:claude-opus-5`                                             | `opus:claude-sonnet-5`        | `codex:gpt-5.6-sol`           |
| `latency-loop`           | Latency-critical tight loop                     | `devin:swe-1.6-fast`                                             | `agy:Gemini 3.6 Flash (High)` | `codex:gpt-5.6-luna`          |
| `whole-codebase`         | Whole-codebase context (1M-token reads)         | `opus:claude-sonnet-5`                                           | `agy:Gemini 3.6 Flash (High)` | `codex:gpt-5.6-terra`*        |
| `verification-sensitive` | Verification-sensitive (the check IS the task)  | `opus:claude-opus-5`                                             | `opus:claude-sonnet-5`        | — (avoid devin/codex-sandbox) |
| `long-horizon`           | Long-horizon autonomous (overnight-scale)       | `opus:claude-opus-5`                                             | `opus:claude-fable-5`         | `codex:gpt-5.5`               |
| `cross-vendor`           | Cross-vendor diversity (2nd opinion / review)   | pick a different vendor than the 1st author — codex ↔ opus ↔ agy |                               |                               |

**What moved in this refresh, and why:**

- `opus:claude-opus-5` displaces `claude-opus-4-8` everywhere it appeared —
  same price, ~10 points better on Pro.
- `codex:gpt-5.5` is promoted to 2nd on `architecture` and 3rd on
  `long-horizon` over `gpt-5.6-sol`, because 5.5 is the highest _benchmarked_
  Codex tier on the official Terminal-Bench board and Sol is absent from it.
- `agy:Gemini 3.6 Flash` replaces 3.5 Flash in every slot; it beats its
  predecessor on every published benchmark.
- `devin:glm-5.2` takes the 3rd `mechanical-bulk` slot from `devin:swe-1.6`:
  it has an actual SWE-bench Pro number (62.1%) where swe-1.x has none, and is
  free at 200K on Pro. **Both are gated out on any repo with secrets** — this
  row only applies to clean/public repos.
- `devin:kimi-k3` is deliberately **not** in the table despite the strongest
  open-weights numbers here. It is the pick to reach for manually on a public
  repo where you want frontier-adjacent quality at $3/$15; it stays out of the
  defaults because the gate removes it from exactly the repos most users route.

## Operational modifiers (from pilot runs — outrank benchmark deltas)

| Backend | Observed behavior                                                                                       | Selection impact                                                                                                                       |
| ------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| devin   | `accept-edits` mode cannot run verify commands → packets always return unverified                       | Fine for edits; penalize when verification honesty is the deliverable                                                                  |
| devin   | Model marketplace: the `--model` you pass changes vendor, price, and jurisdiction — but not the harness | Always name the model explicitly; never rely on the default alias, and never use `devin:adaptive` when you need to know what ran       |
| codex   | Sandbox false-FAILs on home-dir caches (dprint/uv) → orchestrator re-run is authoritative               | Not disqualifying; avoid when the task's spec depends on in-sandbox check output                                                       |
| codex   | Network egress is blocked by default inside its sandbox                                                 | The structural anti-exfiltration win — but a packet that legitimately needs network (installs, API calls) fails until egress is opened |
| agy     | cwd alone does not contain it — escaped worktree in pilot; needs containment backstop                   | Penalize for tasks brushing against the main checkout; fine for scoped worktree edits                                                  |
| opus    | Self-verifies honestly, including flagging its own workarounds                                          | Prefer for verification-sensitive and review-adjacent packets                                                                          |
