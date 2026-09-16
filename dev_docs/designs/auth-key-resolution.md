# Design — generic auth-key resolution

Status: approved, not yet implemented
Date: 2026-08-01
Supersedes: `dev_docs/opx-key-resolution-docs-prompt.md` (untracked, deleted — it framed
this as an `opx` documentation task, which was the wrong altitude)

## Problem

`workflow-skills` ships to other people, so no one operator's secret tooling may be
baked in as a default. Today the framework hard-codes exactly one mechanism for turning
a credential reference into a credential: `subprocess.run(["op", "read", ref])`,
duplicated in each of the five key-consuming Linear assets, with `linear.api_key_ref`
documented as "a full `op://vault/item/field` reference".

That single mechanism is both too narrow and silently failing:

- **Too narrow.** Operators variously use plain `op`, a 1Password service account,
  `opx` (a personal wrapper that forces an approval dialog per read and invalidates the
  session afterwards), `pass`, Vault, or a plain exported variable. Only the first two
  work.
- **Silently failing.** This repo's own gitignored `.task-config.local.yml` carried an
  `api_key_ref` whose value was prefixed with `opx` — the prefix is the defect, and the
  reference itself is redacted here. It resolves as `op read "opx op://…"` and fails. A
  malformed ref fails in exactly the same shape as a correctly-keyless host, so every
  Linear fast path here has been flooring to the MCP path unnoticed. Tracked on the
  local card
  `dev_docs/tasks/linear-fastpath-key-never-resolves-from-config.md` (gitignored — a
  card to close, not a file any PR touches).

The deliverable is a contract future contributors follow for _any_ credential the
framework touches — the Linear key today, a Jira token or GitHub token later — where
`opx` is one allow-listed implementation and nothing more.

## Invariants this must not break

- **The gate.** Any non-zero exit from a fast-path script means "fast path unavailable"
  and the verb falls back to the OAuth-scoped MCP floor. There is deliberately no
  `[ -n "$LINEAR_API_KEY" ]` pre-check — the non-zero exit _is_ the gate.
- **The cloud boundary.** A Linear personal API key is a full-account bearer token and
  must never reach a cloud sandbox. Nothing here may create a route for a key into a
  cloud checkout.
- **The unattended path.** `$LINEAR_API_KEY` directly, or `$OP_SERVICE_ACCOUNT_TOKEN`
  plus `$LINEAR_API_KEY_REF` resolved by plain `op read`, must keep working.
- **Legibility of failure.** `linear-common.md` requires the agent to surface an
  actionable reason ("run `op signin`") when a configured ref does not resolve. That
  survives — see "Diagnostics".
- **Scripts stay env-in / JSON-out**, stdlib-only, parsing no YAML.

## Rejected alternatives

**The ref value doubles as a command** (`api_key_ref: opx op://…`, run via `shlex.split`
or a shell). Rejected on three counts. The merged config includes the _committed_
`.task-config.yml`, so a cloned repo whose config says `api_key_ref: curl … | sh` would
execute attacker argv the first time a fast path fires. Redaction collapses: reducing a
pointer to `op://<vault>/…` has nothing well-formed to reduce once the value is an
arbitrary command string, and the error path leaks whatever the command was. And a field
that is sometimes data and sometimes a program is a permanent parsing and auditing
problem. `shell=True` is never justified here — no legitimate resolver needs pipes,
globbing, or command substitution.

**Caller-injected environment only** (drop config resolution; operators wrap every
invocation, e.g. `opx run --env LINEAR_API_KEY=op://… -- python3 …`). Not rejected so
much as already present: this _is_ precedence step 1 below, and it stays fully supported
and documented. It cannot be the _only_ mechanism — the convenience layer exists because
interactive operators will not wrap every invocation, and deleting it floors the fast
path for everyone but the fastidious.

## The contract

For any credential, three names.

| Layer    | Environment                | Config                    | Contains                                       |
| -------- | -------------------------- | ------------------------- | ---------------------------------------------- |
| Secret   | `$LINEAR_API_KEY`          | never                     | the raw value                                  |
| Pointer  | `$LINEAR_API_KEY_REF`      | `linear.api_key_ref`      | an opaque `<scheme>://…` reference — data only |
| Resolver | `$LINEAR_API_KEY_RESOLVER` | `linear.api_key_resolver` | an identifier from an allow-list — never argv  |

