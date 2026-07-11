# Linear read fast-paths — design

A family of read-only GraphQL scripts that replace an MCP fan-out with one
(paginated) query per scope for a specific, named Linear read. This doc is the
durable **why** and **how to extend** — the per-command wiring lives in each
consumer's own `.md` file and is not restated here. Read this before adding a
fifth fast-path.

## The family

| Script                                                                                        | Read                                                                         | Consumer(s)                                                                                    | `description`?            | Field philosophy                                                                                                                                                                                                                                        |
| --------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`commands/handlers/assets/linear-ready.py`](../commands/handlers/assets/linear-ready.py)     | Ready-candidate selection (find-candidates)                                  | `/do-tasks` tracker path (`linear-claim.md` "Find candidates")                                 | No                        | Skinny — just enough to gate + rank + branch: `id identifier title priority estimate updatedAt branchName url assignee labels state project`                                                                                                            |
| [`commands/handlers/assets/linear-scan.py`](../commands/handlers/assets/linear-scan.py)       | In-flight scan (state + PR attachment)                                       | `/sweep-for-complete`, `/reconcile-tasks` row 2 (both via `linear-common.md` "In-flight scan") | No                        | Skinniest — `id identifier title url state { id type } attachments { nodes { url } }`; PR resolution and merge-checking are a separate downstream step                                                                                                  |
| `commands/handlers/assets/linear-relations.py` (in-review, not yet on `main`)                 | Relations load (native `blockedBy`/`blocks`/`relatedTo`/`duplicateOf` edges) | `/reoptimize-tasks` "Load — build the graph"                                                   | **Yes** (plus `estimate`) | Richer — reoptimize runs rarely and legitimately needs body text for dedup/overlap judgment, so it trades the skinny-fields default for a payload that answers the graph-building question in one shot                                                  |
| [`commands/handlers/assets/linear-archive.py`](../commands/handlers/assets/linear-archive.py) | Terminal-state issue archive (+ `issueArchive` mutation)                     | `/archive-tasks`                                                                               | No                        | Origin of the shared `get_key()`/`gql()` helpers every sibling script reuses verbatim; also the one family member that mutates (the Linear MCP exposes no archive mutation, so this is a GraphQL-only backstop, not a read fast-path with an MCP floor) |

Each script is a **paginated GraphQL query per scope** (a project, or the
whole team when no `--project` is given) instead of a multi-call MCP fan-out
(`list_teams` → `list_workflow_states` → `list_issues` per scope → `get_user`
→ lazy `get_issue` for missing fields). The `description` column is the one
axis that varies by design, not oversight: the read gets exactly the fields
its consumer needs and no more, because Linear's GraphQL lets you ask for
precisely that, and a fatter payload has a real cost (token budget, request
size) with no consumer to justify it.

## The host-gated try-script-then-floor pattern

Every fast-path consumer wires the same shape, described once here rather
than in each `.md` file:

1. If `Bash` is available, attempt the script first.
2. On **any** non-zero exit, or stdout that doesn't parse as the script's
   documented JSON object, log one debug line
   (`Fast-path unavailable (<reason>) — falling back to MCP floor.`) and run
   the MCP floor instead.
3. A host with no `Bash` tool falls to the floor by construction — nothing to
   gate.

**The fallback IS the gate.** There is no separate
`[ -n "$LINEAR_API_KEY" ]` pre-check anywhere in the wiring. Each script's
`get_key()` (see below) exits fast and non-zero the moment no key resolves,
before any network call — so "no key" and "GraphQL error" and "team not
found" all collapse into the same one signal the caller already has to
handle: non-zero exit. An explicit env pre-check would actively misgate the
headless case where `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF` are
set but `$LINEAR_API_KEY` itself is not — that case must still _attempt_ the
fast path and let the script resolve the key. Cloud sandboxes (`claude.ai` /
Claude Code cloud) never set either variable, so they hit the same non-zero
exit and fall to the floor **by construction**, not by any cloud-detection
logic — see the security boundary below.

The two paths are provably equivalent by shared spec, not by coincidence:
each script and its MCP-floor sibling both implement the same
`linear-common.md` block (see next section), so a caller never needs to
reconcile "what did the fast path pick" against "what would the floor have
picked" — they're defined to be the same selection.

## The shared read specs (`linear-common.md`)

Two blocks in [`commands/handlers/linear-common.md`](../commands/handlers/linear-common.md)
are the single source of truth for a read that has **more than one
implementation to keep in lockstep**:

