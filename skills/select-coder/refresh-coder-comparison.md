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

## Run this on a local machine

Not from a remote/web session. Two of the slices below need the coder CLIs
themselves (`devin`, `agy`, `codex`), and a managed remote environment's egress
policy will block most of the source hosts outright — a dry run from Claude Code
on the web reached `code.claude.com` and nothing else, with `tbench.ai`,
`artificialanalysis.ai`, `metr.org`, and `antigravity.google` all refused at the
proxy. Discover that at the start, not four slices in.

## What to re-check, and where

| Slice                   | Sources                                                                                                                                                      | What you're after                                                         |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------- |
| Agentic correctness     | `tbench.ai/leaderboard/terminal-bench/<version>` — **not** `/leaderboard`, which is an index; Artificial Analysis Coding Agent Index                         | One comparable figure per model, harness named                            |
| Multi-file correctness  | SWE-bench Pro leaderboard at `labs.scale.com` (`scale.com` 308s to it); aggregators when it lags                                                             | A fallback figure where no agentic score exists                           |
| Pricing, tiers, context | Each vendor's pricing page and model cards — but for the **Claude** rows use the `claude-api` skill, which is authoritative on model ids, pricing and limits | The cost tier and the context window                                      |
| Model rosters           | `devin models list`, `agy models`, `~/.codex/config.toml`, the session's `availableModels`                                                                   | Models that appeared or disappeared since the last refresh                |
| Integrity / safety      | METR evaluations, vendor system cards, Terminal-Bench's per-run **Hacks** column and its `tbench.ai/news` integrity posts                                    | Anything that makes a coder's self-report untrustworthy                   |
| Secret exposure         | Each vendor's ToS, privacy policy, security/data-usage docs; CVE and incident search                                                                         | Who may read what the agent reads, and how durably                        |
| Containment             | Each CLI's sandboxing docs and open issues; our own pilot-run behavior                                                                                       | Whether the workspace boundary is OS-enforced, prompt-enforced, or absent |

Fetching: `firecrawl` (CLI) or WebFetch for pages that block plain fetches;
WebSearch to catch entrants nobody thought to look for. When a host is refused
at the proxy rather than by the page, WebSearch is the only path left — and what
it returns is aggregator evidence, which the next section says you may not set a
number from.

**The correctness slice needs firecrawl specifically**, and firecrawl bills per
call — so raise the spend at the start, not four slices in. Both leaderboards
render client-side: WebFetch returns navigation chrome and reads as an empty
board rather than erroring, which is the failure mode that invites an
aggregator number. The working recipe is `firecrawl map <site> --search
leaderboard` to find the versioned path, then `firecrawl scrape` it. Guessing
the path doesn't work — plausible spellings 404, and a 404 is easy to misread as
"that board was retired".

## How to read the sources

- **Coverage is not disagreement.** Absence from a leaderboard means the vendor
  didn't submit — never that the model is weak or unmeasured. Look for an
  independent index that covers it before concluding anything.
- **Check each board for staleness separately.** A leaderboard that lists no
  current-generation model is stale, not authoritative, however official it
  looks — and staleness is per-board, not per-refresh. As of the last pass
  Terminal-Bench was current while SWE-bench Pro was a full generation behind,
  so a `Pro` figure and a `TB` figure fetched the same afternoon are not equally
  fresh. Mark the stale board's column, don't average across them.
- **Vendor self-reports are upper bounds.** Mark them in the matrix (`*`) and
  prefer an independent run when both exist. Where a vendor publishes only
  relative deltas, the row is unbenchmarked — say so rather than implying a number.
- **Score the harness, not just the weights.** A coder backend is agent + model;
  a figure that doesn't name the agent is weaker evidence than one that does.
  When the board names a _different_ harness than the row you're filling, the
  number does not transfer — it's evidence about the weights. Mark the row, or
  leave it unbenchmarked.
- **Never break a tie on a SWE-bench Pro delta alone** — the harness isn't
  pinned across aggregators and part of the public split is reported broken.
- **Aggregator-only evidence flags a row; it never sets one.** When the primary
  board is unreachable and all you have is a search summary or an SEO round-up,
  you have learned that a row may be stale — not what its number is. Record it as
  stale, leave the old figure, and finish the slice when you can reach the board.
  This is the rule the pre-split matrix quietly broke: its SWE-bench Pro column
  was aggregator-sourced and read like measurement.
- **A served window is not a model-card window.** When a CLI reportedly serves
  less than the card advertises, record the card as an upper bound and flag it.
- **Prefer the binding document over the marketing page** for exposure and
  containment: the ToS and the security docs, not the FAQ.

## Writing the results back

matrix.md is decisions only — tier, one score, one line. Anything you learn that
doesn't change a routing choice doesn't go in it, and doesn't come back here
either. Update in this order:

1. **Gate verdicts and their citation links.** A gate moves on a documented
   change in a vendor's binding terms, a change in what a CLI's sandbox actually
   enforces (a fix that closes an escape, or a newly demonstrated one), a new
   incident, or a new integrity finding. A benchmark move never does — and note
   that a gate can move in both directions: a sandbox that becomes enforceable
   clears gate 2 as surely as a new escape fires it. If a verdict changes, check whether
   `SKILL.md` steps 3–4 (which restate the gates and modifiers inline) still
   agree; they are the copy a caller actually reads.
2. **The routing table.** Substitute models within a row before reordering rows.
   Reordering is a claim that the ranking changed; substitution usually isn't.
3. **The model tables.** One correctness figure per row, in the matrix's
   existing units, with `*` on vendor-reported numbers.
4. **The operational modifiers**, if a pilot run or a published evaluation
   contradicted one.
5. **Bump `Cached:`** — on a full refresh only, and only if every slice was
   actually checked. A single-backend or single-model pass updates its rows and
   **leaves the date alone**: a bumped date marks every unchecked slice fresh and
   suppresses the next real refresh, which is worse than not refreshing at all.
   If a full pass left a slice unverified, keep the old figure, keep the old
   date, and say which slice is stale.

## Adding or retiring a model

- **Adding.** It needs a cost tier, a context window, one correctness figure, and
  an account that can actually reach it — a limited-release or
  institution-gated model is noise in a routing table however good it is
  (`claude-mythos-5` is the standing example). It earns a _routing-table_ slot
  only by displacing something on the dimension that row is about — being new is
  not a reason. If the model sits on a backend a **gate** removes, mark the row
  `†` — matrix.md's routing-table footnote, not the account-gating sense used
  above. Mark every such row, not just some: an unmarked row on a gated backend
  reads as unconditionally available.
- **Retiring.** Keep a superseded model only while it's a plausible fallback
  (the successor may not be available in a given session); drop it once the
  successor is universal.

## Adding a backend

A new backend needs both gate answers before it can be ranked at all: who sees a
secret the agent reads, and whether its workspace boundary is enforceable. Until
those are answered from binding documents, it doesn't go in the matrix — an
unanswered gate is a fail, not a blank. It also needs a dispatch story in
`orchestrate-coders/backends/`, or a matrix row points at something nothing can run.
