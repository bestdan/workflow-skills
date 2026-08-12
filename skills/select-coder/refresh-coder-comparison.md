# refresh-coder-comparison — re-research the coder matrix

The occasional refresh that keeps [`matrix.md`](matrix.md) honest. This file is
the **procedure**: which sources to re-check, how to read them, and how to write
the results back. It holds no findings of its own — matrix.md is where the
current answers live.

**Not the same as `/select-coder --refresh`.** That re-probes _this machine_ —
which CLIs are installed, which tokens are still alive, which models the account
can reach — into `dev_docs/orchestrate-coders/.coders.yml`, on a 30-day clock,
by running `scripts/probe-coders.sh`. This procedure refreshes _world state_:
boards, prices, model rosters, vendor terms. The two share nothing; run both
when both are stale.

## When to run

- matrix.md's `Cached:` date is older than **~2 months**.
- A model someone wants to route to isn't in the matrix.
- A vendor ships a new tier, changes pricing, or renames a model.
- A new safety evaluation, incident report, or terms change lands for a backend
  in the matrix — this is the one trigger that can move a **gate**, so it
  outranks the calendar.

Not on every select-coder invocation. Routing off a two-month-old table is fine;
routing off one that predates a model generation is not.

## What to re-check, and where

| Slice                   | Sources                                                                                    | What you're after                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| Agentic correctness     | Terminal-Bench board (tbench.ai); Artificial Analysis Coding Agent Index                   | One comparable figure per model, harness named                            |
| Multi-file correctness  | Scale's SWE-bench Pro leaderboard; aggregators when it lags                                | A fallback figure where no agentic score exists                           |
| Pricing, tiers, context | Each vendor's pricing page and model cards                                                 | The cost tier and the context window                                      |
| Model rosters           | `devin models list`, `agy models`, `~/.codex/config.toml`, the session's `availableModels` | Models that appeared or disappeared since the last refresh                |
| Integrity / safety      | METR evaluations, vendor system cards                                                      | Anything that makes a coder's self-report untrustworthy                   |
| Secret exposure         | Each vendor's ToS, privacy policy, security/data-usage docs; CVE and incident search       | Who may read what the agent reads, and how durably                        |
| Containment             | Each CLI's sandboxing docs and open issues; our own pilot-run behavior                     | Whether the workspace boundary is OS-enforced, prompt-enforced, or absent |

Fetching: `firecrawl` (CLI) or WebFetch for pages that block plain fetches;
WebSearch to catch entrants nobody thought to look for.

## How to read the sources

- **Coverage is not disagreement.** Absence from a leaderboard means the vendor
  didn't submit — never that the model is weak or unmeasured. Look for an
  independent index that covers it before concluding anything.
- **Check the board itself for staleness.** A leaderboard that lists no
  current-generation model is stale, not authoritative, however official it looks.
- **Vendor self-reports are upper bounds.** Mark them in the matrix (`*`) and
  prefer an independent run when both exist. Where a vendor publishes only
  relative deltas, the row is unbenchmarked — say so rather than implying a number.
- **Score the harness, not just the weights.** A coder backend is agent + model;
  a figure that doesn't name the agent is weaker evidence than one that does.
- **Never break a tie on a SWE-bench Pro delta alone** — the harness isn't
  pinned across aggregators and part of the public split is reported broken.
- **A served window is not a model-card window.** When a CLI reportedly serves
  less than the card advertises, record the card as an upper bound and flag it.
- **Prefer the binding document over the marketing page** for exposure and
  containment: the ToS and the security docs, not the FAQ.

## Writing the results back

matrix.md is decisions only — tier, one score, one line. Anything you learn that
doesn't change a routing choice doesn't go in it, and doesn't come back here
either. Update in this order:

1. **Gate verdicts and their citation links.** Only a documented change in a
   vendor's binding terms, a new incident, or a new integrity finding moves a
   gate — a benchmark move never does. If a verdict changes, check whether
   `SKILL.md` steps 3–4 (which restate the gates and modifiers inline) still
   agree; they are the copy a caller actually reads.
2. **The routing table.** Substitute models within a row before reordering rows.
   Reordering is a claim that the ranking changed; substitution usually isn't.
3. **The model tables.** One correctness figure per row, in the matrix's
   existing units, with `*` on vendor-reported numbers.
4. **The operational modifiers**, if a pilot run or a published evaluation
   contradicted one.
5. **Bump `Cached:`** to today's date. If a slice couldn't be re-verified, leave
   the old figure and say which slice is stale — a bumped date over an unchecked
   number is worse than no refresh.

## Adding or retiring a model

- **Adding.** It needs a cost tier, a context window, and one correctness figure
  before it earns a model-table row. It earns a _routing-table_ slot only by
  displacing something on the dimension that row is about — being new is not a
  reason. On a gated backend, mark the row `†`.
- **Retiring.** Keep a superseded model only while it's a plausible fallback
  (the successor may not be available in a given session); drop it once the
  successor is universal.

## Adding a backend

A new backend needs both gate answers before it can be ranked at all: who sees a
secret the agent reads, and whether its workspace boundary is enforceable. Until
those are answered from binding documents, it doesn't go in the matrix — an
unanswered gate is a fail, not a blank. It also needs a dispatch story in
`orchestrate-coders/backends/`, or a matrix row points at something nothing can run.