- **"Ready-candidate selection"** — the state scope (`unstarted`-type only),
  the gate table (estimate, three exclusion labels, assignee), and the rank
  order (priority urgent→low, none(0) last, then `updatedAt` ascending).
  Implemented identically by `linear-ready.py`'s `gate()`/`rank_key()` and by
  `linear-claim.md`'s MCP-floor steps 5–6.
- **"In-flight scan"** — the parameterized state-type scope (callers name
  which type set applies: `started` for the merged→Done sweep, `backlog` +
  `unstarted` for reconcile row 2), the skinny field list (explicitly _no_
  `description`), and the scope-resolution/pagination rules. Implemented
  identically by `linear-scan.py` and by `linear-sweep-complete.md` /
  `linear-reconcile.md`'s MCP floors.

**Lockstep rule.** Each block ends with an explicit instruction: change the
spec here first, then update every consumer — the script _and_ every `.md`
file that reads it — in the same change. The spec is not descriptive
documentation of what the scripts happen to do; it is the contract the
scripts and the MCP floors are both required to satisfy, so a single spec
edit is the only way to change the read without the two paths silently
diverging.

What triggers a block is **more than one implementation of the same read that
must stay in lockstep — not a consumer count.** That happens two ways: (a)
2+ consumer commands share the identical read (the "In-flight scan" case —
sweep and reconcile row 2), or (b) a **single** consumer has both a fast-path
script and an MCP floor whose selection rules are subtle enough to drift apart
(the "Ready-candidate selection" case — one consumer, `/do-tasks`, but the
gate + rank rules must match exactly between `linear-ready.py` and the
`linear-claim.md` floor, so the spec pins both). A read whose script and its
single consumer's prose are simple enough to eyeball together — relations load
— can skip the block; add one the moment a second consumer, or a
subtle-enough script/floor split, makes silent drift a real risk.

## The key / security boundary

A Linear **personal API key** (what `linear.api_key_ref` in the handler
config points at) is a **full-account bearer token** — anyone holding it can
read and write everything the key's owner can in Linear. This is the reason
every script in the family resolves it the same narrow way and every
consumer treats the resolution itself as the security gate, not an add-on
check.

**Resolution order** (`get_key()`, defined once in `linear-archive.py` and
reused verbatim by every sibling):

1. `$LINEAR_API_KEY` — a raw key already in the environment.
2. `op read "$LINEAR_API_KEY_REF"` — a full `op://vault/item/field`
   reference, resolved via the 1Password CLI. This only works
   non-interactively when `op` is signed in an authorized terminal, or
   `$OP_SERVICE_ACCOUNT_TOKEN` is set.

Neither source is ever a literal key in a config file — `linear.api_key_ref`
holds an `op://` reference, never a raw secret.

**Never in a cloud sandbox.** `claude.ai` / Claude Code cloud sessions never
set `$LINEAR_API_KEY` or `$LINEAR_API_KEY_REF`. Even on a cloud host that is
`Bash`-capable and attempts a fast-path script, `get_key()` exits non-zero
before any GraphQL request goes out, and the run falls to the MCP floor
(OAuth-scoped, no raw key). The guarantee this design makes is that **the key
is never present** in that environment — not that the script is never
invoked. Do not "fix" this by wiring the key into cloud config; that defeats
the entire boundary.

**Under 1Password desktop-app integration**, `op` only unlocks in an
authorized terminal, never in an agent's tool-spawned subshell — so an
interactive session exports the resolved key into the _launching_ terminal
before starting the agent (inheriting an already-resolved env var is fine),
and a headless run instead sets `$OP_SERVICE_ACCOUNT_TOKEN` alongside
`$LINEAR_API_KEY_REF` so the script can resolve it itself.

## The opt-in live-smoke-test convention

`scripts/test-linear-ready-live.sh` and `scripts/test-linear-scan-live.sh`
hit the **real** Linear GraphQL API, unlike every other `scripts/test-*.sh`
harness, so they are opt-in rather than always-on:

- **Key precedence**, identical to the scripts' own `get_key()`: raw
  `$LINEAR_API_KEY`, then `$LINEAR_API_KEY_REF` (an `op://` ref), then
  `linear.api_key_ref` read out of `dev_docs/tasks/.task-config.local.yml`
  (checked first — a personal ref belongs in the gitignored local override)
  or `.task-config.yml`. A ref is resolved with a **bounded** `op read`
  (backgrounded, hard-killed after ~6s) so a locked 1Password desktop session
  can never hang `check.sh` waiting on a biometric prompt it can't answer.
