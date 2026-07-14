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
`agy:Gemini 3.5 Flash` for `agy:Gemini 3.1 Pro` changes the model's correctness
and speed; it changes nothing about who may read your `.env`. So those two live
in one cross-backend table below, while context — genuinely per-model — sits in
the per-model tables.

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

_Researched 2026-07-13, verified 2026-07-14 against each vendor's binding terms
and its own docs — cited inline._

**Read this first: the control that actually works is upstream of the vendor.**
No backend here promises not to _process_ a secret the agent hands it — they all
do, because that's what inference is. The reliable protection is that the agent
never reads the secret: keep `.env` and real-PII fixtures out of coder worktrees,
and set deny-read rules for `~/.ssh`, `~/.aws`, and `~/.config` on every backend.
This matters most where you'd least expect it — Claude Code's sandbox permits
**reads across the whole machine** by default, credential files included, unless
you deny them ([sandboxing docs][claude-sandbox]). Vendor choice is the second
line of defense, not the first.

| Backend | Who sees a secret the agent reads?                                                                                                                                                                                                                                                                                                                                    | Persistence / durability                                                                                                                                                                                                                                                                                                                        | Known secret-leak vectors                                                                                                                                                                                                                                              | Containment                                                                                                                                                                                                                                                                                                                                                 |
| ------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| opus    | Anthropic's inference path only. No human review of your content; no repo upload. Telemetry and error reporting are on by default but **carry no code or file paths** (`DISABLE_TELEMETRY=1`, `DISABLE_ERROR_REPORTING=1`). Note `/feedback` **does** send the transcript — don't run it after a secret was read.                                                     | **Lowest.** 30-day retention on commercial/API terms; no training on Customer Content, so a read secret stays transient. ZDR available (Enterprise). Local risk instead: transcripts sit in **plaintext** under `~/.claude/projects`.                                                                                                           | CVEs, all patched: a directory-escape (CVE-2025-54794) and config-injection chains that exfiltrated API keys via a malicious `ANTHROPIC_BASE_URL` in a repo's settings ([Check Point][cp-cc]). Prompt injection via PR comments demonstrated.                          | **OS-enforced** (Seatbelt / bubblewrap) but **Bash-only** — Read/Edit/Write go through the permission system instead. Writes default to the cwd subtree. Anthropic's own docs call it "not a complete isolation boundary," and sandboxed **reads span the whole machine** (`~/.ssh`, `~/.aws`) unless denied — the single most important thing to fix here. |
| codex   | OpenAI's inference path only. No human review, no repo indexing found. Telemetry is anonymous usage/health data — **no code** (`[analytics] enabled = false`).                                                                                                                                                                                                        | **Depends on the plan.** API-billed or ChatGPT Business/Enterprise: no training, so transient ([data policy][oai-data], [Codex admin FAQ][oai-codex]). Consumer ChatGPT (Free/Plus/Pro): training is ON by default — a read secret becomes **durable** unless you opted out in Data Controls. `probe-coders.sh` reports which via `codex.auth`. | CVE-2025-61260: command injection via crafted GitHub branch names during cloud tasks, used to retrieve GitHub tokens; patched. Researchers report sandbox bypasses exist.                                                                                              | **Strongest of the four.** OS-enforced: Seatbelt (macOS), bubblewrap + seccomp (Linux). **Network egress blocked by default** — the single best structural defense against exfiltration here. `.git`, `.codex`, `.agents` stay read-only even under `workspace-write`.                                                                                      |
| agy     | **Google employees and contractors.** Its [own ToS][agy-tos] states they "may access, view, review and use Interactions" — so a `.env` the agent reads is human-reviewable content, not just model input. This is the concrete secrets problem with agy, and a paid Google AI subscription does **not** exempt you (only Gemini Enterprise / Workspace / Cloud does). | **High, and unbounded.** Interactions feed training by default; **no retention period is stated anywhere**; deletion is by emailed request. An opt-out exists in settings — but the CLI never reports whether it's in force, so the skill cannot confirm it.                                                                                    | No agy-specific incident found. Its predecessor Gemini CLI had a confirmed exfiltration vuln (prompt injection + weak command allowlisting, [Tracebit][tracebit]) and a CVSS-10 RCE that ran **before the sandbox initialized**.                                       | **Effectively none for our dispatch.** `--sandbox` imposes only terminal restrictions, and [issue #36][agy-36] documents that `--dangerously-skip-permissions` auto-approves the bypass-sandbox prompt, making it inert. Nothing pins a workspace root. Our pilot's worktree escape is consistent with this.                                                |
| devin   | Cognition, plus **whoever serves the model you picked** — see the passthrough hole below. The CLI is a local agent; the cloud handoff is explicit/opt-in.                                                                                                                                                                                                             | Trains on Customer Data **by default** ([ToS §3.3.1][cog-tos], [security docs][cog-sec] — they agree). Opt-out on paid plans (admin-only on Teams) and it enables ZDR. Enterprise never trained on without written consent. No retention period published.                                                                                      | **The worst record here for your threat model.** Documented indirect prompt-injection attacks leaking **secrets and env vars** out of Devin via its shell and browser tools ([Embrace The Red][etr]) — reported Apr 2025, reportedly still unresolved 120+ days later. | **Real, and OS-level when asked for.** `accept-edits` confines edits to the workspace and prompts for anything outside it; `--sandbox` uses bubblewrap (network allowlist documented unstable). `bypass` mode "lacks OS-level isolation." No escape reports found.                                                                                          |

