---
created: 2026-09-20
status: accepted
convention: ../auth_key_access.md
---

# The TypeSafe key stays a plaintext local-config value

## Context

The Jev instrument behind §1 of
[`../research/2026-09-17-jev-applications/`](../research/2026-09-17-jev-applications/README.md)
needs a paid API key. It reads one from `dev_docs/tasks/.task-config.local.yml` as a
raw `typesafe.api_key` — rung 0 of [`../auth_key_access.md`](../auth_key_access.md).

The objection, filed as #773, was that this puts a live secret **inside the repo tree**,
where a directory-wide grep, an editor index, or a backup of that folder reads it. The
obvious remedy — an `op://` pointer — does not work here: resolving one runs `op read`,
which on a machine with the desktop app raises a biometric prompt, and repeated calls
cannot each be approved by a human. `lindev`, where this actually runs, is reached over
SSH and has **no desktop session at all**, so the prompt cannot surface under any
invocation.

Three options were on the table. **A** — export `$TYPESAFE_API_KEY` from the shell
profile and delete the config line. **B** — an `op://` pointer plus
`$OP_SERVICE_ACCOUNT_TOKEN`, the shape
[`bestdan/barclay`](https://github.com/bestdan/barclay) uses for Plaid. **C** — leave it.

## Decision

**C.** The key stays where it is. **A is ruled out permanently. B is deferred**, with
the triggers below, and when it happens it will not be jev that drives it.

Measured on 2026-09-20, before deciding:

- The file is gitignored (`.gitignore:45`, `dev_docs/tasks/*`) and untracked — it cannot
  be committed.
- It is now mode `600`, under a `/home/dan` at mode `750`, on a box whose only
  non-system account is `dan` and whose group `dan` has no other members. The only
  principals that can read it are root and processes running as that user.
- Neither environment route was in place: `$TYPESAFE_API_KEY` and
  `$OP_SERVICE_ACCOUNT_TOKEN` both unset, no export in any profile.

`auth_key_access.md`'s [Unattended and cloud](../auth_key_access.md#unattended-and-cloud)
section already sanctions both "the raw secret directly" and "`$OP_SERVICE_ACCOUNT_TOKEN`
plus a pointer". C is the first of those, so this is on-contract rather than a shortcut
around it.

### Why A is ruled out, not merely deferred

An **exported** variable is inherited by every process the user starts — every agent
tool call, every `uv run`, every package postinstall, and `/proc/<pid>/environ` of
anything long-lived. The 600 file is read by one script. That objection holds wherever
the export is written, so an unsynced file does not rescue it.

The sync risk is real but secondary: `~/.zshenv` is a symlink into the synced
`bestdan/dotfiles` repo, so an export there would put a live API key inside a git
repository — strictly worse than a file that cannot be committed at all. `~/.zshrc` is
unsynced only in the trivial sense; it is 34 bytes at mode 664 whose whole content
sources the synced tree.

Redefining A as "a credentials file outside the repo, read at invocation" is not A. It
is C with the file moved, and this file is already outside anything git can see.

### Why B is deferred rather than taken

Priced against barclay's actual shape — the service-account token in a gitignored
`chmod 700` wrapper, **not** in a shell profile — B is a genuinely different threat
model, not a relocation. Three things the plaintext key cannot offer: the on-disk
secret is revocable on its own without rotating the API key, its reads are logged by
1Password, and the API key itself never touches this box's disk.

What B does not change is the **exposure set**. A 700 wrapper is readable by exactly the
same principals as the 600 config file. B improves what an attacker gets and what you
learn afterwards; it does not shrink who can get it.

Against that, B costs: creating a service account in the 1Password web UI on another
machine; moving the TypeSafe item out of Personal into a created vault, because
**service accounts cannot read Personal or Private vaults**; and swapping rung 0's
zero-dependency reproduction for a network `op read` that fails closed —
`jev-description-collision.py`'s `resolve_key` exits rather than falling through to
another rung when `op read` fails. For a hand-run, dev-only instrument used a handful of
times, that does not pay.

### The reframing: this is a machine decision, not an instrument one

On a headless box an interactive human has the _same_ `op` problem as cron, so "what
should a human on `lindev` do" collapses to "does this box have a service account". That
question belongs to a **runtime** path, not to frozen evidence.
`~/src/dotfiles/scripts/nightly-linear-tidy.sh` is the path that actually faces it —
`linear.api_key_ref` has the identical biometric dead end — and as of 2026-09-20 it is
not scheduled on this box (no crontab for `dan`, no matching user timer).

**Jev must never be the reason to create a service account.** If one is ever created for
the Linear path, jev rides along at the cost of one YAML line.

## Consequences

- The instrument keeps the only rung with no external dependency that can rot between
  runs, which is what a reproducible research record wants.
- A live key remains on disk in the checkout, mitigated to `600` under a `750` home. That
  mitigation is now load-bearing for this decision rather than incidental — see the third
  revisit trigger.
- `linear.api_key_ref` keeps the same unresolved biometric problem. This record does not
  address it; it only declines to let jev decide it.

## Revisit when

Any one of these fires:

1. **A Linear sweep or `/auto-pilot` is scheduled on `lindev`.** Then create the service
   account and a non-Personal vault, put the token in one gitignored `chmod 700` wrapper
   outside any repo (barclay's shape), move `linear.api_key_ref` and
   `typesafe.api_key_ref` to that vault, and delete the plaintext line — one change.
2. **The TypeSafe key acquires real blast radius** — meaningful billing exposure, or it
   becomes shared with a runtime path or CI. Rotation then matters, and B makes rotation
   a vault edit instead of a file hunt.
3. **A second human account appears on the box, or the `600`/`750` posture loosens.** This
   decision leans on that posture.
4. **A service account is found to already exist** with a reachable non-Personal vault.
   B's setup cost is then near zero and the rotation-plus-audit upgrade is free — take it.
   Not checkable from `lindev`; look under Developer → Service Accounts in the 1Password
   web UI.

If B is ever done, use `resolve_key`'s existing rung 3 (`api_key_ref`). Do not add an
`op run --env-file` wrapper on top: on this box `op run` needs the same service-account
token, so it is B with redundant plumbing.

**One guard to add at that point, because it does not exist yet.** Barclay rejects any
credential still shaped like an `op://` reference, so forgetting the resolution step
fails loudly instead of sending the literal string to the API. `extract_key` guards only
one direction of that, measured 2026-09-20:

| Config                          | `extract_key` returns                                           |
| ------------------------------- | --------------------------------------------------------------- |
| `api_key_ref: "sk-notapointer"` | `None` — a non-pointer in the pointer field is refused          |
| `api_key_ref: "op://v/i/f"`     | `("ref", "op://v/i/f")` — resolved, correct                     |
| `api_key: "op://v/i/f"`         | `("raw", "op://v/i/f")` — **sent to the API as a bearer token** |

The third row is the failure barclay designed against: a pointer written to the raw field
is forwarded verbatim in an `Authorization` header. It costs one request and one confusing
`401` rather than a secret, so it is not urgent today — but whoever does B should add the
shape check in the same change, while the two field names are freshly in mind.

## Alternatives

|                     | Approach                                    | Prompt-free on `lindev`?    | Why not                                                                                                     |
| ------------------- | ------------------------------------------- | --------------------------- | ----------------------------------------------------------------------------------------------------------- |
| A                   | `export TYPESAFE_API_KEY` from the profile  | yes                         | Exported to every process the user starts; `~/.zshenv` is synced. Dominated by C on every axis.             |
| B                   | `$OP_SERVICE_ACCOUNT_TOKEN` + `api_key_ref` | yes                         | A real improvement, but its cost is not proportionate to a hand-run dev instrument. Deferred, not rejected. |
| `op run --env-file` | resolve references at exec time             | only with a service account | B with different plumbing; the ladder already has a pointer rung.                                           |
| C                   | status quo                                  | yes                         | **Chosen.**                                                                                                 |