- **Self-skip keyless.** With no key resolvable, the test exits 0 — quiet
  (`SKIP`) in CI, loud (`WARNING`) locally — so keyless devs and CI both stay
  green, and a Linear personal API key never has to live in CI secrets (see
  the security boundary above; keeping CI keyless is itself a consequence of
  that boundary, not a separate policy).
- **Contract assertions, not workspace values.** Workspace state (which
  issues exist, their fields) is mutable and not worth pinning down; the test
  instead asserts the **API contract**: the top-level `{ meta, ... }` shape
  is exact, the per-item field set is exact (including the deliberate
  _absence_ of fields like `description` or the internal `_updatedAt`), gate
  invariants hold (e.g. every ready candidate's `estimate` really is below
  the max, no excluded label survived), and drop-reason strings match the
  canonical pattern from `linear-common.md`.
- **Bad-key fail-closed check.** A second run with a bogus key (and the
  `op://` ref unset, so it can't accidentally fall back and succeed) must
  exit non-zero with empty stdout and an error on stderr — this is exactly
  the fallback trigger every consumer's try-script-then-floor wiring depends
  on, so the test proves the fail-closed contract holds, not just the happy
  path.
- **Wired into `scripts/check.sh`** like any other test harness — its
  self-skip behavior is what makes that safe to do unconditionally.

## How to add a fifth fast-path

1. **Write a new script** under `commands/handlers/assets/`, mirroring the
   shape of `linear-ready.py` or `linear-scan.py`: import/copy `get_key()`
   and `gql()` from `linear-archive.py` verbatim (do not reimplement key
   resolution or the GraphQL POST), build one paginated query per scope
   (`--team`, `--project` repeatable, resolve the team via the same
   `PRELUDE_BY_ID`/`PRELUDE_BY_NAME` pattern), and print exactly one JSON
   object to stdout on success — everything else (progress, errors) goes to
   stderr, and any failure is a non-zero exit with the reason on stderr, never
   a partial or malformed stdout payload.
2. **Decide the field list deliberately.** Default to skinny fields — only
   what the consumer(s) actually read. Only include `description` (or other
   heavy fields) if the consumer has a real, stated need for body text, the
   way relations-load does for dedup judgment; document that choice in the
   script's docstring the way `linear-scan.py` calls out its _absence_ of
   `description`.
3. **If the read will have more than one implementation to keep in lockstep**
   — 2+ consumers sharing it, or a single consumer whose fast-path script and
   MCP floor have subtle selection rules that could drift (as with
   Ready-candidate selection's gate + rank) — add a block to `linear-common.md`
   (state scope, field list, scope-resolution rule) as the single source of
   truth, and have the script's docstring and every consuming `.md` file point
   at it. If a single consumer's script and prose are simple enough to eyeball
   together, skip it — don't pre-abstract for a hypothetical second caller.
4. **Wire the consumer's `.md` file** with the try-script-then-floor pattern:
   attempt the script when `Bash` is available, treat any non-zero exit or
   unparseable stdout as the fallback trigger (one debug line, then run the
   existing MCP floor), and restate the security-boundary note (a Linear
   personal API key is a full-account bearer token, never injected into a
   cloud sandbox — copy the note verbatim from `linear-claim.md` "Find
   candidates" or `linear-sweep-complete.md` "Find in-flight issues" rather
   than rewording it).
5. **Add an opt-in live smoke test**, `scripts/test-linear-<name>-live.sh`,
   copying the key-resolution/self-skip/bad-key-fail-closed structure from
   `scripts/test-linear-ready-live.sh` or `scripts/test-linear-scan-live.sh`
   verbatim and swapping in the new script's path and its contract (exact
   top-level shape, exact per-item field set including deliberately-absent
   fields, any gate/invariant assertions specific to this read). Wire it into
   `scripts/check.sh` alongside the others — its self-skip behavior keeps
   that safe.
6. **Keep the MCP floor as the real fallback**, not a stub — the fast path is
   a pure optimization the design proves equivalent to the floor via the
   shared spec (step 3) or, for a single-consumer read, via matching
   docstring and consumer prose by hand. Never let a consumer depend on the
   fast path alone.
