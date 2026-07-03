# Coder capability matrix

**Cached: 2026-07-03.** Benchmarks and pricing move fast. If this date is
older than ~2 months, or a model in play isn't listed, refresh before
recommending: web-search current SWE-bench Pro (Scale leaderboard),
Terminal-Bench (tbench.ai), and vendor pricing pages — `firecrawl` (CLI) or
WebFetch work for pages that block plain fetches — and update this file with
a new cache date. Prefer Scale and tbench.ai over aggregator sites; treat
vendor-self-reported numbers as upper bounds.

## Dimensions

| Dimension        | What it measures                                                 |
| ---------------- | ---------------------------------------------------------------- |
| **Correctness**  | Multi-file coding accuracy (SWE-bench Pro, Terminal-Bench)       |
| **Speed**        | Wall-clock to a finished packet (model tok/s + harness overhead) |
| **Cost**         | `$` cheap · `$$` mid · `$$$` frontier (per-token or quota draw)  |
| **Creativity**   | Design taste, frontend/visual work, naming, API ergonomics       |
| **Autonomy**     | Long-horizon multi-step work without steering                    |
| **Verification** | Does the coder honestly run/report checks in our harness?        |

## Models by backend

### opus (native Claude subagent — always available)

| Spec                    | $/Mtok in/out | Cost | Speed  | Correctness                                        | Best for                                                              |
| ----------------------- | ------------- | ---- | ------ | -------------------------------------------------- | --------------------------------------------------------------------- |
| `opus:claude-opus-4-8`  | $5 / $25      | $$$  | medium | SWE-bench Pro ~69% · Terminal-Bench 2.1 ~79%       | Architecture, long-horizon, hardest packets; honest self-verification |
| `opus:claude-sonnet-5`  | $3 / $15      | $$   | fast   | ~63% Pro — "~79% of Fable capability at 30% price" | Default PR-sized implementation; best cost/quality balance            |
| `opus:claude-haiku-4-5` | $1 / $5       | $    | fast   | Modest; cheapest per solved task (~$0.13/point)    | Mechanical edits, renames, config churn, high-volume simple packets   |
| `opus:claude-fable-5`   | $10 / $50     | $$$  | slow   | SWE-bench Pro ~80% · Terminal-Bench 2.1 88% (SOTA) | Only when the user explicitly opts in — hardest unsolved problems     |

Claude models carry the strongest practitioner consensus on **creativity/design
taste** and convention-following; they are the default for frontend/visual
packets and for review-adjacent work.

### codex (OpenAI Codex CLI)

| Spec                 | $/Mtok in/out | Cost | Speed  | Correctness                                          | Best for                                                    |
| -------------------- | ------------- | ---- | ------ | ---------------------------------------------------- | ----------------------------------------------------------- |
| `codex:gpt-5.5`      | $5 / $30      | $$$  | slow   | Terminal-Bench 2.1 ~83% (top harness) · Pro ~57–59%* | Frontier agentic/terminal work; cross-vendor second opinion |
| `codex:gpt-5.4`      | $2.50 / $15   | $$   | medium | SWE-bench Pro ~59% (tops Scale public set)           | Workhorse implementation; best benchmark-per-dollar         |
| `codex:gpt-5.4-mini` | $0.75 / $4.50 | $    | fast   | Pro ~54% — strong for the price                      | Parallel packet fan-out, single-file fixes, bulk work       |

*OpenAI self-reports higher; independent harnesses score lower — treat
vendor numbers as upper bounds. Codex CLI is the most **token-efficient**
harness (~4× fewer tokens than Claude Code in practitioner tests) and has
the best sandboxing; slightly weaker design taste than Claude.

### agy (Google Antigravity CLI — subscription quota, not per-token)

