# Nightly Linear tidy — PreThink team

A once-a-night hygiene pass over the whole **PreThink** Linear team that keeps
the board honest and under Linear's free-plan **250 active-issue cap**. It is a
thin orchestration of three existing verbs, run in a fixed order, with a
deliberate split between what it **auto-applies** and what it only **reports** —
and a deliberate split of **where** each step runs, so a full-account API key
never enters the cloud sandbox.

This doc is the authoritative playbook. It is driven two ways:

- **Cloud agent routine (detect + sweep).** A scheduled Claude Code session
  fires nightly and runs the two GitHub-dependent steps. In the cloud sandbox
  the only working GitHub path is the GitHub MCP (`gh` isn't installed and raw
  `api.github.com` is not allow-listed through the egress proxy — only Linear
  is), so these steps run in an agent session, not a bare cron. Crucially, both
  are **keyless**: detection reads Linear over the OAuth Linear MCP and checks
  PRs over the GitHub MCP; sweep completes issues over the Linear MCP
  (`save_issue`). Neither needs a personal API key.
- **External backstop (archive).** Archiving is the one step that needs a
  personal API key (the Linear MCP has no archive mutation), so it runs
  **outside** the cloud sandbox — `scripts/nightly-linear-tidy.sh` on your own
  machine or a CI job with a scoped token. See "Security boundary" below.

## The pipeline (order is load-bearing)

| # | Step                  | Verb                                    | Scope                   | Posture                  |
| - | --------------------- | --------------------------------------- | ----------------------- | ------------------------ |
| 1 | Detect false closures | `/find-false-closures` (report)         | per repo-mapped project | **report only**          |
| 2 | Complete merged work  | `/sweep-for-complete --all --apply`     | whole team              | **apply** (keyless, MCP) |
| 3 | Archive terminal work | `/archive-tasks --older-than 3 --apply` | whole team              | **apply** (external)     |

Every mutating verb defaults to a dry run and only writes with `--apply`, so the
apply-posture rows above **must** carry `--apply` — a bare `/sweep-for-complete`
or `/archive-tasks` reports candidates and changes nothing.

**Why this order.** `/find-false-closures` deliberately ignores _archived_
issues — archival is treated as a settled, deeper-vetted state — so it must see
the board **before** step 3 retires anything.

**Why step 1 is report-only.** Restoring a "false closure" un-completes an issue,
and correctness depends on a complete project→repo mapping (a completed issue is
flagged only when _no merged PR in the one repo it was checked against_ owns it).
The PreThink workspace spans several repos, so the nightly run **surfaces**
candidates for a human to restore with an explicit `--apply` (or `--only PRE-N`).
Steps 2 and 3 are safe to automate: step 2 completes an issue only after
independently merge-verifying **that issue's own** linked PR, and step 3 only
touches terminal-state issues past an age threshold (with the archive hold
below).

## Scope: whole-team vs. per-project — per verb

The instinct to "loop over each project" is right for exactly one of the three:

- **Archive → whole-team, no loop.** `linear-archive.py --team PreThink` (no
  `--project`) paginates the _entire_ team in one pass, **including issues with
  no project**. Looping project-by-project would silently miss every unassigned
  issue and is strictly worse.
- **Sweep → `--all` (whole-team), no loop.** Same reasoning; `--all` is the
  whole-team mode and also catches no-project in-flight issues.
- **Find-false-closures → per-project, but only the repo-backed ones.** This
  asset is intrinsically project-scoped (it requires `--project` and has no
  whole-team mode) and needs a `--repo` per project. Do **not** loop all ~37
  projects — only those whose work actually ships through a GitHub repo we can
  read. Pure docs/planning projects have no PR-backed delivery and cannot have
  false closures.

### Project → repo mapping (step 1)

Run `/find-false-closures` once per (project, repo) pair below. Extend as new
repo-backed projects appear. `--project` takes the project UUID (from
`list_projects`); `--repo` is the GitHub `owner/name`.

| Repo                      | Projects (examples)                                                                                                                                                                      |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `bestdan/workflow-skills` | workflow-skills general backlog; workflow-skills: Handler parity follow-ups; reconcile-tasks; autopilot-harness; Auto-pilot mode; Linear MCP token-cost fix; Linear multi-project config |
| `bestdan/finplan`         | Scenarios; FinPlan MCP; State Tax Coverage; Typed Accounts; Snapshots; api alignment; HTTP REST service; Cashflow Modeling; Auth & Billing; (most PRE-* finplan work)                    |
| `bestdan/dotfiles`        | dotfiles                                                                                                                                                                                 |
| `bestdan/papercuts`       | papercuts-related work                                                                                                                                                                   |

**A project that maps to more than one repo must _intersect_, not union, the
per-repo flags.** Each repo run is blind to the others, so an issue delivered by
a merged PR in repo A is still flagged by the independent repo-B run. Unioning
the flagged sets would therefore turn a _complete_ mapping into a false-positive
generator. Aggregate per project and report an issue as a false closure only when
**no** mapped repo supplies an ownership signal — equivalently, intersect each
repo run's flagged set. A project whose work spans repos not in the mapping can't
be judged and should be reported as "unverifiable," never auto-flagged.