**The passthrough hole.** Routing a packet through `devin:glm-5.2`,
`devin:deepseek-v4`, or `devin:kimi-k2.7` sends whatever the agent read — secrets
included — to a model served by Zhipu, DeepSeek, or Moonshot. Cognition documents
**nothing** about whether it proxies those requests or you hit the origin labs'
APIs under _their_ data policies, and those labs are absent from its published
subprocessor list. You cannot answer "who saw my `.env`" for these models at all.
That is an open question, not a cleared one — keep them off any repo where the
answer would matter.

**Practical upshot.** Only `opus` is no-training _by default_ on ordinary
commercial terms. `codex` is too when API-billed or on ChatGPT Business/
Enterprise. `agy` and `devin` both train by default, and their opt-outs — which exist but
which neither CLI reports — can't be verified by the skill. That governs how
_durable_ a leak is, not whether the gate fires: gate 1 drops both on exposure
grounds regardless (agy for human review, devin for its exfiltration record).
Separately, `agy` is the one backend here that also cannot be confined to its
worktree.

[oai-data]: https://platform.openai.com/docs/guides/your-data
[oai-codex]: https://developers.openai.com/codex/enterprise/work-admin-faq
[agy-tos]: https://antigravity.google/terms
[agy-36]: https://github.com/google-antigravity/antigravity-cli/issues/36
[cog-tos]: https://cognition.com/legal/platform-terms-of-service
[cog-sec]: https://docs.devin.ai/admin/security
[claude-data]: https://code.claude.com/docs/en/data-usage
[claude-sandbox]: https://code.claude.com/docs/en/sandboxing
[cp-cc]: https://research.checkpoint.com/2026/rce-and-api-token-exfiltration-through-claude-code-project-files-cve-2025-59536/
[tracebit]: https://tracebit.com/blog/code-exec-deception-gemini-ai-cli-hijack
[etr]: https://embracethered.com/blog/posts/2025/devin-can-leak-your-secrets/

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
nothing it doesn't have to win on other axes. Given its secret-exposure posture
(Google staff may read the Interactions the agent sends, paid subscription
included) and its absent containment, **agy is no longer the default for
whole-codebase work** — see the routing table. It remains a fine pick on a repo
with no live secrets, which is most of them.

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
serves 272–400K (see the codex table). If the secret-exposure gate removes agy, the
2nd slot is empty rather than filled by codex — say so instead of routing a
1M-token read to an unverified window.

**Two gates run before this table, and they can overrule every row in it:**

1. **Secret exposure.** Ask one question: _could the agent read a live secret or
   real PII in this repo?_ (`.env` present, credential files, fixtures with real
   user data, a CI token in scope.) If yes:
   - Drop **`agy`** — Google staff may read what the agent read.
   - Drop **`devin`** — demonstrated prompt-injection secret exfiltration, and its
     passthrough models make "who saw it" unanswerable.
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

| Backend | Observed behavior                                                                         | Selection impact                                                                                                                       |
| ------- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| devin   | `accept-edits` mode cannot run verify commands → packets always return unverified         | Fine for edits; penalize when verification honesty is the deliverable                                                                  |
| codex   | Sandbox false-FAILs on home-dir caches (dprint/uv) → orchestrator re-run is authoritative | Not disqualifying; avoid when the task's spec depends on in-sandbox check output                                                       |
| codex   | Network egress is blocked by default inside its sandbox                                   | The structural anti-exfiltration win — but a packet that legitimately needs network (installs, API calls) fails until egress is opened |
| agy     | cwd alone does not contain it — escaped worktree in pilot; needs containment backstop     | Penalize for tasks brushing against the main checkout; fine for scoped worktree edits                                                  |
| opus    | Self-verifies honestly, including flagging its own workarounds                            | Prefer for verification-sensitive and review-adjacent packets                                                                          |
