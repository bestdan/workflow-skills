# Nightly Linear tidy — PreThink team

A once-a-night hygiene pass over the whole **PreThink** Linear team that keeps
the board honest and under Linear's free-plan **250 active-issue cap**. It is a
thin orchestration of three existing verbs, run in a fixed order, with a
deliberate split between what it **auto-applies** and what it only **reports**.

This doc is the authoritative playbook. It is driven two ways:

- **Primary — a scheduled Claude Code routine** (agent session, fires nightly).
  The agent needs GitHub for the PR merge-checks, and in the cloud sandbox the
  **only** working GitHub path is the GitHub MCP (`gh` isn't installed and raw
  `api.github.com` is not allow-listed through the egress proxy — only Linear
  is). That is why the GitHub-dependent steps run **in an agent session**, not a
  bare cron.
- **Backstop — `scripts/nightly-linear-tidy.sh`** covers the one fully
  GitHub-free step (archive) as a plain key-only script you can cron anywhere.

## The pipeline (order is load-bearing)

| # | Step | Verb | Scope | Posture |
|---|------|------|-------|---------|
| 1 | Detect false closures | `/find-false-closures` | per repo-mapped project | **report only** |
| 2 | Complete merged work | `/sweep-for-complete --all` | whole team | **apply** |
| 3 | Archive terminal work | `/archive-tasks --older-than 3` | whole team | **apply** |

**Why this order.** `/find-false-closures` deliberately ignores *archived*
issues — archival is treated as a settled, deeper-vetted state — so it must see
the board **before** step 3 retires anything. Even though step 1 is report-only
(nothing is lost either way), keeping archive last means the nightly report
reflects the pre-archive board.

**Why step 1 is report-only.** Restoring a "false closure" un-completes an issue,
and correctness depends on a complete project→repo mapping (a completed issue is
flagged only when *no merged PR in the one repo it was checked against* owns it).
The PreThink workspace spans several repos, so an incomplete mapping would
produce false positives, and auto-un-closing on a false positive is a surprising,
hard-to-notice mutation. The nightly run therefore **surfaces** candidates for a
human to restore with an explicit `--apply` (or `--only PRE-N`). Steps 2 and 3
are safe to automate: step 2 completes an issue only after independently
merge-verifying **that issue's own** linked PR, and step 3 only touches
terminal-state issues past an age threshold.

## Scope: whole-team vs. per-project — per verb

The instinct to "loop over each project" is right for exactly one of the three:

- **Archive → whole-team, no loop.** `linear-archive.py --team PreThink` (no
  `--project`) paginates the *entire* team in one pass, **including issues with
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

| Repo | Projects (examples) |
|------|---------------------|
| `bestdan/workflow-skills` | workflow-skills general backlog; workflow-skills: Handler parity follow-ups; reconcile-tasks; autopilot-harness; Auto-pilot mode; Linear MCP token-cost fix; Linear multi-project config |
| `bestdan/finplan` | Scenarios; FinPlan MCP; State Tax Coverage; Typed Accounts; Snapshots; api alignment; HTTP REST service; Cashflow Modeling; Auth & Billing; (most PRE-* finplan work) |
| `bestdan/dotfiles` | dotfiles |
| `bestdan/papercuts` | papercuts-related work |

A project whose issues span more than one repo needs a run per repo (or the
widest repo whose merged PRs cover it) — see
`commands/handlers/linear-false-closures.md`.

## Auth & prerequisites

- **`LINEAR_API_KEY`** — a Linear **personal API key** (full-account bearer
  token). Required by every step's GraphQL asset (`linear-scan.py`,
  `linear-archive.py`, `linear-false-closures.py`). `api.linear.app` is
  allow-listed through the egress proxy.
  > Security note: this key is a full-account token living in a cloud sandbox —
  > the handler docs warn against exactly this. It is an informed opt-in for this
  > routine. Keep it rotatable; scope down if Linear ever supports it.
- **GitHub** — needed only by steps 1 and 2 (PR merge-checks). In the cloud
  sandbox the working path is the **GitHub MCP** (`mcp__github__*`), available to
  the agent, **not** a standalone script. `gh` is not installed and raw
  `api.github.com` is not reachable, so `linear-false-closures.py`'s `gh api`
  calls fail here — in-session, use the GitHub MCP to resolve merged PRs instead.
  No separate GitHub token is required beyond what the MCP already provides.
- **Archive needs no GitHub at all** — `LINEAR_API_KEY` only.

## Running it in an agent session (the routine's playbook)

1. **Preflight.** Confirm `LINEAR_API_KEY` is present and the GitHub MCP responds
   (`get_me`). If GitHub is unavailable, **skip steps 1–2** and run only step 3
   (archive), noting the skip in the report — never fail the whole run.
2. **Step 1 — detect false closures (report only).** For each (project, repo)
   pair, run `linear-false-closures.py --project <uuid> --repo <owner/name>
   --since 2d` in **dry-run** (no `--apply`). In this sandbox its `gh` calls
   fail; instead follow `commands/handlers/linear-false-closures.md`'s detection
   logic with the GitHub MCP standing in for the merged-PR lookup. Collect every
   flagged id into the report — do **not** restore.
3. **Step 2 — sweep complete (apply).** Run `/sweep-for-complete --all`
   (`commands/handlers/linear-sweep-complete.md`): scan started-state issues
   (`linear-scan.py --team PreThink --state-type started`), resolve each issue's
   own linked PR, merge-check it via the GitHub MCP, and complete only the
   issues whose own PR merged. Each completion is independently verified — never
   inferred from a sibling.
4. **Step 3 — archive (apply).** Run `scripts/nightly-linear-tidy.sh --apply`
   (or `linear-archive.py --team PreThink --older-than 3 --include-canceled
   --apply`) to retire completed **and** canceled issues older than 3 days.
5. **Report.** One summary: false-closure candidates surfaced (for human
   review), issues completed, issues archived, and anything skipped (e.g. GitHub
   unavailable). Keep it short.

## Testing

Every step defaults to a dry run and can be exercised read-only against the live
workspace:

```bash
# Archive candidates (completed + canceled > 3d), changes nothing:
scripts/nightly-linear-tidy.sh                       # dry-run is the default
LINEAR_API_KEY=… scripts/nightly-linear-tidy.sh      # explicit key

# Sweep's scan input (the started-state issues), read-only:
python3 commands/handlers/assets/linear-scan.py --team PreThink --state-type started

# False-closure detection for one project (needs gh + a GitHub token on the host):
python3 commands/handlers/assets/linear-false-closures.py \
  --project <uuid> --repo bestdan/workflow-skills --since 2d
```

## Cadence

The routine fires nightly (a fresh agent session per run). Adjust the schedule
via the routine's cron. The GitHub-free archive step can additionally run as a
standalone cron/GitHub Action off `scripts/nightly-linear-tidy.sh` for
belt-and-suspenders coverage of the 250-issue cap.