Generic form: `<SERVICE>_<CREDENTIAL>`, `<SERVICE>_<CREDENTIAL>_REF`,
`<SERVICE>_<CREDENTIAL>_RESOLVER`; in config, `<credential>_ref` and
`<credential>_resolver` beside the relevant service block.

### Precedence, and how ref and resolver pair

The secret and the pointer are resolved down one ladder; the resolver is resolved down a
**separate** ladder. They are _not_ bridged as a pair. The reason: a pointer says _which
secret_, and belongs to the checkout or the launching environment, while a resolver says
_how this machine unlocks secrets_, and belongs to the machine. Pairing them per-layer
produces the failure Fable identified — an inherited `$LINEAR_API_KEY_REF` from a
terminal export would suppress the `.local.yml` resolver and silently fall back to plain
`op read`, either hanging on an approval-less read or bypassing the approval flow the
operator installed `opx` to get.

**Secret / pointer ladder** — first hit wins:

0. `linear.api_key` from `.task-config.local.yml` — a raw key in the local config, bridged
   by the agent into `$LINEAR_API_KEY`. Added after the first review round at the owner's
   request: an operator who doesn't want to run a secret manager for one key should not be
   forced to. Local file only, refused in the committed config, same provenance rule as
   the resolver. The exposure it accepts — plaintext inside a tree every agent session
   reads — is documented at the point of configuration rather than prevented, because it
   is the operator's call to make about their own token.
1. `$LINEAR_API_KEY` (raw secret) → use it; no resolver runs.
2. `$LINEAR_API_KEY_REF` → resolve it.
3. `linear.api_key_ref` from the merged config, bridged by the agent onto the _same_
   Bash invocation that runs the script → resolve it.
4. Nothing → unavailable.

A _failed_ resolve at step 2 or 3 does **not** fall through to the next rung. Falling
through masks a misconfiguration as absence, which is the bug this design exists to
kill.

**Resolver ladder** — independent, first hit wins:

1. `$LINEAR_API_KEY_RESOLVER`.
2. `linear.api_key_resolver` read from the **raw `.task-config.local.yml` leaf**, not
   the merged view (see Provenance), bridged onto the same invocation.
3. Default: `op`.

This yields the full pairing matrix without further rules: any ref source composes with
any resolver source.

### Resolver allow-list

Extended in code, never by config:

| Identifier     | argv                  |
| -------------- | --------------------- |
| `op` (default) | `["op", "read", ref]` |
| `opx`          | `["opx", ref]`        |

Contract for any future entry: takes one opaque reference as its **final** argument,
writes exactly one secret value to stdout, and signals unavailability by non-zero exit.
A missing binary, a timeout, or empty stdout is also unavailability. An identifier that
is not on the list is a distinct `unknown-resolver` failure — never a silent fallback to
`op`. Until a backend has an entry here, its users inject the resolved value into the
environment (precedence step 1); arbitrary command execution never becomes a config
feature.

### Reference grammar

A reference must begin with a scheme — `^[a-z][a-z0-9+.-]*://` at position 0 — and carry
no leading or trailing whitespace and no newline. Whitespace **inside** the reference is
legal and must stay legal: 1Password item titles routinely contain spaces, and the
canonical example in `linear-config.md` — `op://Private/Linear API/credential` —
carries one. The scheme-at-position-0 rule
is what rejects `opx op://…`, and it is sufficient on its own; a blanket no-whitespace
rule would reject the very configuration this design is meant to make work. Anything
failing the grammar is a distinct `malformed-ref` failure, not the generic no-key error.

### Provenance

`*_resolver` is honored only from the environment or the gitignored
`.task-config.local.yml` — never from the committed `.task-config.yml`. A resolver key
found in the committed config is a **loud error**: the agent reports it and does not
bridge it. Silently ignoring it would recreate exactly the failure class this design
exists to kill, where a value in the wrong place fails in the same shape as absence.

Enforcement lives in the **bridging agent**, not in the helper: the helper receives
environment variables and cannot see where they came from. `linear-common.md` must
therefore instruct the agent to read `api_key_resolver` from the raw
`.task-config.local.yml` leaf in isolation — a deliberate, stated departure from the
repo-wide "always read the merged view" rule, and the only key with that exception.
`/doctor` gets a matching check.

Be precise about what this buys, because the earlier draft overstated it: the rule
defends against an **untrusted checkout**, not against a hostile environment. Anyone who
can set `$LINEAR_API_KEY_RESOLVER` can already run commands on the machine. And with `op`
as the default resolver, a committed pointer is _not_ inert on a machine with a live `op`
session — it resolves. What the committed config cannot do is name the _program_ that
runs.

