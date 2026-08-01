# opx in Linear fast-path key resolution

Date: 2026-08-01
Status: approved

## Problem

`commands/handlers/linear-common.md` → "Key resolution" documents exactly one
mechanism for getting a Linear personal API key to the five fast-path Python
scripts: the script resolves `op read "$LINEAR_API_KEY_REF"` itself, backed by
an ambient `op signin` session or by `$OP_SERVICE_ACCOUNT_TOKEN`.

The ambient-session model is the thing worth removing for interactive/agent
runs. An `op signin` session is visible to every subprocess the user's machine
spawns for roughly 30 minutes, so any agent turn in that window can read the key
without the user seeing anything. The key in question is a Linear **personal API
key** — a full-account bearer token.

`opx` (github.com/bestdan/opx, installed at `~/.local/bin/opx`) wraps `op` to
force a native approval dialog on every secret read and to invalidate the `op`
session afterwards, so nothing is cached between invocations. It fails closed:
with no UI available the confirm collapses to denial (exit 3) rather than
hanging. Exit codes: `0` ok, `1` op/tool failure, `2` usage error, `3` denied.

## What is already proven

This invocation was field-tested against real Linear with the Python scripts
**completely unchanged**:

```bash
opx run --env "LINEAR_API_KEY=op://Private/PreThink Linear/dan_local_key" -- \
  python3 commands/handlers/assets/linear-archive.py --team PreThink --older-than 1
```

It works because the caller injects the key into the child's environment, and
the scripts already prefer `$LINEAR_API_KEY` over `$LINEAR_API_KEY_REF` — so
they never invoke `op` or `opx` at all. One dialog per invocation, secret scoped
to that child process, nothing left in the agent session's environment.

## Decision

`opx run` becomes the **documented default for interactive/agent invocations**.
The scripts' own `op read` path is retained and stays documented, but only for
unattended runs. `opx` cannot serve those — no UI to approve, so it would exit 3.

### Selection rule

`linear-common.md` → "Key resolution" step 3 becomes a two-way branch:

1. `$LINEAR_API_KEY` or `$LINEAR_API_KEY_REF` **already set** in the
   environment → change nothing, invoke `python3 <script>.py …` plain. This is
   the headless `$OP_SERVICE_ACCOUNT_TOKEN` + `$LINEAR_API_KEY_REF` case and the
   launching-terminal export. Unchanged from today.
2. Otherwise, merged config has `linear.api_key_ref`:
   - **`opx` on PATH** →
     ```bash
     opx run --env 'LINEAR_API_KEY=<linear.api_key_ref from merged config>' -- \
       python3 "${CLAUDE_PLUGIN_ROOT}/commands/handlers/assets/<script>.py" --team "<linear.team>" …
     ```
   - **`opx` absent** → today's form,
     `LINEAR_API_KEY_REF='<ref>' python3 <script>.py …`, and the script resolves
     it with its own `op read`.
3. Otherwise → invoke bare. The script exits non-zero and the run floors, which
   is correct for a keyless host.

Both existing load-bearing rules survive verbatim:

- **Same Bash call.** Each Bash tool call is a fresh shell, so the credential
  and the invocation must never be split across two calls.
- **Single quotes** around the ref, because it comes from a config file and
  could contain `$(…)`, backticks, or `"`.

### Failure legibility

The gate is unchanged: any non-zero exit from the script floors the run to the
MCP path, non-fatally. What changes is the one explanatory line, which must now
distinguish two causes that have different fixes:

- **opx exit 3** — the read was **denied**, or there was no UI to ask. Not
  fixable by `op signin`. Retry and approve the dialog, or export the key in the
  launching terminal.
- **`op read` failure** — the usual cause is a lapsed session; the fix is one
  `op signin` in the user's own terminal.

Both stay redacted to the vault segment (`op://<vault>/…`), per
`linear-config.md` → "Keep `api_key_ref` out of the committed config". Said once
per run, not per scope.

### Cost, stated honestly

