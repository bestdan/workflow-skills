# Where TypeSafe's Jev could fit in this repo (2026-09-17)

An assessment of [Jev](https://typesafe.ai/), TypeSafe's "System One" model, against
this repository's actual decision points. Dated because it grades a product that
shipped a day earlier: the pricing, latency and capability claims below are
snapshots, not durable facts.

**Nothing here is adopted.** This note exists so the next person who reaches for a
model call to make a small judgment knows whether a typed call is the better tool,
and knows the three places it plainly is.

## What Jev is, in one paragraph

Jev takes a **state** (string or JSON) plus a map of typed **questions** and returns
one typed **answer** per question. No prose, no parsing. There are exactly three
question types:

| Type       | Ask when                                  | Answer                                                                     |
| ---------- | ----------------------------------------- | -------------------------------------------------------------------------- |
| **noul**   | A clean yes/no where P(yes) is the signal | `noul` — a float 0 (no) → 1 (yes)                                          |
| **choice** | One of a known, unordered set wins        | `choice`, `probabilities` (sum 1), `confidence`                            |
| **score**  | The answer sits on an ordered rubric      | `score` (may land between levels), `probabilities`, `legend`, `confidence` |

One `POST https://api.typesafe.ai/v1/systemone` carries every question. They are
evaluated **in parallel and in isolation** against the same state — one answer never
becomes hidden context for another — so adding questions barely moves latency. Docs
quote ~100ms typical, a ~~32k-token budget shared by state and questions, and a
batching win of 11.5× cheaper / 9.6× faster for 13 questions in one call versus 13
calls. Pricing (~~$0.042/MTok in, $0 out) comes from launch coverage, not the docs.

## Using it well — the rules that actually bind

1. **One snap judgment per question.** "Does this convey urgency?" yes; "analyze this
   and decide what to do" no. A question needing extended reasoning is a signal to
   split it, not to write longer instructions.
2. **Decompose, then weight in code.** Don't ask "rate this pitch" — ask about market
   size, feasibility and differentiation separately and combine with your own
   coefficients. When priorities change you edit a number, not a prompt.
3. **Fan out speculatively.** Ask every question the code _might_ need in the one
   call and let the code discard what it doesn't use. Extra questions are nearly free;
   extra calls are not.
4. **Never ask what code can compute.** Deterministic work stays deterministic.
5. **`confidence` is the architecture.** It collapses the distribution's shape into
   one number so the model can say "I don't know", and thresholds should scale with
   the stakes of the action being gated — not be one global number.
6. **Pick the type your code can act on.** A Choice maps to branches, a Score to a
   threshold, a Noul to an `if`. Don't use Noul for a spectrum: `noul: 0.5` means the
   model is split between yes and no, _not_ that the answer is "medium".
7. **Two requests are the exception.** Only chain when the first answer is needed to
   _build_ the second request (fetch more state, pick the next options).

## The shape of the fit

This repo's product is prompt text, so there are two very different adoption classes:

- **Class A — dev/CI tooling** (`scripts/`, `evals/`). Real programs. A network call
  costs a secret and a flake risk, and nothing that touches `just check` may take
  either. Low blast radius otherwise.
- **Class B — runtime skill behavior** (`skills/`, `commands/`). Every adoption here
  makes an installed user need a `TYPESAFE_API_KEY`. Only defensible as a **fast path
  that degrades to today's model judgment when the key is absent**, never as a
  requirement.

## Ranked applications

### 1. Description-collision scoring for `evals/` — Class A, best fit

`evals/` runs a full `claude -p` session per case and greps the log for a `Skill`
invocation. That is a pass/fail answer to a **Choice** question — "given this naive
prompt, which of the 16 skills should fire?" — bought at the price of an agent loop,
which is exactly why the suite is opt-in and non-blocking.

Jev answers that question natively, and returns `probabilities` over all 16 skills.
The margin between the winner and the runner-up is the number that actually matters
here and the current harness cannot produce it: it shows a description **collision**
before it causes a misfire. This repo is full of near-neighbours — `co-review` vs
`local-review`, `research-spike` vs `research-spike-tutorial`, `task` vs
`assess-task`, `do-tasks` vs `deliver-task`, `sweep-for-complete` vs
`sweep-for-archive` vs `complete-task`. The docs' own
[skill-suggestion cookbook](https://docs.typesafe.ai/cookbooks/skill_suggestion)
ranks 182 skills in one request; 16 is trivial.

Honest limit: Jev is not the model doing the routing at runtime, so this is a **proxy
for whether the descriptions discriminate**, not a replication of Claude's selection.
The `claude -p` suite stays the ground truth. Jev's version is the cheap pre-check
that could plausibly run on every PR — which the current one never can.

Bonus: it also covers the two skills with no eval case at all (`auto-pilot`,
`deliver-task`) for free, since scoring is per-prompt against the whole description set.

### 2. The unbuilt output-quality evals — Class A

`evals/README.md` names this extension point and names its blocker: "kept separate so
a flaky judge never blocks the deterministic gate." A Score question per quality
dimension, with `confidence` used to **drop** ambiguous judgments rather than let them
swing a verdict, addresses the specific objection. Still nondeterministic, still
opt-in, still non-blocking — but now cheap enough to actually run.

### 3. `assess-task` — Class B, near-perfect structural match

`skills/assess-task/SKILL.md` asks a model to emit seven fixed-enum dimensions, a
label from a closed set of eight, a `confidence: high | low`, and a `runner_up` when
confidence is low. Every one of those is a Jev primitive:

| `task_profile` field                                                            | Jev question                                               |
| ------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| `complexity`, `creativity`                                                      | Score over the ordered levels                              |
| `scope`                                                                         | Score (`single-file` → `whole-codebase` is ordered)        |
| `autonomy`, `speed_sensitivity`, `cost_sensitivity`, `verification_criticality` | Noul each                                                  |
| `label`                                                                         | Choice over the eight routing labels                       |
| `confidence`, `runner_up`                                                       | **Fall out of `label`'s `probabilities`** — no self-report |

Today `confidence` and `runner_up` are a model's self-assessment, which is the least
reliable thing a model produces; with a Choice they are the distribution itself.
The whole profile is one call, all questions in parallel, ~100ms — replacing a
subagent spawn that `select-coder` and `orchestrate-coders` pay for per packet.

Cost to weigh: the skill reads `related_files` when a description is thin. Jev cannot
go fetch anything, so the caller has to assemble the state first.

### 4. The 7th promote check — Class B, narrowest and cleanest

`commands/promote-tasks.md` runs six deterministic checks in `task-scan.py` plus one
model judgment: _does this card's scope plausibly fit size 5?_ That single judgment is
the only reason scoring needs a model at all, it is restated across four handlers, and
the docs already concede it is tolerable only because `/promote-tasks` is not a
blocking gate.

As a Score over size levels it becomes uniform across handlers, cheap enough to run
over a whole backlog under `all`, and — the real gain — **confidence-gated**: a
genuinely ambiguous card can be _held_ in `new` instead of coin-flipped into
`needs_refinement`. That is strictly better behavior than today's binary.

### 5. Semantic lint for rules `validate.py` structurally cannot check — Class A

`AGENTS.md` carries several load-bearing rules that no gate enforces because they are
semantic, not syntactic:

- **PR title Conventional Commit type.** "The PR title is the release lever… getting
  it wrong ships a wrong release, not a wrong label." A Choice over
  `feat`/`fix`/`chore`/… given a diff summary is a textbook Jev question, and this is
  an unguarded rule with real blast radius.
- **"Frontmatter `description` is interface."** Is this written as trigger conditions
  or as documentation? A Noul.
- **"One fact, one home."** Does this new section restate something already true in
  `commands/task-config.md` or `dev_docs/releasing.md`? Entity alignment, a Noul.
- **Progressive disclosure.** Has a procedure or table leaked into a SKILL.md body
  that belongs in `references/`? A Noul per section.

Constraints: advisory only, and a **separate script** — `validate.py`'s single
dependency is hash-locked and it must stay offline and deterministic.

### 6. Co-review triage — partial fit, and the interesting non-fit

`agents/co-review-reconciler.md` grades findings `high`/`medium`/`low` and gates
auto-apply on it, which _looks_ Jev-shaped. It is not. Its own rules demand reasoning
Jev cannot do — "read the regex, trace the control flow, evaluate the glob", judge the
proposed fix independently of the finding, confirm an Action version is current. Jev
must not replace the reconciler.

What Jev _can_ do in co-review is the work around it: **deduplication** ("are these two
findings the same finding?" — a Noul, and the reconciler already has to merge across
reviewers), and **screening** which findings warrant the expensive pass at all.

### 7. `research-spike` backfill — small but exact

`skills/research-spike/references/adoption.md` calls out that `backfill` is judgment —
"deciding whether a sentence is a deferral at all". That is a Noul over each candidate
sentence. The convergence metrics themselves are arithmetic and must stay in
`research-spike.py`: rule 4.

## Where it does not belong

- **Anything in the blocking gate.** `just check` is hermetic and offline. A network
  call adds a flake source and a secret requirement to the one thing that must have
  neither.
- **Anything `validate.py`, `task-scan.py`, `plan-graph.py` or `tier-coverage.py`
  already decides.** Deterministic work stays deterministic — Jev's own rule 4.
- **The reconciler's correctness judgments.** See §6.
- **Any prose.** PR bodies, task cards, plans, review comments. Jev generates nothing.
- **`select-coder`'s ranking.** Once a profile exists (§3), mapping it through
  `matrix.md` is a lookup. The value is upstream, not here.

## If any of this is ever adopted

- **Fast path, never a requirement.** Every Class B use degrades to today's model
  judgment when no key is present. Route the secret through
  [`dev_docs/auth_key_access.md`](../auth_key_access.md).
- **Logic goes in a typed file.** A Jev call from a skill body is
  `commands/handlers/assets/<name>.py` with a test pair, called from the prose — see
  [CONTRIBUTING.md](../../CONTRIBUTING.md#logic-goes-in-a-typed-file). Not a fenced block.
- **Calibration is claimed, not proven for our questions.** No threshold gates
  anything until it has been tuned against this repo's own cards, prompts and diffs.
  Start by logging Jev's answer _and_ the model's and comparing; promote only what
  agrees.
- **32k is the ceiling.** Fine for task cards, findings and frontmatter. Not fine for
  whole-codebase states or long agent traces without chunking.

## Sources

Official docs were unreachable from this session (egress policy blocks `typesafe.ai`
and `docs.typesafe.ai`). The quotes and API shapes above come from a verbatim
2026-09-16 snapshot of `docs.typesafe.ai` mirrored in
[`docxology/daf-jev`](https://github.com/docxology/daf-jev) under `docs/reference/`,
with every page re-hashed against that repo's `MANIFEST.json` before use. Pricing and
the 32k figure's framing come from launch coverage. **Re-verify against
[docs.typesafe.ai](https://docs.typesafe.ai/introduction) before acting on any number
here.**
