# Auth key access

How a credential reference becomes a credential in this plugin. Follow this for **any**
secret a skill, command, or asset script needs — the Linear API key today, a Jira token
or a GitHub token tomorrow.

The design rationale, the alternatives that were rejected, and why the rules are shaped
this way live in [`dev_docs/designs/auth-key-resolution.md`](designs/auth-key-resolution.md).
This document is the contract itself.

## The three names

For a credential belonging to service `<SERVICE>`:

| Layer    | Environment                        | Config                            | Holds                              |
| -------- | ---------------------------------- | --------------------------------- | ---------------------------------- |
| Secret   | `$<SERVICE>_<CREDENTIAL>`          | local file only                   | the raw secret value               |
| Pointer  | `$<SERVICE>_<CREDENTIAL>_REF`      | `<service>.<credential>_ref`      | an opaque `<scheme>://…` reference |
| Resolver | `$<SERVICE>_<CREDENTIAL>_RESOLVER` | `<service>.<credential>_resolver` | an identifier from the allow-list  |

For the Linear key that is `$LINEAR_API_KEY`, `$LINEAR_API_KEY_REF` /
`linear.api_key_ref`, and `$LINEAR_API_KEY_RESOLVER` / `linear.api_key_resolver`.

**A raw secret never goes in the committed config or in a tracked file.** It is accepted
in the **gitignored `.task-config.local.yml`** for operators who would rather not run a
secret manager — see [Plaintext keys](#plaintext-keys) for what that costs — and refused
in the committed `.task-config.yml`.

A pointer is not a secret, but it is still sensitive — it advertises where a full-account
token lives — so its canonical home is that same gitignored file, never the committed
one.

## Two ladders

The pointer and the resolver are resolved **independently**. A pointer says _which
secret_ and belongs to the checkout or the launching environment; a resolver says _how
this machine unlocks secrets_ and belongs to the machine. Pairing them would mean an
inherited `$..._REF` silently suppressed the operator's configured resolver.

**Secret / pointer** — first hit wins:

0. `<service>.<credential>` from `.task-config.local.yml` — a raw secret in the local
   config, bridged by the agent into `$<NAME>`. Local file only; refused in the committed
   config. See [Plaintext keys](#plaintext-keys).
1. `$<NAME>` — the raw secret. Used directly; no resolver runs.
2. `$<NAME>_REF` — resolve it.
3. `<service>.<credential>_ref` from the **merged** config, bridged by the agent onto the
   same Bash invocation that runs the script.
4. Nothing → unavailable (`unconfigured`).

Rung 0 is numbered from zero because it is not something the helper sees: like rung 3, it
is a config value the **agent** bridges into the environment. An inherited `$<NAME>`
(rung 1) is what the helper actually reads in both cases.

**Resolver** — first hit wins:

1. `$<NAME>_RESOLVER`.
2. `<service>.<credential>_resolver` from `.task-config.local.yml` — see
   [Provenance](#provenance).
3. Default: `op`.

A **failed** resolve never falls through to the next rung. Falling through would make a
misconfigured pointer look identical to a host that simply has no key — the exact bug
this contract exists to prevent.

## The resolver allow-list

| Identifier     | argv invoked          |
| -------------- | --------------------- |
| `op` (default) | `["op", "read", ref]` |
| `opx`          | `["opx", ref]`        |

The config names an **identifier**, never a command line. The argv is built in
`commands/handlers/assets/_secret_resolve.py`. An identifier not on the list fails with
`unknown-resolver` — it never falls back to `op`.

**Adding a backend.** Add an entry to the allow-list in `_secret_resolve.py`, with a
test. It must take the reference as its **final** argument, write exactly one secret
value to stdout, and exit non-zero when it cannot. Until a backend is on the list, its
users inject the resolved value into the environment instead (ladder rung 1) — for
example `op run`, `opx run --env NAME=op://… -- CMD`, or a plain export. **Arbitrary
command execution never becomes a config feature.**

## Reference grammar

A reference must start with a scheme — `^[a-z][a-z0-9+.-]*://` at position 0 — with no
leading or trailing whitespace and no newline.

Whitespace **inside** a reference is legal and must stay legal: 1Password item titles
routinely contain spaces, so `op://Private/PreThink Linear/dan_local_key` and
`op://Private/Linear API/credential` are both valid. What the rule rejects is a value
that begins with something other than a scheme — notably a command-prefixed one like
`opx op://Private/x/y`, which is a `malformed-ref` failure with its own distinct message
rather than a generic "no key" error.

## Provenance

`*_resolver` is honored **only** from the environment or the gitignored
`.task-config.local.yml`. Never from the committed `.task-config.yml`.

A resolver key found in the committed config is a **loud error** — report it, do not
bridge it. Silently ignoring it recreates the failure mode this contract exists to kill.

Enforcement lives in the **bridging agent**, not in the helper: the helper receives
environment variables and cannot see where they came from. So the agent must read
`*_resolver` from the raw `.task-config.local.yml` leaf in isolation — a deliberate
exception to the repo-wide "always read the merged view" rule, and the only key that has
one.

What this buys, precisely: it stops an **untrusted checkout** from naming the program
that runs on your machine. It does not defend against a hostile environment — anyone who
can set `$..._RESOLVER` can already run commands. And with `op` as the default, a
committed pointer is **not** inert on a machine with a live `op` session; it resolves.
The guarantee is that the committed config cannot choose the _program_.

## Plaintext keys

`<service>.<credential>` — a raw secret in `.task-config.local.yml` — is **supported**,
for operators who don't want to run a secret manager for a single key. It takes
precedence over the pointer: nothing needs resolving, so no resolver runs and no approval
is raised.

Two rules, both enforced the same way as the resolver's provenance rule:

- **Local file only.** In the committed `.task-config.yml` a raw secret is refused with a
  loud error, never merged and never used. The agent must read this key from the raw
  `.task-config.local.yml` leaf in isolation, not from the merged view.
- **Never echoed in prose.** It is a secret, so it never appears in a diagnostic, a log
  line, or a summary — the same rule that governs a resolved value.

There is a third consequence, and it is the one most easily missed: bridging a config
value into the environment means the agent writes it into the **command it runs**, so a
plaintext key lands in the session transcript and is briefly visible in `ps`. A pointer
does not have that problem — only the reference is bridged, and the resolver hands the
secret to the process directly. This is inherent to choosing plaintext, not a defect in
the bridge, but it is part of what you accept.

The trade, stated plainly so the choice is informed. `.task-config.local.yml` is ignored
robustly: `.gitignore` ignores `dev_docs/tasks/*` wholesale and negates only the committed
config, so this is not one forgotten ignore line away from being committed, and
`git stash -u` does not sweep ignored files. What you accept instead is twofold: a
plaintext full-account token **inside the repo tree**, where every agent session, editor
index, directory-wide grep, and backup of that folder can read it — and, because the
agent bridges it into the command it runs, the token also appears in the **session
transcript**. For a plaintext key without either exposure, export `$<NAME>` from your
shell profile — same rung, nothing on disk in the checkout and nothing bridged.

Both are legitimate. Nothing in this plugin nags about either.

## Failure semantics

Unavailable means the helper raises `SecretUnavailable`; the script exits non-zero with
an already-redacted message. **What that means is the caller's decision**, and callers
differ:

| Consumer                                                   | On unavailable                                          |
| ---------------------------------------------------------- | ------------------------------------------------------- |
| `linear-ready.py`, `linear-scan.py`, `linear-relations.py` | floor to the MCP path; non-fatal, reported once per run |
| `linear-archive.py`, `linear-false-closures.py`            | fatal — these have no MCP floor, so the command stops   |

If you add a consumer, say which of these it is in its own documentation. Do not assume
"floors" is the default.

## Timeouts

The resolver call is bounded at **120 seconds** — one value for all resolvers. The bound
has to clear a **human**: `op read` blocks on desktop-app approval and `opx` deliberately
blocks on a native approval dialog, so a few-second timeout would kill a resolve
mid-approval. A timeout is unavailability, category `timeout`.

## Diagnostics

- **Never** print a full reference. Reduce it: `op://<vault>/…` for `op://` refs, the
  phrase "configured secret reference" for anything else.
- **Never** pass a resolver's stderr through verbatim — it can contain the pointer or
  worse.
- Report the **reason category** instead, which is what makes a failure actionable.
  `unconfigured` is the one category a caller should stay **quiet** about: it means the
  host named no credential at all, which is a legitimate configuration, not a fault.
  Every other category means something was configured and did not work, and deserves a
  line.

| Category           | Means                                      | Usual fix                                     |
| ------------------ | ------------------------------------------ | --------------------------------------------- |
| `unconfigured`     | nothing named a secret or a reference      | none — this host is deliberately keyless      |
| `no-session`       | no authorized 1Password session            | run `op signin` in your own terminal          |
| `not-found`        | the vault/item/field doesn't exist         | fix the reference                             |
| `denied`           | the resolver refused                       | approve the prompt, or check vault access     |
| `no-binary`        | the resolver isn't installed               | install it, or change the resolver identifier |
| `timeout`          | no answer within 120s                      | approve the dialog, or use an unattended path |
| `empty`            | resolver succeeded but returned nothing    | check the field name                          |
| `malformed-ref`    | the reference isn't a `<scheme>://…` value | fix the reference (see grammar above)         |
| `unknown-resolver` | identifier not on the allow-list           | fix it, or add the backend in code            |

## Unattended and cloud

- **Unattended** (cron, `/auto-pilot`, CI) has no UI and therefore no approval dialog.
  Use the raw secret directly, or `$OP_SERVICE_ACCOUNT_TOKEN` plus a pointer under the
  default `op` resolver. An approval-based resolver like `opx` correctly fails closed
  there; a cron that needs it must set the resolver to `op` or inject the secret.
- **Cloud** sandboxes must never receive a full-account key. The guarantee is enforced by
  **delivery**, not by this contract: cloud launches receive no `.local.yml`, no
  `*_REF`, no `*_RESOLVER`, and no raw secret, so resolution fails before any API call
  and the run floors to the OAuth-scoped MCP path. No resolver design can protect an
  environment that is deliberately handed the raw secret — so don't hand it one.

## Using it

```python
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _secret_resolve import SecretUnavailable, resolve_key

try:
    key = resolve_key("LINEAR_API_KEY")
except SecretUnavailable as e:
    sys.exit(str(e))  # already redacted
```

To check whether a credential resolves **without** revealing it — this is what
`/doctor` uses:

```sh
python3 commands/handlers/assets/_secret_resolve.py --probe LINEAR_API_KEY
```

It writes nothing to stdout ever, prints only a reason category to stderr on failure, and
exits non-zero when the credential is unavailable.

## One gotcha worth knowing

`opx` invalidates the `op` session after every read. So once something resolves through
`opx`, the next plain `op read` in that shell — `/doctor`'s probe, a live smoke test, a
manual read — fails until the next approval. That is `opx` working as designed, not a
broken session.