### Failure semantics

Unavailable = the helper raises; the script exits non-zero with a sanitized message. What
that means is **caller-defined**, and the five consumers do not agree:

| Consumer                                                       | On unavailable                                 |
| -------------------------------------------------------------- | ---------------------------------------------- |
| `linear-ready.py`, `linear-scan.py`, `linear-relations.py`     | floor to MCP, non-fatal, reported once per run |
| `linear-archive.py` (GraphQL-only backstop, no MCP mutation)   | fatal — the command cannot proceed             |
| `linear-false-closures.py` (documented as having no MCP floor) | fatal — the command cannot proceed             |

The earlier draft's flat "non-fatal, floors" was wrong for the last two. The helper
raises a typed `SecretUnavailable`; each script's `get_key()` decides. This keeps the
gate exactly as it is for the three that have one.

### Timeouts

The helper bounds the resolver call at **120 seconds**, a single value for all
resolvers. The bound has to clear a **human** — `opx` deliberately blocks on a native
approval dialog, and `op read` blocks on desktop-app approval too — so anything in the
usual few-second range would kill a resolve mid-approval. 120s also sits under the Bash
tool's own default so a headless `op read` on a machine with no session surfaces as a
clean unavailable rather than a hung tool call. Today there is no timeout at all on
`op read` in any of the five scripts (only the HTTP call is bounded, at 15s), so this is
new protection, not a regression.

### Diagnostics

Never pass a resolver's stderr through verbatim, and never print a full reference:
`op://<vault>/…` for `op://` refs, the phrase "configured secret reference" for any other
scheme.

Exit status alone is not enough — `linear-common.md` requires the agent to tell the user
to run `op signin` when a session has lapsed, and a bare status cannot distinguish that
from a missing item or a missing binary. So the helper emits a **sanitized reason
category** on stderr, which the agent may relay verbatim: `unconfigured`, `no-session`,
`not-found`, `denied`, `no-binary`, `timeout`, `empty`, `malformed-ref`,
`unknown-resolver`. Category plus the vault-reduced pointer is what a caller reports; the
resolver's own output never is.

`unconfigured` was added during implementation and is load-bearing: the first draft had
only eight categories, which forced "nothing is configured anywhere" to share `not-found`
with "the ref names an item that does not exist". Those must stay distinct, because
`linear-common.md` requires a keyless host to floor **silently** while a configured-but-
unresolvable key gets an explanatory line. Collapsing them would have reintroduced this
design's founding bug in a new place.

## Invariant check

- **The gate** is unchanged for the three consumers that have one: a missing resolver, a
  failed read, a timeout, empty stdout, or `opx` exiting 3 because no UI is available all
  produce a non-zero script exit and the MCP floor. No new pre-checks.
- **The cloud boundary** is unchanged: a cloud checkout has no `.local.yml`, no resolver,
  and no 1Password session, so the script exits non-zero before any GraphQL request. State
  plainly, per codex's correction, that the guarantee is enforced by **delivery** — cloud
  launches receive no local config, no `*_REF`, no `*_RESOLVER`, and no raw secret — not
  by the resolver design, which cannot stop an environment deliberately handed
  `$LINEAR_API_KEY`.
- **Unattended** is unchanged: the raw env secret still wins outright, and
  `$OP_SERVICE_ACCOUNT_TOKEN` plus a ref works under the default `op` resolver. `opx`
  failing closed headlessly is correct behavior; a cron that wants it must set the
  resolver to `op` or inject the key directly.

## Code shape

A new sibling `commands/handlers/assets/_secret_resolve.py` — an importable name, which
the five hyphenated scripts reach via `sys.path[0]` — exporting:

- `resolve_key(name="LINEAR_API_KEY") -> str`, walking both ladders above; raises
  `SecretUnavailable(category, message)` where `message` is already redacted.
- `redact(ref) -> str`.
- A `--probe <NAME>` CLI entry point: runs the same resolution, prints **only** the
  reason category to stderr, never the secret to stdout, and exits 0/non-zero. This is
  what `/doctor` calls; doctor must not import Python internals from command prose, and
  must not shell out to `op read` directly now that the resolver is configurable.

Each script's `get_key()` becomes a call into it plus that script's own fatal-vs-floor
handling. Two behaviors it adds that do not exist today:

- **Grammar validation**, per above — what should have caught the `opx op://…` value
  instead of months of quiet flooring.