## Security boundary — where the key runs

A Linear **personal API key** is a full-account bearer token: anyone holding it
can read and write everything its owner can. This repo's handler docs
(`commands/handlers/linear-sweep-complete.md`,
`commands/handlers/linear-false-closures.md`) state a hard rule — **it must never
enter a claude.ai / Claude Code cloud sandbox.** This runbook keeps that rule:

- **Cloud steps (1–2) are keyless.** Detection reads Linear over the OAuth Linear
  MCP and checks PRs over the GitHub MCP; sweep completes over the Linear MCP.
  No `LINEAR_API_KEY` / `LINEAR_API_KEY_REF` is set in the cloud session, and
  none is needed.
- **The key-requiring step (3) runs outside the sandbox** — `linear-archive.py`
  (the GraphQL `issueArchive` mutation the MCP can't do) on your own machine or a
  CI job, reading the key from `$LINEAR_API_KEY` or a scoped
  `$OP_SERVICE_ACCOUNT_TOKEN` + `op://` ref. This is the "Run it without an
  agent" path in `commands/handlers/linear-archive.md`.

Do not "fix" a keyless cloud run by wiring the key into cloud config — that
re-creates exactly the exposure the boundary exists to prevent.

## Archive hold — never retire an unreviewed candidate

Because `/find-false-closures` excludes archived issues, archiving a completed
issue that was actually a false closure but never reviewed would hide it
permanently, and the `--apply --only PRE-N` recovery path could never reach it.
The archive step therefore observes a **hold**:

1. **Detection first, and it must have succeeded.** Do not archive on a night
   detection was skipped (e.g. GitHub unavailable) — an issue that was never
   scanned must not be retired.
2. **Detection's `--since` window must exceed the archive threshold.** Run
   detection with `--since` ≥ the archive `--older-than` (e.g. detection
   `--since 4d` against archive `--older-than 3`), so every archive-eligible
   issue was scanned at least once before it becomes eligible.
3. **Hold outstanding candidates.** Any id currently on the false-closure
   candidate list (surfaced, not yet reviewed/cleared) is excluded from archive
   until a human restores it (`--apply --only`) or clears it. Feed the night's
   candidate ids to the archive step as an exclusion set.

## Running the cloud steps in an agent session

1. **Preflight.** Confirm the Linear MCP and GitHub MCP both respond
   (`list_teams`, `get_me`). If GitHub is unavailable, **skip steps 1–2** and
   note the skip in the report — and, per the archive hold, the external archive
   must **not** run for that window.
2. **Step 1 — detect false closures (report only).** For each repo-backed
   project, follow `commands/handlers/linear-false-closures.md`'s detection logic
   with the Linear + GitHub MCPs (the shipped `linear-false-closures.py` shells
   out to `gh`, absent in the sandbox, so use the MCP instead). Intersect
   per-repo flags as above. Collect flagged ids into the report — do **not**
   restore.
3. **Step 2 — sweep complete (apply).** Run `/sweep-for-complete --all --apply`
   (`commands/handlers/linear-sweep-complete.md`): scan started-state issues,
   resolve each issue's own linked PR, merge-check it via the GitHub MCP, and
   complete only issues whose own PR merged, over the Linear MCP. Each completion
   is independently verified — never inferred from a sibling.
4. **Report.** One summary: false-closure candidates surfaced (for human review),
   issues completed, and anything skipped (e.g. GitHub unavailable). Hand the
   candidate ids to the external archive as its exclusion set.

## Running the archive step (external backstop)

On your own machine or a CI job — never the cloud sandbox:

```bash
# Dry-run (default): list candidates, change nothing.
scripts/nightly-linear-tidy.sh

# Apply: archive completed + canceled issues older than 3 days. An --apply run
# with no resolvable key/team FAILS (exit 1) so an unattended cron alerts
# instead of recording a false-green no-op.
LINEAR_API_KEY=… scripts/nightly-linear-tidy.sh --apply
```

Gate this on step 1 having succeeded and pass the outstanding candidate ids as an
exclusion set (the archive hold). `scripts/nightly-linear-tidy.sh` is a thin
wrapper over `commands/handlers/assets/linear-archive.py`; it resolves the
key/team the same opt-in way the `test-*-live.sh` harnesses do and archives
completed + canceled issues past the threshold (default 3d).

## Testing

Every step defaults to a dry run and can be exercised read-only against the live
workspace:

```bash
# Archive candidates (completed + canceled > 3d), changes nothing:
scripts/nightly-linear-tidy.sh

# Sweep's scan input (the started-state issues), read-only:
python3 commands/handlers/assets/linear-scan.py --team PreThink --state-type started

# False-closure detection for one project (needs gh + a GitHub token on the host;
# in the cloud sandbox use the GitHub MCP per the detection logic instead):
python3 commands/handlers/assets/linear-false-closures.py \
  --project <uuid> --repo bestdan/workflow-skills --since 4d
```

## Cadence

The cloud detect+sweep routine fires nightly (a fresh agent session per run);
adjust the schedule via the routine's cron. The external archive backstop runs on
its own nightly cron/GitHub Action, gated on the detect step and its candidate
hold, to keep the workspace under the 250-issue cap.
