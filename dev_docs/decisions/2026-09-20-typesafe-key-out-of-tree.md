---
created: 2026-09-20
status: accepted
convention: ../auth_key_access.md
---

# The TypeSafe key moves out of the repo tree

## Context

The Jev instrument behind §1 of
[`../research/2026-09-17-jev-applications/`](../research/2026-09-17-jev-applications/README.md)
needs a paid API key. It read one from `dev_docs/tasks/.task-config.local.yml` as a raw
`typesafe.api_key` — rung 0 of [`../auth_key_access.md`](../auth_key_access.md).

The objection, filed as #773, was about **location**: a live secret inside the repo
tree, where a directory-wide grep, an editor index, or a backup of that folder reads it.
Measured 2026-09-20, two of those three are live. A default `rg` misses the file, because
ripgrep honours `.gitignore`; `rg --no-ignore --hidden` finds it. A backup or sync of
`~/src` has no such scruples. The editor-index claim is inferred, here as in the issue.

The obvious remedy, an `op://` pointer, cannot resolve unattended on `lindev`: it is
reached over SSH with no desktop session, so a 1Password biometric prompt cannot surface
under any invocation. That ruled out the pointer in its default form and left a
service account as the only 1Password route.

## Decision

**Move the key to a mode-600 file outside every checkout, and have the instrument read it
directly.** How to use the shape is the contract's
[Three shapes](../auth_key_access.md#three-shapes-and-which-to-pick), which carries it as
one of three supported plaintext shapes; this record is the reasoning.

```python
OPERATOR_KEY_FILE = Path.home() / ".config" / "workflow-skills" / "typesafe_api_key"
```

read after `$TYPESAFE_API_KEY` and before any pointer. Anything that reads only `$<NAME>`
can still get it bridged on the command line:

```bash
TYPESAFE_API_KEY="$(cat ~/.config/workflow-skills/typesafe_api_key)" <command>
```

It answers the location objection directly and at no dependency cost: nothing new to
install and nothing to lapse between runs. What it does **not** do is narrow principals —
root and anything running as the operator can still read the file. That was already
acceptable and remains so.

The direct read is a correction, not the original plan. This record first specified the
prefix form only, on the argument that its friction was load-bearing. The operator
declined to type it, which would have left a profile `export` — the broadest shape here —
as the only friction-free option. Reading the file costs one branch in the one shared
`resolve_key` and keeps the value's lifetime at a single process.

### This adds a shape; it does not remove one

All three plaintext shapes stay supported. The contract's
[Three shapes](../auth_key_access.md#three-shapes-and-which-to-pick) table is the
comparison, and its
[They do not collide, but they do shadow](../auth_key_access.md#they-do-not-collide-but-they-do-shadow)
section is the rule that keeps them apart: precedence decides, first hit wins, so a
second shape is never wrong — only inert, and silently so.

That silence is the one thing this decision has to be executed carefully around. Deleting
the raw line is not optional housekeeping; it is the change. Leave it and the instrument
keeps reading rung 0 while the out-of-tree file sits there believed-in and unused.

### Why not a shell-profile export

An **exported** variable is inherited by every process the operator starts — every agent
tool call, every `uv run`, every package postinstall, and `/proc/<pid>/environ` of
anything long-lived. The per-invocation prefix reaches the invoked command and its
descendants, for that one invocation. The difference is scope and lifetime.

The placement compounds it here: `~/.zshenv` is a symlink into the synced
`bestdan/dotfiles` repo, so an export there would put a live API key inside a git
repository — worse than the gitignored file it replaced. `~/.zshrc` is unsynced only in
the trivial sense; it is 34 bytes whose whole content sources the synced tree.

### Why not a service account, yet

Priced in its usual shape — the service-account token in a gitignored, mode-700 wrapper
outside any repo, read per invocation — a service account is a different threat model,
not a relocation. It offers three things no plaintext shape can: the on-disk secret is
revocable on its own without rotating the API key, its reads are logged by 1Password,
and the API key itself never touches this box's disk.

What it does not change is the **exposure set**: a 700 wrapper is readable by exactly the
same principals as a 600 file. So against the out-of-tree file it buys audit and
rotation, not a smaller blast radius — and it costs a service account created in the web
UI, an item living in a service-account-readable vault, and a network `op read` that
fails closed, replacing a local file read that cannot rot between runs.

The objection to a shell-profile export above applies to B as #773 words it, too, which
is worth saying because the two sections otherwise look inconsistent. B exports
`OP_SERVICE_ACCOUNT_TOKEN` from the profile — a credential good for every item in every
vault the account is granted, not just this one key — so the placement argument lands
harder there, not softer. That is an objection to the placement and not to the service
account, which is why B is priced above in the wrapper shape instead.

One framing correction, because an earlier draft of this record got it backwards: that
service accounts cannot read Personal or Private vaults is **deliberate scoping, not a
limitation**. It is what bounds a service account's blast radius, and it counts for the
approach rather than against it.

Two things get conflated here, so to be plain about which is which. A **service account**
is a 1Password principal: a non-human identity with its own token, scoped to the vaults
you grant it, whose reads 1Password logs. That is the thing this section defers.
**1Password Environments** is a separate feature, for grouping an application's variables
so they need not sit in a Personal vault. They are not two routes to the same place —
Environments **composes with** a service account rather than replacing one, so on a
headless box it still needs the whole apparatus priced above and removes none of that
cost. It is named here only because it is the vendor's own answer to the vault-scoping
point, and it is unavailable to this box anyway: `op environment read` ships on the CLI's
**beta** channel (`2.33.0-beta.02` or later), and stable 2.39.0 does not have it. No
stable upgrade reaches it; the gate is the channel, not the version number.

Checked 2026-09-20, and recorded because the obvious probe misleads twice. `op --help` on
2.39.0 lists no `environment` — but `op env` exists **unlisted**, and is a different
thing (`op env ls` lists shell variables already holding `op://` references, a companion
to `op run`). And the version number reads backwards: stable 2.39.0 is numerically past
`2.33.0-beta.02` and still lacks the command, because the gate is the channel.

### The service-account question belongs to a runtime path

On a headless box an interactive human has the same `op` problem as cron, so "does this
box get a service account" is a machine-level question, not an instrument-level one — and
it belongs to a **runtime** path rather than to frozen evidence.
`~/src/dotfiles/scripts/nightly-linear-tidy.sh` is the path that faces it, since
`linear.api_key_ref` has the identical biometric dead end. As of 2026-09-20 it is not
scheduled here: no crontab for `dan`, no matching user timer.

If a service account is ever created for that path, the TypeSafe key rides along at the
cost of one config line.

## Consequences

- The instrument keeps a rung with no external dependency that can rot between runs,
  which is what a reproducible research record wants.
- The operator types nothing: the instrument reads the file. The prefix stays available
  for a one-off override, since the file is read after `$<NAME>`.
- The instrument now has a rung `_secret_resolve.py` does not, so the two ladders have
  diverged further. Both remain faithful to the contract, which documents the operator
  key file and says which consumers read it — but a reader comparing the two will find
  four rungs on one side and three on the other.
- A plaintext key still exists on disk, now at `~/.config/workflow-skills/`, mode 600
  under a 700 directory. Principals are unchanged.
- `linear.api_key` has the same unresolved biometric problem and the same three shapes
  available. This record does not decide it.

## Execution

Repo-side, in this change: the contract gains the third shape, how to use it, and the
collision rule, and `resolve_key` gains the branch that reads the operator key file.

Operator-side — **done on `lindev` 2026-09-21**, in one go, because a half-done move is
the silent-shadow case above:

1. `install -d -m 700 ~/.config/workflow-skills`
2. `install -m 600 /dev/null ~/.config/workflow-skills/typesafe_api_key`, then paste the
   key in with an editor rather than echoing it into the shell.
3. Delete `typesafe.api_key` from **every copy the ladder scans** — the checkout you are
   standing in and the main checkout's `dev_docs/tasks/.task-config.local.yml`. Both are
   read, the local one first, because `local_config_paths` computes the second from
   `git rev-parse --git-common-dir` rather than from the root it is handed.
4. Verify per the contract's
   [shadow section](../auth_key_access.md#they-do-not-collide-but-they-do-shadow): with
   the operator key file temporarily moved aside **and** the variable unset, the ladder
   must reach its own "no key" error — that is what proves no raw value survives in a
   scanned config. Then put the file back and confirm resolution comes from it.

   Measured on `lindev`, 2026-09-21: with both absent, `SystemExit: No TypeSafe key`;
   with the file present and `$TYPESAFE_API_KEY` unset, the key resolves from the file
   and matches the value that had been in the config. Neither check calls the API —
   `resolve_key` is exercised directly, since a live run costs money.

## Revisit when

1. **A Linear sweep or `/auto-pilot` is scheduled on `lindev`.** Create the service
   account and a service-account-readable vault then, move both keys to pointers, and
   delete the out-of-tree file — one change.
2. **The TypeSafe key acquires real blast radius** — meaningful billing exposure, or it
   becomes shared with a runtime path or CI. Rotation then matters, and only the service
   account makes rotation a vault edit rather than a file hunt.
3. **Jev graduates from a research instrument into shipped plugin behaviour.** The
   proportionality argument above is a property of the current moment, not a permanent
   one; a shipped feature wants a real auth story.
4. **A service account is found to already exist** with a reachable vault, or
   **Environments reach the stable CLI channel**. Either makes the better option cheap
   enough to take on its own merits.
5. **A second human account appears on the box.** The 600/700 posture is doing work here.

## Confirmation

The two routes to the file are confirmed differently, and only one of them mechanically.

**The direct read is tested.** `OperatorKeyFileTests` in
[`../research/2026-09-17-jev-applications/references/test_jev_description_collision.py`](../research/2026-09-17-jev-applications/references/test_jev_description_collision.py)
covers the four behaviours the rung has to get right: the file is read when nothing else
is configured, an exported variable still overrides it, an empty file falls through
rather than returning an empty bearer token, and a raw config value still shadows it. The
operator-side check in Execution step 4 confirms the same thing against the real file.

**The command-prefix route rests on convention alone**, and cannot do otherwise: rung 1
reads `$<NAME>`, and nothing can tell where an inherited value came from. That is the
property which lets any consumer accept this shape without a code change, and it is also
why nothing can enforce that the operator used it.

## Alternatives

- **A profile export.** Ruled out permanently, not deferred — see
  [Why not a shell-profile export](#why-not-a-shell-profile-export). It trades a narrow,
  per-invocation exposure for an ambient one, and `~/.zshenv` here is a symlink into a
  synced repo.
- **A 1Password service account plus a pointer.** Deferred with triggers, not rejected —
  see [Why not a service account, yet](#why-not-a-service-account-yet). It buys audit and
  independent rotation but does not shrink the exposure set.
- **1Password Environments.** The vendor feature for this job, rejected circumstantially:
  `op environment read` ships only on the CLI's beta channel, and it composes with a
  service account rather than replacing one. Revisit trigger 4 covers it.
- **An out-of-tree file the consumer reads itself** — **adopted**, after first being
  rejected here. The rejection said it "needs a code change in every consumer," and that
  was wrong on the facts: `_secret_resolve.py` reads only the environment and is
  unaffected, and the three Class A instruments share one `resolve_key` — the two siblings
  load it out of this record's instrument by `spec_from_file_location` rather than
  reimplementing it. So the change is one branch in one function, not one per consumer.
  It was also rejected on a cost that turned out not to be payable: the operator declined
  to type the per-invocation prefix, which left a profile `export` as the only
  friction-free shape, and that is the broadest option on the table. Reading the file
  directly costs five lines and keeps the value's lifetime at one process.
