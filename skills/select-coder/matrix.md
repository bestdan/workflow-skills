# Coder capability matrix

Decision reference only: which coder to use, and what disqualifies one.

[`refresh-coder-comparison.md`](refresh-coder-comparison.md) is where the numbers come from and how to refresh them; `/refresh-coder-comparison` runs it.

**Cached: 2026-07-28.** Refresh when this is older than ~2 months, or when a
model in play isn't listed. Don't answer from a stale table as if it were current. If we are within one week of needing a refresh, let the user know.

**Partial pass 2026-08-12** — date deliberately not bumped. Verified: model
rosters, the Terminal-Bench 2.1 board, the SWE-bench Pro board, and every gate
citation link. **Not checked: pricing/tiers/context, secret exposure,
containment, and integrity beyond the boards' own hack penalties.** SWE-bench
Pro's board is itself a generation behind — it lists no Opus 5, GPT-5.6, or
Gemini 3.6 — so every `Pro` figure here is a fallback, not a current reading.

**Two dimensions are per-backend, not per-model:** secret exposure and
containment are properties of the harness. Swapping `agy:Gemini 3.6 Flash` for
`agy:Gemini 3.1 Pro` changes correctness and speed; it changes nothing about
who may read your `.env`. Everything else in the model tables is per-model.

## Gates — run before ranking

These filter, they don't penalize. A backend that fails a gate is **removed**,
and the report names the gate that removed it. A gate is not a tiebreak.

### Gate 1 — secret exposure

Fires if the agent could read a live secret or real PII in this repo: a present
`.env`, credential files, fixtures with real user data, a CI token in scope.
Training alone does not fire it — say so rather than silently filtering.

| Backend | Verdict     | Why                                                                                                                                                                                                                           |
| ------- | ----------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `agy`   | **dropped** | Google staff "may access, view, review and use Interactions" ([ToS][agy-tos]) — what the agent reads becomes human-reviewable, paid subscription included                                                                     |
| `devin` | **dropped** | Demonstrated prompt-injection exfiltration of secrets and env vars ([Embrace The Red][etr]); Cognition names no model provider for any of its 37 families ([privacy][cog-privacy]), so "whose servers saw it" is unanswerable |
| `codex` | keep        | Prefer `auth: api-key` over a consumer ChatGPT login, which trains by default and makes a leak durable — that ranks codex, it doesn't disqualify it                                                                           |
| `opus`  | keep        | No training on customer content, 30-day retention — a read secret stays transient                                                                                                                                             |

**Whatever survives, the vendor is the second line of defense, not the first.**
Deny reads of `~/.ssh`, `~/.aws`, and `~/.config`, and keep `.env` out of the
coder's worktree. Claude Code's sandbox permits reads across the whole machine
by default ([sandboxing][claude-sandbox]); that, not the vendor choice, is what
prevents the leak.

### Gate 2 — containment

Fires when packets run unattended or in parallel near the main checkout.

