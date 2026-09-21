---
created: 2026-09-20
---

# Design — out-of-tree plaintext keys

Status: accepted — adopted for the TypeSafe key by
[`decisions/2026-09-20-typesafe-key-out-of-tree.md`](../decisions/2026-09-20-typesafe-key-out-of-tree.md),
and carried into the contract as one of three supported plaintext shapes
Date: 2026-09-20
Extends: [`dev_docs/auth_key_access.md`](../auth_key_access.md) — this adds a
recommended shape at rung 1, and changes no rung, no name and no resolver.

## Problem

`auth_key_access.md` offers an operator who does not want to run a secret manager
exactly two plaintext shapes, and each carries an exposure the other does not:

- **A raw value in `.task-config.local.yml`** (rung 0) puts the secret **inside the
  repo tree**, where a directory-wide grep, an editor index, or a backup of that
  folder can read it.
- **`export $<NAME>` from the shell profile** (rung 1) takes it out of the tree and
  puts it in **every process the operator starts** — each agent tool call, each
  package postinstall, each `uv run` — and `/proc/<pid>/environ` of anything
  long-lived.

The contract names both costs honestly and leaves the operator to pick. In practice
both get picked for the wrong reason: the config is chosen because it is what the
template shows, and the export is chosen when someone notices the tree exposure and
reaches for the only documented alternative — trading a narrow, durable exposure for a
broad, ambient one.

There is a third shape that has neither cost, and the repo already uses it in one
place without naming it as a pattern:
`~/src/dotfiles/scripts/nightly-linear-tidy.sh` offers "a credentials file outside the
repo" as its fourth escape when `op` cannot prompt.

## Proposal

**A mode-600 file outside every repo tree, read per invocation into rung 1.**

```bash
# once
install -d -m 700 ~/.config/workflow-skills
install -m 600 /dev/null ~/.config/workflow-skills/typesafe_api_key
# paste the key in with an editor; never echo it into the shell

# per invocation
TYPESAFE_API_KEY=$(cat ~/.config/workflow-skills/typesafe_api_key) <command>
```

`$(cat …)` rather than bash's `$(<…)` shorthand, deliberately. Measured 2026-09-20 on
a box where `/bin/sh` is `dash`: `sh -c 'V=$(<f) printenv V'` prints an empty line and
**exits 0**, while the `cat` form prints the value. The shorthand fails silently rather
than loudly, and it fails in exactly the place this doc sends a reader who wants this
unattended — a crontab line runs under `/bin/sh`.

The three properties that make this worth writing down:

- **It is not in a checkout.** That closes the repo-tree vectors and only those: a
  checkout-wide grep, an editor's project index, and a backup of the repo folder no
  longer reach it. A home-wide grep or a backup of `$HOME` still does — `~/.config` is
  not a hiding place. Against other users, mode 600 under a 700 directory is what
  narrows it, and against root and your own processes nothing here does.
- **It is not exported.** A command-prefix assignment scopes the value to the invoked
  command **and its descendants, for that one invocation**. A profile `export` reaches
  every process of every login session for as long as the line is in the profile,
  including all the ones that have nothing to do with this key. The difference is
  scope and lifetime, not a count of processes.
- **It needs no code.** Rung 1 already reads `$<NAME>`, so every consumer works
  unchanged. No resolver, no new config key, no dependency, nothing to lapse between
  runs.

The cost, stated plainly: **you must type the prefix.** Forgetting it is a loud
failure only once the cleanup below is complete: with no raw value in any scanned
config and no pointer configured, the ladder falls through to its own "no key" error.
While either survives, forgetting the prefix succeeds silently through the other rung
and the operator never learns the narrowing is not in effect. That is friction the
export does not have, and the friction is the whole reason the value stays narrow.

### Why per-invocation rather than a wrapper or an export

A wrapper script that sets the variable and then runs the command is the same shape
with the friction removed, and it is fine. What it must not become is an export in a
profile: the moment the assignment leaves the command line, the value is inherited by
everything, and the proposal's second property is gone. If the friction is
intolerable, a per-project wrapper is the right relief; a profile export is not.

## Scope

This is a **recommendation for operators**, not a mechanism. It changes no code, and
it is not enforced anywhere — nothing can tell where an inherited `$<NAME>` came
from, which is precisely what makes rung 1 able to consume this at no cost.

It applies to any credential the contract covers. `linear.api_key` and
`typesafe.api_key` are the two live cases; a Jira or GitHub token would be the same.

## What this is not

- **Not a secret manager, and not a substitute for one.** A plaintext file on disk is
  readable by root and by anything running as the operator. This proposal narrows
  _location_; it does not narrow _principals_. Where an audit trail, independent
  revocation, or rotation-without-touching-disk matters, the answer is still a
  pointer plus a resolver (rungs 2–3).
- **Not a change to the ladder.** Rung 0 still beats rung 1, which is the one trap
  worth repeating: exporting or prefixing changes nothing while a raw value remains
  in a scanned config. The config line must go in the same change.