| Spec                          | Cost basis                    | Cost | Speed | Correctness                                       | Best for                                                                                                |
| ----------------------------- | ----------------------------- | ---- | ----- | ------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `agy:Gemini 3.5 Flash (High)` | quota (≈$1.50/$9 API-equiv)   | $    | fast  | SWE-bench Verified ~79% · Terminal-Bench 2.1 ~76% | Fast agentic default; unusually strong for a "fast" model — beats its own Pro sibling on coding benches |
| `agy:Gemini 3.1 Pro (High)`   | quota (≈$2/$12; heavier draw) | $$   | slow  | Verified ~81%, but loses to Flash on agentic work | Deliberate reasoning/debugging where thinking depth beats speed                                         |

Quota caveat: 5-hour refresh windows; background sub-agents burn quota
independently. 1M context makes agy good for whole-codebase context tasks.

### devin (Cognition Devin CLI)

| Spec                 | Cost basis                | Cost | Speed                | Correctness                                                                                             | Best for                               |
| -------------------- | ------------------------- | ---- | -------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| `devin:swe-1.6`      | free w/ plan (0× credits) | $    | fast (~200 tok/s)    | Upper-mid tier; Cognition publishes only relative deltas (+10% over 1.5) — absolute standing UNVERIFIED | Cheap scoped edits when cost dominates |
| `devin:swe-1.6-fast` | $0.30/$1.50 (pro, 0.5×)   | $    | fastest (~950 tok/s) | Same weights as swe-1.6                                                                                 | Latency-critical iteration loops       |
| `devin:swe-1.6-slow` | free-tier pin             | $    | slow                 | Same family                                                                                             | Only option on the free tier           |

Devin also exposes frontier passthroughs (Opus/Sonnet/GPT/Gemini) at credit
multipliers — usually worse economics than calling those models via their
native backend; don't route through devin for them.

## Routing table (task profile → ranked candidates)

| Task profile                                    | 1st                                                              | 2nd                           | 3rd                           |
| ----------------------------------------------- | ---------------------------------------------------------------- | ----------------------------- | ----------------------------- |
| Architecture / multi-file refactor / hard bug   | `opus:claude-opus-4-8`                                           | `codex:gpt-5.5`               | `agy:Gemini 3.1 Pro (High)`   |
| Standard PR-sized feature or fix                | `opus:claude-sonnet-5`                                           | `codex:gpt-5.4`               | `agy:Gemini 3.5 Flash (High)` |
| Mechanical / bulk / high-volume simple packets  | `codex:gpt-5.4-mini`                                             | `opus:claude-haiku-4-5`       | `devin:swe-1.6`               |
| Frontend / design / creative naming & API shape | `opus:claude-opus-4-8`                                           | `opus:claude-sonnet-5`        | `codex:gpt-5.5`               |
| Latency-critical tight loop                     | `devin:swe-1.6-fast`                                             | `agy:Gemini 3.5 Flash (High)` | `codex:gpt-5.4-mini`          |
| Whole-codebase context (1M-token reads)         | `agy:Gemini 3.5 Flash (High)`                                    | `opus:claude-sonnet-5`        | —                             |
| Verification-sensitive (the check IS the task)  | `opus:claude-opus-4-8`                                           | `opus:claude-sonnet-5`        | — (avoid devin/codex-sandbox) |
| Long-horizon autonomous (overnight-scale)       | `opus:claude-opus-4-8`                                           | `codex:gpt-5.5`               | —                             |
| Cross-vendor diversity (2nd opinion / review)   | pick a different vendor than the 1st author — codex ↔ opus ↔ agy |                               |                               |

## Operational modifiers (from pilot runs — outrank benchmark deltas)

| Backend | Observed behavior                                                                         | Selection impact                                                                      |
| ------- | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------- |
| devin   | `accept-edits` mode cannot run verify commands → packets always return unverified         | Fine for edits; penalize when verification honesty is the deliverable                 |
| codex   | Sandbox false-FAILs on home-dir caches (dprint/uv) → orchestrator re-run is authoritative | Not disqualifying; avoid when the task's spec depends on in-sandbox check output      |
| agy     | cwd alone does not contain it — escaped worktree in pilot; needs containment backstop     | Penalize for tasks brushing against the main checkout; fine for scoped worktree edits |
| opus    | Self-verifies honestly, including flagging its own workarounds                            | Prefer for verification-sensitive and review-adjacent packets                         |
