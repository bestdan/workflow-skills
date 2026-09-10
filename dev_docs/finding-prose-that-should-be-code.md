# Finding prose that should be code

How to spot prose in this repo that should be a helper script, how to rank
what you find, and the mistakes that have already been paid for. The rule
itself, and where an extracted helper goes, live in
[CONTRIBUTING.md](../CONTRIBUTING.md#logic-goes-in-a-typed-file); this file is
only about finding and ranking the candidates. Candidates already found and not
yet extracted are filed as issues.

## The smell

Nearly everything this plugin ships is markdown an agent reads and re-executes
by reasoning. Prose is right for judgment — routing, scope assessment,
inference over ambiguous input. It is wrong for computation the agent has to
re-derive identically on every invocation: each re-derivation costs context and
is a fresh chance to apply the rule slightly differently from last time. A
fenced block is worse again, because nothing in the gate can see it.

## How to find it

Read the files in front of you in full against the rubric below — the files
in a PR when reviewing one, a handler and its siblings when editing one, the
whole tree only for a deliberate audit. A grep for computation-shaped language
finds the fenced blocks and misses the other six categories.

The middle column is the **mistake**: what the prose looked like before it was
extracted. None of these survive in the tree, so they are quoted here. The
right column is what replaced each one.

| Category                                        | The mistake, as the prose read before extraction                                                                                                             | What replaced it                                                                |
| ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------- |
| **Hand-walked algorithms**                      | _"Detect cycles, then topologically sort the non-terminal nodes with an urgency → smaller-estimate → age tie-break, then sweep every edge for an inversion"_ | `commands/handlers/assets/linear-graph-analyze.py`, one call, three JSON fields |
| **Fenced blocks with logic**                    | A 36-line `bash` fence paginating GraphQL with a `jq` cursor loop, in `gh-issue-promote.md` — seven defects found in it, none by the gate                    | `commands/handlers/assets/gh-issue-rollups.py` with a test per defect           |
| **Decision tables**                             | Three near-identical seven-row tables mapping state plus labels to a kanban section, one per tracker handler, each applied by hand with a precedence rule    | `commands/handlers/assets/kanban-classify.py` with a per-tracker field map      |
| **API choreography**                            | _"Try the links attachment; if none, search PR titles for the identifier; if none, list PRs by branch; then classify by merge state"_                        | `commands/handlers/assets/linear-pr-resolve.py`, which runs the probes itself   |
| **Rules that could be hooks or lints**          | _"Before the final `rm -rf`, confirm `$WORK` is non-empty, absolute, and not inside the learner's repo"_ — a check the reader was trusted to perform         | `scripts/tutorial-root-guard.sh`, exit 1 on any failed predicate                |
| **Prose re-deriving a shipped script**          | A handler walking the archive sweep query by query, ending with "the script does the same thing"                                                             | A pointer to `linear-archive.py`; the walk is deleted                           |
| **The same procedure restated across handlers** | The Jira transition-id lookup written out five times, in two diverging variants, one of which filtered cancel-style names after counting instead of before   | `commands/handlers/assets/jira-resolve-transition.py`, called from all five     |

**Re-check every absence claim before you rank it.** "No script computes this"
is what makes a finding a finding, and it is the claim a reader is most likely
to get wrong — inference cannot tell "not there" from "not where I looked". Run
`rg` against `scripts/` and `commands/handlers/assets/` for the concept, not
only for the filename you expect, and record that you did. Several past
findings landed as an extra flag or subcommand on an existing asset rather than
a new script, which is the outcome this step exists to produce.

## How to rank

Five weights, in this order:

1. **An explicit prior defect in the prose.** A dated incident, a quoted wrong
   result, or a warning the prose gives itself. This dominates everything else:
   it is evidence the hand-walk already failed once.
2. **Confidence the logic is deterministic.** If two careful readers could
   legitimately disagree on an output, it is judgment and stays prose.
3. **Call-site count.** Every copy is an independent chance to break the rule.
4. **How hot the path is.** A hand-walk that runs on every invocation, or once
   per issue over a whole board, costs context and wall-clock every time; the
   same logic in a script runs in milliseconds and costs the agent one tool
   call. A rarely-run procedure can wait.
5. **Size.** `S` under 50 lines, `M` under 200, `L` above. Small and duplicated
   beats large and singular.

Sequence the first slice by (1) and (5) together — a small finding with a dated
defect is the cheapest proof that the round is worth running.

## The one standing constraint

**A script can own the decision over fetched JSON, never an MCP fetch and
never a tracker write.** MCP tools (`mcp__linear__*`, `mcp__atlassian__*`) are
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

Each of these cost a review round or a production defect once. Every entry is
the **rule** in bold, then the **mistake** that produced it, then the file where
the **correct form** lives today — that file's header carries the specifics.
The mistakes are described rather than linked because none of them survive in
the tree.

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
