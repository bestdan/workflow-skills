# Run decisions log — cao_usage autonomous delivery

Technical calls made during the unattended run. Escalations go to Codex
`gpt-5.6-sol` (high) or Fable; each entry records who decided and why.
Graduates into `dev_docs/cao_usage.md` via task 6.

## Run configuration (user-approved at preflight, 2026-07-17)

- Orchestrator: Claude (this session), self-paced loop; usage gate at 90%
  consumed (session OR weekly), sleep-till-reset via chained hourly wakeups.
- Coder: codex `gpt-5.6-terra` (default model) via `codex exec --sandbox
  workspace-write` in per-task worktrees. Coders do not commit; the
  orchestrator harvests the diff.
- Review: `/co-review --local --non-interactive` in the task worktree
  (Claude subagent, codex as local reviewer), then commit → push → stacked PR.
- PR structure: stacked (t2 base = t1 branch, t4 base = t3 branch); others
  base = `bestdan/cao-usage-adapter`. Nothing merges unattended; tasks stop at
  `needs_review`. Task 6 parked for the user.
- cao-server: started by the orchestrator at launch (healthy at :9889).

## Decisions

1. **t5 serialized behind t3** (orchestrator, no escalation). t5 has no
   dependency edge but edits the same files (`scripts/claude-usage.sh`,
   `run-budget.md`) as t3; running them in parallel guarantees conflicts.
   t5 will branch off t3's branch (stacked) once t3 completes.
2. **Wrapper location `scripts/cao-coder`** (orchestrator, per repo
   convention that shipped artifacts live in-repo, not `~/.local/bin`).
   Baked into t1's packet spec.
3. **t1 co-review, fixed unattended** (reviewer consensus, no escalation):
   bare-repo guard in `scripts/cao-coder` (`rev-parse --is-inside-work-tree`
   must print `true`, not just exit 0) — found by codex AND devin; leftover
   `.packet-spec.md` deleted. Rejected as false positive: devin's
   command-injection claim on the `exec cao-run` line (all args quoted).
4. **t1 lint gate + tests → RESOLVED by codex gpt-5.6-sol high.**
   (Q1) Rename `scripts/cao-coder` → `scripts/cao-coder.sh` so the existing
   `*.sh` glob in lint-shell.sh discovers it; update the .coders.yml template,
   select-coder pointer, bats tests, and usage text in lockstep; do NOT
   broaden shared lint infra. (Q2) Add all four missing test cases (bare-repo
   worktree, missing spec, malformed backend:model, cao-run absent from an
   isolated PATH); tighten fleet-rejection to `assert_failure 1`; and fix an
   exit-code inconsistency sol spotted: missing-spec and non-worktree
   branches use `die` (exit 1) but are invalid caller inputs — the script's
   own contract says exit 2. Corrected to exit 2 with tests enforcing it.