- **Unattended: cron yes, launchd no.** Not sourcing a profile and not running a
  command line are two different facts, and they come apart here. **cron** hands each
  entry to a shell (`$SHELL`, default `/bin/sh`), so a crontab line can carry the
  prefix assignment directly — subject to escaping `%`, which cron reads as a newline,
  and to the `sh` point above, since `/bin/sh` is where the `$(<…)` shorthand fails
  silently. **launchd** execs `ProgramArguments` with no shell at all, so it needs a
  wrapper script or an explicit `sh -c`. Where neither is wanted, a service-account
  token is still the answer — see
  [`auth_key_access.md`](../auth_key_access.md#unattended-and-cloud).

## The trap this must document

`local_config_paths` does not scan only the checkout it is handed. Measured
2026-09-20 against
`dev_docs/research/2026-09-17-jev-applications/references/jev-description-collision.py`:
called with a root pointing at an empty temporary directory, it still returned
`/home/dan/src/workflow-skills/dev_docs/tasks/.task-config.local.yml`, because the
second path is computed from `git rev-parse --git-common-dir` rather than from the
root. That measurement shows the main checkout's copy is **also** scanned; it cannot
show the worktree's own copy is skipped, because the empty root had none. Both are
scanned, and **the checkout you are standing in is read first** — `local_config_paths`
returns `[root, main]`, filtered to the ones that exist, and `resolve_key` returns on
the first raw hit. So a worktree's own copy shadows the main checkout's, and a raw
value in either one wins over rung 1.

So the instruction is not "delete the line from the local config". It is **delete it
from every copy the ladder scans — this checkout's and the main checkout's**.

Verify by running the consumer with the variable unset. What counts as a pass depends
on what else is configured, and "no key" is the right answer in only one of the two
cases:

| Configuration                 | A clean removal looks like                                                         |
| ----------------------------- | ---------------------------------------------------------------------------------- |
| No pointer configured         | `No TypeSafe key` — the ladder reaches its own error                               |
| An `op://` pointer configured | resolution **through the pointer** — an `op` approval, or an `op`-specific failure |

Anything that returns a key **without touching `op`** means a raw value is still in a
scanned config. That is the only failure this check can report, and it errs toward a
false alarm rather than a false clear: a raw value returns before the environment is
read, so the consumer cannot say "no key" while one survives anywhere the ladder looks.

Do not disable the other rungs to isolate rung 0. The step is easy to forget to undo,
and the alternative — a resolver trace naming which rung answered — does not exist in
this code and adding one means editing a frozen artifact, which the next section
refuses on its own grounds.

## Rejected alternatives

- **An out-of-tree _config_ file** — same file, but taught to the resolver as another
  `.task-config.local.yml` location. Rejected: it needs a code change in every
  consumer, and for a frozen research artifact that means editing evidence. Rung 1
  reaches outside a checkout today with no change at all, which is the whole reason
  the proposal wears rung 1 rather than inventing a path.
- **A profile export** — see above. Trades a narrow exposure for an ambient one.
- **1Password Environments** — the vendor feature for exactly this job, and the
  rejection is circumstantial rather than a judgment that it is wrong. Two grounds. It
  composes with a service account rather than replacing one, so on a headless host it
  still needs the full service-account apparatus. And it is **beta**:
  [`op environment read`](https://www.1password.dev/cli/reference/management-commands/environment/)
  ships only on the CLI's beta channel (`2.33.0-beta.02` or later), so stable 2.39.0
  does not have it and no stable upgrade will — adopting it means pinning a frozen
  artifact to a pre-release CLI. Revisit when it reaches stable.

  Checked 2026-09-20, because the obvious probe misleads twice. `op --help` on 2.39.0
  lists no `environment`, but `op env` exists **unlisted** — a different thing
  (`op env ls`, which lists shell variables already holding `op://` references, a
  companion to `op run`). And the version number reads backwards: stable 2.39.0 is
  numerically past `2.33.0-beta.02` and still lacks the command, because the gate is
  the channel, not the number.
- **Going straight to a pointer plus a service account** for every key. The right
  answer when the properties under **What this is not** are wanted, and the wrong
  default: it makes a hand-run script depend on a network read that fails closed, and
  it prices in a vault and a token for a key that may not warrant either.

## Open questions

- **Where should the file live?** `~/.config/<tool>/<name>` follows XDG and is what
  the example uses. `nightly-linear-tidy.sh` has its own `CREDS_FILE` convention;
  if these should be one path, that is a dotfiles change, not a change here.
- ~~**Should `auth_key_access.md` recommend this, or only describe it?**~~ Resolved in
  this change: the contract recommends it. Its
  [Three shapes](../auth_key_access.md#three-shapes-and-which-to-pick) table names this
  one "the default worth reaching for" while leaving all three supported.
- **Is a per-project wrapper script worth shipping**, or does each operator write
  their own? Shipping one makes the friction argument moot and the "not an export"
  property harder to lose by accident.
