# Linear shared scan & fast-path/floor gate — architecture notes

Rationale for how the Linear handlers share their GraphQL-fast-path / MCP-floor
plumbing. This is the **why**; the operational **how** lives in the handler files
themselves (linked below). Written after the PRE-590 `linear_shared_scan` refactor
(PRs up to #256).

## The problem it solved

Four Linear handlers each independently re-specified the same "try the GraphQL
fast-path, else fall back to the MCP floor" gate, including a full copy of the
**security-boundary** callout: `linear-claim.md`, `linear-sweep-complete.md`,
`linear-reconcile.md` (row 2), and `linear-reoptimize.md`. Four copies of a
load-bearing safety rule drift; the refactor collapsed them to one.

## The canonical gate — and its deliberately narrow contract

The single source is **`linear-common.md` → "Fast-path / MCP-floor gate (and the
security boundary)"**. Every consumer references it and passes only **which
script** it drives (`linear-ready.py` / `linear-scan.py` / `linear-relations.py`).

The gate owns **only what is identical across all consumers**:

- the failure mechanism (script non-zero exit / unparseable stdout → one debug
  line → MCP floor);
- the rule that there is **no** independent `[ -n "$LINEAR_API_KEY" ]` pre-check —
  the script's own non-zero exit _is_ the gate (so the headless
  `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF` case still attempts the fast
  path);
- the **security boundary**: the account key is a full-account bearer token that
  must never reach a cloud sandbox; cloud sessions set no key, so the script exits
  non-zero before any request and the run floors by design.

**Why the gate stops there — the load-bearing boundary.** It must **not** absorb
each consumer's **fast-path eligibility** (which searches attempt the fast path)
or **fallback granularity** (what unit falls to the floor). Those genuinely differ,
and folding them into the shared block would silently misdescribe real behavior —
which is exactly the bug co-review caught on #256 before merge. Keep them local:

- **`linear-claim.md` (ranked claim search)** — only the ranked search attempts the
  fast path; the direct-identifier path always stays on MCP; a scope the fast path
  can't serve (Unassigned, a `--project` pin) floors the **whole search**.
- **`linear-reconcile.md` row 2 (in-flight scan)** — invokes `linear-scan.py`
  **once per resolved scope**, so a failure floors **only that scope**.
- **`linear-sweep-complete.md` (in-flight scan)** — **batches all configured
  projects into one `linear-scan.py` call**. `linear-scan.py` exits non-zero as a
  whole on any scope's failure (no per-scope isolation), so a failure floors the
  **entire configured-project batch**, not one scope.
- **`linear-reoptimize.md` (relation-graph load)** — one whole-graph pass, so any
  failure floors the **whole load**.

The reconcile-vs-sweep split is the subtle one: same "In-flight scan" read, but
**different fallback units** purely because one calls per-scope and the other
batches. The shared doc names both explicitly rather than claiming a uniform
"per-scope" fallback (the pre-merge overclaim).

## The shared read: "In-flight scan"

`linear-common.md` → "In-flight scan" is the single source for the state-scoped
read used by the sweep/reconcile verbs. It is **parameterized by state-type**
(sweep → `started`; reconcile row 2 → `backlog` + `unstarted`) and carries the
concrete `linear-scan.py` **Fast-path invocation** (repeatable `--project`,
repeatable `--state-type`; batching left to the consumer, since the script unions
either way). `linear-reconcile.md` row 2 delegates its scan here instead of
restating it — the way row 1 already delegates wholesale to `linear-sweep-complete.md`.

## Two things deliberately NOT unified

1. **`linear-claim.md`'s candidate selection stays separate.** "Find candidates"
   is a _different_ read — ranked, filtered (estimate/label/assignee), driven
   by `linear-ready.py`, and it uses the **claim variant** of the Unassigned
   bucket. It shares only the gate; its selection was never folded into "In-flight
   scan."

2. **The two Unassigned-bucket variants must stay distinct** (`linear-common.md` →
   "The Unassigned bucket"):
   - **Claim variant** (`/do-tasks`) — membership is "no project **or** in a
     project outside the configured set." A wide catch-all is a _feature_ there
     (pick up unfiled team work).
   - **Sweep/reconcile variant** (`/sweep-for-complete`, `/reconcile-tasks`) —
     membership is `projectId == null` **only**. These verbs are
     destructive-adjacent (complete / move / demote), so the wide catch-all would
     pull every unrelated project's in-flight work into a scheduled `--apply`
     run's blast radius. The narrower rule is the guard; `--all` is the explicit
     escape hatch.

   Flattening these would silently widen a destructive scope — never do it.

## One more preserved divergence (row 2 fast path)

`linear-reconcile.md` row 2's fast path resolves PRs from **attachments only**: an
open PR discoverable _solely_ by `[<id>]` title-match or `branchName` is
deliberately missed until that scope floors. This _differs_ from
`linear-sweep-complete.md`'s fast path, which still applies the title/branch
fallbacks. The #256 refactor preserved row 2's behavior rather than silently
aligning it to sweep — noted here so a future maintainer doesn't "fix" the
apparent inconsistency without realizing it's intentional (or, if they do want to
align them, does so as a deliberate behavioral change).

## Follow-ups noted, not done

- **Sweep's whole-batch floor** means one scope's transient Linear GraphQL failure
  floors the _entire_ configured-project sweep to MCP (the scan hits only Linear
  GraphQL; GitHub PR resolution is a separate downstream step). Pre-existing behavior, now
  documented honestly. If per-scope degradation (like reconcile) is wanted, that's
  a deliberate change to `linear-sweep-complete.md`'s invocation, not a doc fix.

## Source of truth

The handler files are authoritative; this note only explains the boundaries.

- `commands/handlers/linear-common.md` — the canonical gate, "In-flight scan", the
  Unassigned-bucket variants.
- `commands/handlers/linear-claim.md`, `linear-sweep-complete.md`,
  `linear-reconcile.md`, `linear-reoptimize.md` — the four consumers.