6. **Wave 2 stacking** (orchestrator, no escalation): t2 stacks on t1's
   branch (PR #224) per the approved plan; t2 and t3 both touch
   `skills/auto-pilot/SKILL.md`/`run-state.md` in different sections —
   potential textual conflicts left to merge time rather than serializing
   the whole plan. t4 will stack on t5 (not directly on t3): both edit
   `scripts/claude-usage.sh` + run-budget docs, so t3 → t5 → t4 is the
   conflict-free chain even though the plan graph only records t4←t3.
7. **Orchestrator error + correction (t2 stacking).** t2's packet spec told
   the coder the sibling `--run-state` work (t3) was already present, but t2
   was stacked on t1 only — the coder re-invented `--run-state` in
   deliver-task/auto-pilot docs with divergent wording and step renumbering,
   guaranteeing semantic merge conflicts with PR #225. Correction: restack
   into ONE linear chain adapter → t1 → t3 → t5 → t2 → t4 (t3 rebases onto
   t1 — disjoint files, clean; PR #225 base retargets to the t1 branch),
   and a follow-up packet has the t2 coder unify the two --run-state
   definitions into one contract (t3's reserve-gate text is the base; the
   profile fields layer on top — both read the same RUN.md front matter).
   Force-pushes are safe: overnight, nothing reviewed yet.
8. **t5 co-review, fixed unattended** (reviewer consensus): stale-fixture
   breakage in t3's reserve bats (past resets_at now trips validation) fixed
   to dynamic epochs; state-file corruption wedge (one unreadable state file
   permanently forced the 1h fail-safe and was never rewritten) fixed to
   treat unreadable prior state as no-prior-state.
9. **t5 session-status contract → RESOLVED (b)+(a) by codex gpt-5.6-sol
   high.** The coder had --session-status emit a grace-adjusted pause epoch
   (or a rolling now+3600 fail-safe) wire-indistinguishable from a real
   reset — which would have corrupted task 4's window-identity tagging and
   double-buffered claude-auto-resume's wait. Ruling: keep the script a pure
   fail-closed READER — emit raw validated reset_epoch, exit nonzero with no
   stdout on implausible/unpersistable resets; grace (+ the 1h fallback on
   failed reads) applies at the pause WRITER (deliver-task gate /
   run-budget near-cap path), written atomically with pause_observed_at +
   pause_source; task-4 packet must state only successful validated readings
   are samples (nonzero read clears the baseline). claude-auto-resume.sh
   needs no change — raw epochs restore its single CAR_BUFFER semantics.
   "stderr must never be treated as a machine interface."
10. **t2 restack + reconciliation → clean** (co-review confirmed criteria
    a–d: no duplicate --run-state/reserve-gate/step-numbering vs t3/t5,
    reserve ordering preserved with profile layered after, default path
    byte-unchanged). t2 was reset onto the t5 chain tip and the less-claude
    profile re-applied to COMPOSE with the merged contract (single arg,
    single gate, profile fields alongside). Fixed in review: missing
    `[--profile less-claude]` on a restated invocation line; scratch files
    removed.
11. **t2 select-coder legibility → decided by orchestrator (no escalation).**
    Co-review's lone judgment call was a style/robustness call the reviewer
    itself deemed technically sufficient — not a technical fork for sol.
    Applied the belt-and-suspenders wiring anyway because it guards the exact
    failure --cao-fleet exists to prevent: (a) Selection step 2 now restricts
    candidates to codex/agy before scoring under --cao-fleet; (b) the
    containment gate carries a --cao-fleet exception so a CAO-dispatched agy
    (isolated in CAO's worktree) isn't wrongly dropped, which would collapse
    the fleet to codex-only.

## Post-build smoke test (full stack assembled, user present)

Assembled all five commits on one tree (worktree at the t4 tip) and ran the
deterministic + contract + one live tier.

- **Deterministic tier — clean.** Full bats suite 50/50, `scripts/check.sh` OK
  (727 spawn-orchestrator assertions + confinement/settings/status-report),
  shell lint OK. One emergent failure found + fixed: `dprint check` flagged
  `skills/select-coder/SKILL.md` (orchestrator's hand-edit used `*before*`
  where repo style is `_before_`) — coders ran sandboxed without dprint, so
  formatting was never normalized on any branch and only surfaced assembled.
  Fixed via `dprint fmt`, amended into t2.
- **Contract tier — clean.** The t5 `--session-status` raw-`reset_epoch`
  contract is consumed consistently by all three consumers: the reserve gate
  (t3, applies grace writer-side only), the usage_deltas instrumentation (t4,
  tags by raw epoch), and claude-auto-resume.sh (single CAR_BUFFER on the raw
  epoch — no double-buffer). This is the cross-task property sol's t5 ruling
  was designed to protect, verified on the assembled tree.
- **Live tier (Tier 1) — CAO dispatch PASSED, t1's deferred user-run AC now
  satisfied.** `scripts/cao-coder.sh <spec> <worktree> codex:gpt-5.6-terra`
  against a real pre-made worktree launched a live codex worker (base
  dev-codex) that created SMOKE.txt with exact content — Claude wrote none of
  it — harvested the diff, exit 0, and **created no rogue `cao/` worktree**
  (the never-`git worktree add` contract held live).

### Live-tier findings

10. **t1 gap — `CAO_ENABLE_WORKING_DIRECTORY=true` was undocumented (FIXED).**
    cao-run only honors the caller-owned worktree if the daemon was started
    with this env var exported; without it the worker edits elsewhere and the
    wrapper harvests an empty diff even on success. t1 documented "cao-server
    must be running" but not *how*. Added the prerequisite to
    orchestrate-coders SKILL.md's runtime-prerequisite paragraph; amended into
    t1 (#224) and the whole chain restacked onto it (all conflict-free).
11. **cao-run reliability caveat (pre-existing, NOT this change — logged for
    the autopilot follow-up).** cao-run logged `Error: Timed out after 300s
    waiting for terminal` yet the task succeeded (file present, exit 0): its
    worker-completion detection is unreliable and falls through to diff-harvest.
    Implications for an unattended less-claude run: (a) a *trivial* CAO task
    took ≥300s wall-clock, so per-task CAO timeouts must be sized well above
    5 min or every dispatch looks like a hang; (b) the orchestrator must not
    read cao-run's "Timed out" stderr as failure — the diff is ground truth.
    t1/orchestrate-coders already treat the diff as ground truth, which is
    precisely why this succeeded despite the bogus timeout — the smoke test
    validated that design principle rather than breaking it.
5. **t3 gate boundaries → RESOLVED (b) by codex gpt-5.6-sol high.** The
   documented claim/verify/co-review gates cannot be enforced from the outer
   loop because /deliver-task is opaque (found by codex + devin in co-review).
   Ruling: keep the AC, extend /deliver-task — add an optional
   `--run-state <RUN.md>` arg (auto-pilot-only); when supplied,
   /deliver-task reads the persisted reserve, caches one usage reading per
   cycle, and consults it before claim, verify, co-review, and every Step-6
   re-verify/re-review; below reserve → the existing near-cap checkpoint
   path, not a delivery failure. Standalone /deliver-task unchanged.
   run-budget.md names skills/deliver-task/SKILL.md as the enforcement site;
   the bats test asserts the gates there. Scope expansion recorded in the
   task file's related_files. Caveat kept: single cached snapshot per cycle
   (freshness revisits in task 4). Rationale: "documenting unenforced
   boundaries is worse than a narrowly justified scope expansion."