- **Code-enforced redaction.** The scripts currently interpolate the full ref into stderr
  (e.g. `linear-scan.py:110`) and only the markdown instructs agents not to repeat it —
  which leaks through logs and through direct invocation.

### Tests

- A hermetic unit test for `_secret_resolve.py`, wired into `scripts/check.sh`: allow-list
  enforcement, unknown identifier, a valid ref containing spaces, the malformed
  `opx op://…` value, no-fallthrough on a failed resolve, timeout, empty stdout, and
  redaction of the ref in every error path. It must stub the resolver binary — no real
  `op`.
- The three opt-in live smoke tests (`scripts/test-linear-{ready,scan,relations}-live.sh`)
  each hard-code their own precedence chain and call `op read` directly, so a non-`op`
  resolver makes them skip while the scripts themselves work. Their config scrape is also
  already broken for spaced refs — the sed in `test-linear-scan-live.sh:77` matches
  `[^#[:space:]]*` and truncates `op://TestVault/Item With Spaces/…` at the space. Both get
  fixed by routing them through `--probe` / the helper.

## Delivery

**PR 1 — contract, fix, and everything that would otherwise lie.**

- `dev_docs/auth_key_access.md` — the contract above, written for contributors.
- `commands/handlers/assets/_secret_resolve.py` + its hermetic test in `scripts/check.sh`.
- The five assets' `get_key()`, **and their module docstrings**, which currently document
  the `op read` contract verbatim.
- `commands/handlers/linear-common.md` → "Key resolution": both ladders, the raw-local-leaf
  exception for the resolver, the reason categories.
- `dev_docs/decisions/linear_read_fastpaths.md` — not the addendum the earlier draft
  planned. It currently states that `get_key()` is defined in `linear-archive.py` and
  copied verbatim by every sibling, and that refs resolve via `op read`; both stop being
  true in this PR. Correct those statements and add the addendum.
- `commands/handlers/linear-archive.md` and `linear-false-closures.md` — they document
  direct invocation with `op read` and are the two fatal-on-unavailable consumers.

Code and the documents that describe its behavior land together; leaving them for PR 2
would ship false implementation guidance in the gap. Closes the local card
`linear-fastpath-key-never-resolves-from-config.md`.

**PR 2 — propagation.** Pure prose, no behavior change, safe to land late because PR 1
keeps `op` as the default.

- `commands/handlers/linear-config.md` → "Archive key".
- `commands/doctor.md` — replace the direct `op read "<ref>"` probe (currently at
  ~L246) with `--probe`, and add the committed-resolver provenance check.
- `dev_docs/decisions/linear_shared_scan.md` — mentions the `$LINEAR_API_KEY_REF`
  contract.
- The committed `dev_docs/tasks/.task-config.yml` comment block (~L12-17), which
  documents the ref/env contract and should name the resolver key.
- The consumer sites that abbreviate the invocation: `linear-claim.md`,
  `linear-reoptimize.md`, `skills/auto-pilot/references/launch-preflight.md`.
- `scripts/test-linear-{ready,scan,relations}-live.sh`.

Grep `op read|api_key_ref|LINEAR_API_KEY` and check each site — the earlier draft's
`LINEAR_API_KEY_REF=` grep misses `launch-preflight.md` (which says `op read`) and
`linear-reoptimize.md` (which only cites the shared gate, and may need no change at all —
verify rather than assume).

This repo's own gitignored `.task-config.local.yml` becomes:

```yaml
linear:
  api_key_ref: op://Private/Linear API/credential
  api_key_resolver: opx
```

No manual version bump: `dev_docs/releasing.md` → "Versioning" states versions bump
automatically on merge to `main` via the Release workflow, driven by the commit type.

## Operational note

`opx` invalidates the `op` session after each read. So once a fast path resolves through
`opx`, any _subsequent_ plain `op read` in the same shell session — `/doctor`'s probe,
a live smoke test, a manual read — will fail until the next approval. That is opx working
as intended, but it looks like a bug from the outside, so both `auth_key_access.md` and
the doctor check should say it in one line.

## Provenance

Approach chosen after independent recommendations from Fable and codex, which converged
on the separate-resolver design and both rejected the ref-as-command form. codex
contributed the allow-listed-identifier tightening (config names an identifier, not
argv), the fatal-vs-floor correction, and the observation that redaction is currently
unenforced in code; Fable contributed the provenance rule confining resolver declaration
to machine scope, the ref/resolver pairing problem, and the timeout-versus-approval
tension. Both then reviewed the written design; this revision folds in that review.