One dialog per **script invocation**, not per turn. `reconcile-tasks` row 2
invokes `linear-scan.py` once per resolved scope, so a single turn can raise
several dialogs; `sweep-for-complete` batches all configured projects into one
call and raises one. This is documented rather than hidden.

### Security boundary

Unchanged in substance, marginally stronger in practice. opx forgets the session
after each read, so the ~30-minute ambient-session window disappears. A cloud
checkout has no `opx`, no `op` session, and no `.task-config.local.yml`, so it
exits non-zero before any GraphQL request and floors to the OAuth-scoped MCP
path exactly as today. No new route for a key into a cloud sandbox.

### `doctor.md`

Today the linear check resolves `op read "<ref>" >/dev/null` to prove the key
works. Under opx that would pop a dialog on every `/doctor` run, and opx's
session-invalidation would kill any ambient `op` session the user had. So:

- Add an `opx` presence line to the prerequisite checks.
- Gate the resolution probe: probe with `op read` only when
  `$OP_SERVICE_ACCOUNT_TOKEN` is set. When `opx` is present and no service
  account token is set, report `PASS` — "ref configured; resolution is
  approval-gated per invocation, not probed here" — instead of resolving.
- When neither applies, keep today's behavior.

`/doctor`'s status vocabulary stays `PASS`/`WARN`/`FAIL`; no `INFO` status is
introduced for one check.

## Out of scope

- **Do not modify the five `commands/handlers/assets/linear-*.py` scripts.**
  Their env-first precedence is exactly what makes this work.
- Swapping `op read` → `opx` **inside** those scripts. Rejected: breaks the
  unattended service-account path (no UI → exit 3), and because a non-zero exit
  _is_ the gate, the breakage is indistinguishable from "no key configured".
- Pre-loading `eval "$(opx --env LINEAR_API_KEY=…)"` in the launching terminal.
  Rejected: requires knowing at terminal-open time which credentials the session
  will need, and parks a full-account bearer token in every subprocess's
  environment for hours — the exact ambient-credential model opx exists to remove.
- Any `[ -n "$LINEAR_API_KEY" ]` pre-check. The gate stays "non-zero exit is the
  fallback trigger".
- Claiming `opx` accepts `--account`. It does not; it follows `$OP_ACCOUNT`.

## Files in scope

| File                                                                                        | Change                                                                                                                       |
| ------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------- |
| `commands/handlers/linear-common.md`                                                        | "Key resolution" rewritten to the selection rule above; failure-legibility paragraph split by cause. Single source of truth. |
| `commands/handlers/linear-config.md`                                                        | "Archive key" documents both mechanisms and which situation each serves.                                                     |
| `commands/doctor.md`                                                                        | `opx` presence check; resolution probe gated as above.                                                                       |
| `skills/auto-pilot/references/launch-preflight.md`                                          | One sentence that opx is explicitly unusable unattended (fails closed, exit 3); existing `op read` guidance stays.           |
| `dev_docs/decisions/linear_read_fastpaths.md`                                               | Addendum noting the opx option and its scoping. No rewrite of history.                                                       |
| `commands/handlers/linear-archive.md`                                                       | Interactive invocation re-pointed; the "Run it without an agent" cron section stays on `op`.                                 |
| `commands/handlers/linear-claim.md`                                                         | The `api_key_ref` note (~L24) re-pointed.                                                                                    |
| `commands/handlers/linear-false-closures.md`                                                | The session/`op signin` guidance (~L101–113) re-pointed.                                                                     |
| `commands/handlers/linear-reoptimize.md`, `linear-reconcile.md`, `linear-sweep-complete.md` | Abbreviated invocations kept consistent with linear-common.md.                                                               |

**No manual version bump.** `CONTRIBUTING.md` → "Versioning" has CI bump
`plugin.json`/`marketplace.json` from the merged commit's Conventional Commit
type, and `docs:` maps to no release. No `SKILL.md` in this repo carries a
`version:` frontmatter field, so there is nothing to bump there either.

## Done when

The docs describe both mechanisms accurately, a reader can tell which applies to
their situation (interactive agent vs unattended vs cloud), and no doc claims a
capability opx does not have. Draft PR opened.
