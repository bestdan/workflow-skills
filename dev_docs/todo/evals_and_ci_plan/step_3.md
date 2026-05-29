← [[evals_and_ci_plan|Overview]]

# Step 3 — Blocking CI workflow

Add the GitHub Actions workflow that runs the deterministic gate on every PR and
push to `main`. It reuses `scripts/check.sh` so CI and local can't diverge.

## Context

- No `.github/` directory exists yet — this is net-new.
- Toolchain the runner needs: `dprint`, the `claude` CLI, Python 3 (preinstalled
  on `ubuntu-latest`). `just` is **not** needed — CI calls `scripts/check.sh`
  directly.
- Minimal third-party actions allowed (per implementation decision):
  - `actions/checkout`
  - `dprint/check` (official; auto-discovers `dprint.json`) — or install dprint
    via its install script if we'd rather run it inside `check.sh`.
  - `lycheeverse/lychee-action` for link integrity (relative links between
    SKILL.md and bundled reference files; the analysis-pipeline `example/`).
- `claude` CLI install: `npm install -g @anthropic-ai/claude-code`. **Verify on
  the first CI run** whether `claude plugin validate` runs without auth in a
  headless runner (acceptable risk — confirm during step 3, don't pre-solve). If
  the first run shows it needs auth, the fallback is to move manifest-schema
  validation into `scripts/validate.py` and drop the CLI step from CI. Add a
  one-line note in `ci.yml` near the validate step flagging this so it's checked.

## Changes

1. **`.github/workflows/ci.yml`**:
   - Triggers: `pull_request` and `push` to `main`.
   - `permissions: contents: read` (least privilege).
   - Single `lint` job on `ubuntu-latest`:
     - `actions/checkout`
     - install `claude` CLI (npm) — or skip if auth-blocked (see above)
     - `dprint/check@v2` (or install dprint, then let `check.sh` run it — pick
       one path so dprint isn't run twice)
     - run `scripts/check.sh` (the deterministic gate: dprint + plugin validate
       + `validate.py`)
   - Separate `links` job (or step) using `lycheeverse/lychee-action` with
     `args: --no-progress --offline './**/*.md'` for internal links; if external
     link checking is wanted, run it non-`--offline` but `continue-on-error` so
     flaky external hosts don't block PRs.
2. **Decide dprint ownership:** either `check.sh` runs `dprint check` (then CI
   just needs dprint installed) **or** the `dprint/check` action runs it (then
   `check.sh`'s dprint step is skipped in CI via an env guard). Document the
   choice in `check.sh`'s header. Prefer: `check.sh` owns it, CI installs dprint
   via the official install script — keeps one code path.
3. **Pin versions** of every action (`@vX.Y.Z` or SHA) for reproducibility.

## Acceptance

**Code-enforced:**

- CI workflow runs on a PR and the `lint` job passes on the green repo.
- A deliberately-broken PR (e.g. unformatted file, or empty SKILL description)
  turns the check **red** — confirm the gate actually blocks.
- `mpalmer/action-validator` (run locally, one-off) or `actionlint` reports the
  workflow YAML as valid (optional belt-and-suspenders; not a committed check).

**User-run:**

- Open a draft PR from this branch and confirm the check appears and is green.
- **Check the auth question on the first CI run:** verify in the Actions log
  that `claude plugin validate` actually ran headless. If it's auth-blocked,
  apply the documented fallback (move manifest-schema checks into
  `validate.py`, drop the CLI step).
- Set the check as a **required status check** in branch protection for `main`
  (GitHub repo setting — must be done in the UI/API after first green run).

## Dependencies

[[step_2]] — CI invokes `scripts/check.sh`, which must exist and be green first.
