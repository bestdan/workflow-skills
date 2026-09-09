# Finding prose that should be code

How to run a prose-to-code survey on this repo: what the smell looks like, how
to rank what you find, and the mistakes that have already been paid for. Two
prose-to-code rounds have run (2026-07 and 2026-09); this file is the method
they left behind, not their results. Their point-in-time records are
[`dev_docs/deterministic-code-opportunity.md` at `eef937e`](https://github.com/bestdan/workflow-skills/blob/eef937e/dev_docs/deterministic-code-opportunity.md)
and
[`dev_docs/2026-09-09-prose-to-code-index.md` at `c1af35e`](https://github.com/bestdan/workflow-skills/blob/c1af35eb26af8d614cf2fa93ebd41b6e919c4af1/dev_docs/2026-09-09-prose-to-code-index.md).
The candidates neither round shipped are filed as issues under the
[round 3 milestone](https://github.com/bestdan/workflow-skills/milestone/3).

## The smell

Nearly everything this plugin ships is markdown an agent reads and re-executes
by reasoning. Prose is right for judgment — routing, scope assessment,
inference over ambiguous input. It is wrong for computation the agent has to
re-derive identically on every invocation: each re-derivation costs context and
is a fresh chance to apply the rule slightly differently from last time. A
fenced block is worse again, because nothing in the gate can see it.

The rule itself, and where an extracted helper goes, live in
[CONTRIBUTING.md](../CONTRIBUTING.md#logic-goes-in-a-typed-file). This file is
only about finding the candidates.

## How to find it

Read every assigned file in full against one rubric. Splitting the tree across
parallel readers, one file group each, is how the 2026-09 round did it. The
2026-07 round instead picked its hotspots by grepping for computation-shaped
language, and a reader who does that finds the fenced blocks and misses
everything else.

| Category                                        | What it looks like                                                                 | Shipped example                                                                                                                              |
| ----------------------------------------------- | ---------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| **Hand-walked algorithms**                      | Prose instructing the agent to walk a graph, sort, or accumulate a count           | `linear-reoptimize.md`'s cycle detection, topological sort and priority-inversion sweep → `commands/handlers/assets/linear-graph-analyze.py` |
| **Fenced blocks with logic**                    | A `bash` fence with loops, cursors, or `jq` pipelines                              | `gh-issue-promote.md` step 3a's 36-line GraphQL pagination loop → `commands/handlers/assets/gh-issue-rollups.py`                             |
| **Decision tables**                             | A table of input conditions the agent applies by hand, especially with precedence  | the kanban section table in `linear-list.md`, `gh-issue.md` and `jira.md` → `commands/handlers/assets/kanban-classify.py`                    |
| **API choreography**                            | "try source A, then B, then C, then post-filter" over a CLI or fetched JSON        | `linear-sweep-complete.md`'s three-source PR discovery → `commands/handlers/assets/linear-pr-resolve.py`                                     |
| **Rules that could be hooks or lints**          | A "never do X" the reader is trusted to honour, guarding something destructive     | research-spike-tutorial's `$WORK` root check ahead of an `rm -rf` → `scripts/tutorial-root-guard.sh`                                         |
| **Prose re-deriving a shipped script**          | A handler that walks the flow by hand and then says the script does the same thing | `linear-archive.md`'s GraphQL sweep, whose own §"Run it without an agent" prefers `linear-archive.py`                                        |
| **The same procedure restated across handlers** | One rule with per-tracker deltas, copied N times                                   | Jira transition-id resolution at five call sites → `commands/handlers/assets/jira-resolve-transition.py`                                     |

**Re-check every absence claim before you rank it.** "No script computes this"
is what makes a finding a finding, and it is the claim a reader is most likely
to get wrong — inference cannot tell "not there" from "not where I looked". Run
`rg` against `scripts/` and `commands/handlers/assets/` for the concept, not
only for the filename you expect, and record that you did. Several round-2
findings landed as an extra flag or subcommand on an existing asset rather than
a new script, which is the outcome this step exists to produce.

## How to rank

Four weights, in this order:

1. **An explicit prior defect in the prose.** A dated incident, a quoted wrong
   result, or a warning the prose gives itself. This dominates everything else:
   it is evidence the hand-walk already failed once.
2. **Confidence the logic is deterministic.** If two careful readers could
   legitimately disagree on an output, it is judgment and stays prose.
3. **Call-site count.** Every copy is an independent chance to break the rule.
4. **Size.** `S` under 50 lines, `M` under 200, `L` above. Small and duplicated
   beats large and singular.

Sequence the first slice by (1) and (4) together — a small finding with a dated
defect is the cheapest proof that the round is worth running.

## The one standing constraint

**A script can own the decision over fetched JSON, never the fetch and never
the tracker write.** MCP tools (`mcp__linear__*`, `mcp__atlassian__*`) are
invocable only by the agent, so anything that must call one stays in prose.
Shape findings accordingly: JSON in on stdin, a decision out on stdout, with
the agent doing the reads before and the writes after.

Two refinements the rounds earned, both worth knowing before you write a
finding off:

- **A raw-credential or CLI path is not MCP.** Linear reads over GraphQL with a
  personal API key are scriptable and mostly are already
  (`linear-scan.py`, `linear-ready.py`, `linear-relations.py`,
  `linear-false-closures.py`), behind a fast-path/floor fallback. Only
  mutations and interactive, auth-bound calls are genuinely prose-only.
- **"That looks like judgment" is a hypothesis, not a verdict.** Deciding
  whether a closed issue is owned by delivered work read as judgment until it
  was written down as four explicit signals in `linear-false-closures.py`.
  Try to state the rule set before concluding there isn't one.

## The shape of a delivered extraction

A helper, its test pair, and the prose rewritten to call it — all three, in one
PR. [CONTRIBUTING.md](../CONTRIBUTING.md#logic-goes-in-a-typed-file) carries
the mechanics: which directory, the `scripts/test_<name>.py` +
`scripts/test-<name>.sh` pair the gate discovers by glob, and how to invoke the
helper through `${CLAUDE_PLUGIN_ROOT}`.

The half that is easy to skip is the prose. Steps run as separate tool calls
with no shared shell state, so **the helper's stdout is the contract** — say in
the prose exactly what the helper prints and what each exit code means, and
delete the hand-walk rather than leaving it beside the call as a fallback. A
surviving hand-walk is the next round's finding.

## Gotchas that generalise

Each of these cost a review round or a production defect once. Every rule below
is embodied in a file today; that file's header carries the specifics.

1. **Default a path to the caller's cwd, never to `__file__` or a git-root
   lookup.** An asset ships to consumers and runs from the installed plugin, so
   `__file__` resolves to the plugin's own checkout — a scan defaulted that way
   reads the plugin's tree and fails _silently green_, because the plugin has
   no such tree and "nothing found is clean" reports success.
   `scripts/task-scan.py`, `scripts/claim-scan.sh` and
   `scripts/research-spike.py` all default to cwd; `scripts/validate.py` is the
   deliberate exception, script-relative so it can validate the plugin's own
   tree in CI, and it validates a consumer's cards only when passed an explicit
   argument. Do not unify these.
2. **Match an identifier as a whole token.** Substring matching lets `task_1`
   match a `Claims-task: task_13` line, and a bracket-form-only title filter
   made a 2026-09-02 nightly run report an issue as having no PR while an open
   PR named it. `scripts/claim-scan.sh` uses `grep -Fxq`;
   `linear-pr-resolve.py`'s `identifier_in_title` keeps that title as a
   fixture.
3. **A failed read is not a zero value.** Give it its own exit code and verdict
   so the caller falls back to a conservative proxy instead of treating "no
   headroom measured" as "no headroom needed".
   `spawn-orchestrator.sh reserve-gate --read-failed` exits 3 with
   `verdict=fallback`, never `proceed`.
4. **"Found nothing" is not "found something negative".** Downstream usually
   routes the two differently, so collapsing them misroutes one.
   `linear-pr-resolve.py` keeps `no_pr` (every probe succeeded, nothing found)
   distinct from `closed_unmerged` (every PR read cleanly and none merged);
   `/reconcile-tasks` garbage-collects the first and demotes the second.
5. **A resolver returns an explicit unresolved marker, never the first match.**
   A tie is the caller's problem to handle, not the script's to guess.
   `jira-resolve-transition.py` prints `AMBIGUOUS` or `NONE` with the candidate
   names in both of its modes.
6. **A per-pair comparator must decide every pair by one fixed precedence.** A
   comparator that falls to a different key depending on which side is missing
   a field is not transitive — three same-priority nodes can rank A<B, B<C and
   C<A, and the sort silently cycles. `gh-issue-graph.py`'s `compare_nodes()`
   decides on `(priority, has_estimate, estimate, age, number)` always. The fix
   is the fixed precedence, not abandoning the comparator.
7. **Confirm the fetcher supplies every field your decision needs.** A
   tie-break over a field the fast path never requests degrades to "missing
   sorts last" in production while passing every test that supplies it by hand.
   `linear-graph-analyze.py`'s age tie-break reads `created_at`, which
   `linear-relations.py` does not fetch.
8. **Locate a table column by header name, and fail closed when it is
   absent.** Position drifts, and an assumed default is worse than a refusal.
   `preflight.sh --scout-run-md` prints both `BLOCKS LAUNCH:` and
   `SCOUT VERDICT: no-go` when RUN.md's task table has no `coder` column.
9. **Decide whether the helper is a filter or a validator, and say so in its
   header.** Most scripts here fail closed — exit non-zero on malformed input
   rather than skip it — and print their partial output before dying, so the
   caller sees which item collided (`plan-graph.py` emits its JSON with a
   populated `cycles` list, then exits non-zero). `claim-scan.sh` is
   deliberately a filter: it ignores malformed frontmatter silently, so nothing
   may rely on it to _reject_ a bad file.
10. **A shared test suite that discovers files by glob will pick up your new
    one.** Check the discovery predicate before adding a file that matches the
    name pattern but not the shape. `scripts/test_linear_gql_shape.py` filters
    on `hasattr(mod, "gql")` rather than the `linear-*.py` glob, so a pure
    JSON-in/JSON-out decision asset with no network seam is not imported and
    failed for lacking a function it was never meant to have.
11. **Never byte-diff a generated markdown file.** `dprint` repads a committed
    table's column widths to its final substituted values; a freshly generated
    copy is never repadded to match, so the diff reports drift on every run.
    `scripts/analysis-pipeline/check-reproducibility.sh` collapses runs of
    padding before comparing.
12. **Know whether the helper is per-handler runtime or cross-handler.** The
    runtime scanners are scoped to one handler's data path on purpose and must
    not be unified; a push-time ordering authority is shared and must not be
    re-derived per handler. `scripts/plan-graph.py` is the shared one —
    `/push-plan` runs it for the linear, gh-issue and jira paths via
    `--id-shape`.

## Do not re-propose

Each of these looks extractable and is prose by design. Both prior rounds
recorded them so a third does not spend a reader on them again.

- **research-spike decision promote** (`skills/research-spike/SKILL.md`).
  Promoting a decision into `decisions.md` is reserved to the organizer;
  `scripts/research-spike.py` refuses to write that file and says so.
- **co-review reviewer input assembly** (`skills/co-review/reviewers/*.md`).
  Each reviewer file keeps the literal command so the exact-match permission
  rule can approve it byte-for-byte. Collapsing the five copies into one
  generated command string voids those rules.
- **`/promote-tasks`' scope-fits-size-5 gate** (`commands/promote-tasks.md`).
  Stated in the prose as model judgment, not keywords, and safe there because
  the command is not a blocking gate — a misjudged card lands in
  `needs_refinement`, never lost. Every other HIGH check is already
  deterministic.
- **select-coder and assess-task routing.** Judgment over a curated capability
  matrix. The probing half is already scripted (`scripts/probe-coders.sh`);
  what is left is the scoring.
- **The config wizards** (`commands/task-config.md`, `*-config.md`).
  `AskUserQuestion` flows — interactive by definition.