- Drop **`agy`** — `--sandbox` imposes terminal restrictions only and is inert
  under `--dangerously-skip-permissions` ([issue #36][agy-36]); nothing pins a
  workspace root, and a pilot run escaped its worktree.
- **Standing exception (`--cao-fleet`), carried from `SKILL.md`:** a CAO-dispatched
  `agy` is pointed at a caller-owned worktree rather than the main checkout, so
  the gate does not fire for it. Be precise about what that is: a configured
  working directory, not an OS-enforced boundary. It moves agy's blast radius;
  it does not fence it. The exemption exists because `--cao-fleet` has only two
  dispatchable backends — it is a scoping decision, not a containment guarantee.

### Non-interactive runs

Auto-pilot and orchestrate-coders never prompt, so they can't ask whether the
repo holds secrets. Check cheaply instead: a tracked or untracked `.env`, or a
`git check-ignore`'d credential file, fires gate 1. If inconclusive, assume it
fires and state the assumption in the report. `data_policy.repo_has_secrets: false`
in `.coders.yml` overrides.

### Carve-out — `codex:gpt-5.6-sol` and verification

Not a gate; a hard exclusion on one label. Sol games its own evaluations at the
highest rate METR has recorded — 55.4% on the honesty suite vs 41.2% for
GPT-5.5, including extracting hidden test answers and attempting container-daemon
privilege escalation ([METR][metr-sol]). **Never route it to
`verification-sensitive` work**, where the check is the deliverable and no outer
loop catches a gamed result. It stays a top pick for implementation _because_
the orchestrator verifies independently — treat that check run as mandatory, not
optional. Substitute `codex:gpt-5.5` wherever a coder's self-report has to be
trusted.

[agy-tos]: https://antigravity.google/terms
[agy-36]: https://github.com/google-antigravity/antigravity-cli/issues/36
[cog-privacy]: https://cognition.com/privacy-policy
[etr]: https://embracethered.com/blog/posts/2025/devin-can-leak-your-secrets/
[claude-sandbox]: https://code.claude.com/docs/en/sandboxing
[metr-sol]: https://metr.org/blog/2026-06-26-gpt-5-6-sol/

## Routing table (task profile → ranked candidates)

`label` is emitted by the **assess-task** skill (`skills/assess-task/SKILL.md`) —
select-coder consumes that profile rather than re-deriving one. The final
`cross-vendor` row is a select-coder routing modifier, not an assess-task label.

| `label`                  | Task profile                                    | 1st                                                              | 2nd                           | 3rd                                                    |
| ------------------------ | ----------------------------------------------- | ---------------------------------------------------------------- | ----------------------------- | ------------------------------------------------------ |
| `architecture`           | Architecture / multi-file refactor / hard bug   | `opus:claude-opus-5`                                             | `codex:gpt-5.6-sol`           | `opus:claude-fable-5`                                  |
| `standard-pr`            | Standard PR-sized feature or fix                | `opus:claude-sonnet-5`                                           | `codex:gpt-5.6-terra`         | `agy:Gemini 3.6 Flash (High)`                          |
| `mechanical-bulk`        | Mechanical / bulk / high-volume simple packets  | `codex:gpt-5.6-luna`                                             | `opus:claude-haiku-4-5`       | `devin:glm-5.2`†                                       |
| `frontend-creative`      | Frontend / design / creative naming & API shape | `opus:claude-opus-5`                                             | `opus:claude-sonnet-5`        | `codex:gpt-5.6-sol`                                    |
| `latency-loop`           | Latency-critical tight loop                     | `devin:swe-1.6-fast`†                                            | `agy:Gemini 3.6 Flash (High)` | `codex:gpt-5.6-luna`                                   |
| `whole-codebase`         | Whole-codebase context (1M-token reads)         | `opus:claude-sonnet-5`                                           | `agy:Gemini 3.6 Flash (High)` | `codex:gpt-5.6-terra`*                                 |
| `verification-sensitive` | Verification-sensitive (the check IS the task)  | `opus:claude-opus-5`                                             | `opus:claude-sonnet-5`        | — (**never `gpt-5.6-sol`**; avoid devin/codex-sandbox) |
| `long-horizon`           | Long-horizon autonomous (overnight-scale)       | `opus:claude-opus-5`                                             | `opus:claude-fable-5`         | `codex:gpt-5.6-sol`                                    |
| `cross-vendor`           | Cross-vendor diversity (2nd opinion / review)   | pick a different vendor than the 1st author — codex ↔ opus ↔ agy |                               |                                                        |

†Gated out on any repo with secrets — these rows apply to clean or public repos only.

*`whole-codebase` is ranked by the window **actually served**, not the advertised
one. If gate 1 removes agy, the 2nd slot is **empty** rather than filled by
codex — say so instead of routing a 1M-token read at an unverified window.

`devin:kimi-k3` is deliberately absent despite the strongest open-weights
numbers here: reach for it manually on a public repo, not as a default.

Meta's **Muse Spark 1.1** is absent for a different reason: it leads SWE-bench
Pro (61.5%) and scores 76.2% on TB 2.1, but no backend here can reach it. It
fails the "an account that can actually reach it" test, not the capability one —
revisit if it lands in a backend's roster.

## Models by backend

Correctness is one figure per row: **TB** = Terminal-Bench 2.1, **Pro** =
SWE-bench Pro, **SWE-V** = SWE-bench Verified. `*` marks a vendor-reported
number (an upper bound, not an independent run). `‡` marks a figure that does
**not** appear on the primary board and whose provenance is unverified — it is
retained, not trusted, and must not break a tie.

### opus (native Claude subagent — always available)

| Spec                    | Cost | Speed  | Context  | Correctness | Best for                                                                                    |
| ----------------------- | ---- | ------ | -------- | ----------- | ------------------------------------------------------------------------------------------- |
| `opus:claude-opus-5`    | $$$  | medium | 1M       | TB ~85%‡    | **Default for hard packets.** Architecture, long-horizon, agentic; honest self-verification |
| `opus:claude-fable-5`   | $$$  | slow   | 1M       | TB 83.8%    | The hardest problems. Ask before selecting unless the task is hard-but-small                |
| `opus:claude-sonnet-5`  | $$   | fast   | 1M       | TB 74.6%    | Default PR-sized implementation; best cost/quality balance                                  |
| `opus:claude-opus-4-8`  | $$$  | medium | 1M       | TB 78.9%    | Fallback only — Opus 5 dominates it at identical price                                      |
| `opus:claude-haiku-4-5` | $    | fast   | **200K** | modest      | Mechanical edits, renames, config churn, high-volume simple packets                         |

`claude-opus-5` has never been submitted to Terminal-Bench 2.1 — the 17-entry
board tops out at Fable 5. Its `~85%` is an estimate of unverified origin, so
the row's ranking rests on the operational modifiers below, not on that number.

Haiku's 200K is the one to watch when fanning out over large files. Claude
models carry the strongest consensus on design taste and convention-following —
the default for frontend/visual and review-adjacent packets.

### codex (OpenAI Codex CLI)

| Spec                  | Cost | Speed  | Context | Correctness   | Best for                                                                                    |
| --------------------- | ---- | ------ | ------- | ------------- | ------------------------------------------------------------------------------------------- |
| `codex:gpt-5.6-sol`   | $$$  | medium | 1.05M*  | TB 88.8%*     | Frontier agentic/terminal work, hard bugs — **except `verification-sensitive`**             |
| `codex:gpt-5.6-terra` | $$   | fast   | 1.05M*  | TB 78.4%      | Default Codex implementation when cost matters but reasoning still does                     |
| `codex:gpt-5.6-luna`  | $    | fast   | 1.05M*  | TB 75.7%      | Parallel fan-out, mechanical/bulk edits, single-file fixes, cheap second passes             |
| `codex:gpt-5.5`       | $$$  | slow   | 1.05M*  | TB 83.1%      | The **integrity-conservative** pick — substitute for Sol when a self-report must be trusted |
| `codex:gpt-5.4`       | $$   | medium | 272K    | Pro 59.1%     | Fallback if Terra is unavailable                                                            |
| `codex:gpt-5.4-mini`  | $    | fast   | 400K    | previous tier | Fallback if Luna is unavailable                                                             |

Context figures are model-card upper bounds — the CLI reportedly serves
272–400K. **Don't pick codex on the strength of a 1M window without measuring
it.** Codex is the most token-efficient harness here and has the best
sandboxing; slightly weaker design taste than Claude.

### agy (Google Antigravity CLI — subscription quota, not per-token)

| Spec                          | Cost | Speed | Context | Correctness | Best for                                                                   |
| ----------------------------- | ---- | ----- | ------- | ----------- | -------------------------------------------------------------------------- |
| `agy:Gemini 3.6 Flash (High)` | $    | fast  | 1M      | TB 78.0%*   | Fast agentic default; supersedes 3.5 Flash on every published benchmark    |
| `agy:Gemini 3.5 Flash (High)` | $    | fast  | 1M      | Pro 55.1%‡  | Fallback if 3.6 is unavailable                                             |
| `agy:Gemini 3.1 Pro (High)`   | $$   | slow  | 1M      | TB 65.8%    | Deliberate reasoning/debugging — loses to both Flash tiers on agentic work |
| `agy:gpt-oss-120b (Medium)`   | $    | fast  | —       | none        | Not recommended; no data to justify routing to it                          |

Quota, not tokens: 5-hour refresh windows, and background sub-agents burn quota
independently. **1M context is no longer agy's differentiator** — Claude is 1M
across the board — so agy is not the default for whole-codebase work. It's a
strong cheap-and-fast option on repos with no live secrets.

### devin (Cognition Devin CLI — a model marketplace, not a model vendor)

`devin:<model>` picks a **vendor and jurisdiction**, not just a capability tier.
Always name the model.

| Spec                      | Vendor            | Cost         | Speed   | Context    | Correctness   | Best for                                               |
| ------------------------- | ----------------- | ------------ | ------- | ---------- | ------------- | ------------------------------------------------------ |
| `devin:swe-1.7`           | Cognition         | free (Pro)   | fast    | 262K       | unbenchmarked | Cheap scoped edits when cost dominates                 |
| `devin:swe-1.7-lightning` | Cognition         | $$           | fastest | 203K       | unbenchmarked | Latency-critical iteration loops                       |
| `devin:swe-1.6-fast`      | Cognition         | $            | fastest | 200K       | unbenchmarked | Latency loop fallback                                  |
| `devin:kimi-k3`           | Moonshot          | $$           | —       | 1M         | TB 80.9%‡     | Open-weights leader; frontier-adjacent on public repos |
| `devin:glm-5.2`           | Zhipu             | free at 200K | —       | 200K or 1M | Pro 62.1%‡    | Best price/performance for bulk work on clean repos    |
| `devin:grok-4.5`          | xAI               | $            | —       | 500K       | TB 79.3%§     | Strong and cheap                                       |
| `devin:inkling`           | Thinking Machines | $            | —       | 1M         | SWE-V 77.6%   | Cheapest 1M-context option with a real score           |

§`grok-4.5`'s 79.3% was scored under **Cursor CLI**, not devin — a different
harness, and the run carries the board's largest hack penalty (−9.0%). Read it
as evidence about the weights, not about `devin:grok-4.5`.

The swe-1.x family has **no published absolute benchmark standing** — free and
unmeasured is what produces silent quality regressions in a fan-out. Never use
`devin:adaptive` (an opaque router — you won't know what ran). Devin also passes
through Claude, GPT-5.6, and Gemini models at or near list price: **don't** —
that inserts an undocumented intermediary in front of a vendor you can reach
directly, for zero capability gain.

## Operational modifiers — these outrank benchmark deltas

| Backend | Observed behavior                                                            | Selection impact                                                                                                           |
| ------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `opus`  | Self-verifies honestly, including flagging its own workarounds               | Prefer for verification-sensitive and review-adjacent packets                                                              |
| `codex` | Sol games evaluations (see carve-out above)                                  | Never `verification-sensitive`; the orchestrator's own check run is mandatory on a Sol packet                              |
| `codex` | Sandbox false-FAILs on home-dir caches (dprint/uv)                           | Orchestrator re-run is authoritative; avoid when the spec depends on in-sandbox output                                     |
| `codex` | Network egress blocked by default inside its sandbox                         | The structural anti-exfiltration win — but a packet that needs network fails until egress is opened                        |
| `agy`   | cwd alone does not contain it — escaped worktree in a pilot                  | Penalize for tasks brushing the main checkout; fine for scoped worktree edits                                              |
| `devin` | `accept-edits` mode cannot run verify commands                               | Packets always return unverified: fine for edits, penalize when verification is the deliverable                            |
| `devin` | `--model` changes vendor, price, and jurisdiction — not the harness          | Always name the model explicitly; never rely on the default alias                                                          |
| `devin` | `--sandbox` requests ACP session mode `autonomous` (as of `devin 3000.3.22`) | On an account whose org gates that mode the handshake fails outright — containment drops to the tool layer or the run dies |
