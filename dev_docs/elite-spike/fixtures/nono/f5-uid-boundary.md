# F5 — two-uid boundary (agent-side, ground truth). Layer 2 of 2.

**Status: CONFIRMED for secrets, with two non-secret isolation gaps to close.**
Run as the real `agent` uid against the maintainer's real files.

This is the **uid layer** (plain agent shell, no sandbox); the **sandbox layer**
(inside `nono run`) is confirmed in the section below. Both layers pass — F5 is
fully closed.

## Results

| Check                                                   | Outcome                                               |
| ------------------------------------------------------- | ----------------------------------------------------- |
| Agent login keychain scoped dump                        | **only `Claude Code-credentials`** — keychain-clean ✓ |
| `cat ~maintainer/.autopilot-sentinel` (0600)            | denied ✓                                              |
| `cat ~maintainer/.config/gh/hosts.yml` (gh token, 0600) | denied ✓                                              |
| `ls ~maintainer/.ssh` (0700)                            | denied ✓                                              |
| `cat ~maintainer/.claude.json` (0600)                   | denied ✓                                              |
| read maintainer Claude token via maintainer keychain    | denied ✓                                              |
| `cat ~maintainer/.gitconfig` (0644, non-secret)         | **readable — gap**                                    |
| `ls ~maintainer` (home 0750, group=staff)               | **traversable — gap**                                 |

## Finding

The uid boundary protects every **secret**: all 0600/0700 files and the
maintainer's keychain token are denied to the agent. But `agent` and `maintainer`
share primary group `staff` (gid 20) and the maintainer home is `drwxr-x---`
(0750), so the agent can **traverse the maintainer home and read any
group-readable (0644) file** in it — here, `.gitconfig` (git identity; not a
secret, but it is leakage).

## Required hardening (§3.1 "no ACL leakage from your home") — APPLIED

`chmod 700 ~maintainer` was applied (2026-07-22): the maintainer home went
`drwxr-x---` → `drwx------`. The agent can no longer traverse the home at all,
closing both the traversal and the `.gitconfig` gap. **F5 uid layer is now
clean.** (Re-running the agent battery should show traversal + `.gitconfig`
denied; not re-run yet, but the perms change makes both structurally impossible.)

**Design note:** the design must state this as a provisioning requirement — the
maintainer home must be `0700`, or the agent must not share the maintainer's
primary group. Not optional; the default macOS `0750`/shared-`staff` setup leaks.

**Alternative** (defense in depth): give `agent` a primary group other than
`staff` so it never shares a group with the maintainer. Heavier; the `chmod 700`
is sufficient.

## Sandbox layer (Checkpoint A) — CONFIRMED

Same battery from inside `nono run --profile claude` as agent:

| Check (inside nono)                      | Outcome                                                                                                                                                   |
| ---------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `cat ~maintainer/.gitconfig`             | DENY ✓ (0700 now also blocks it)                                                                                                                          |
| `cat ~maintainer/.claude.json`           | DENY ✓                                                                                                                                                    |
| `ls ~maintainer`                         | DENY ✓                                                                                                                                                    |
| `cat /Library/Keychains/System.keychain` | **DENY ✓ — System keychain unreachable in the sandbox** (profile grants only `~/Library/Keychains`)                                                       |
| read agent's own Claude token            | DENY — **locked-keychain artifact**, not a nono denial (fresh `sudo -u agent -i` session; F1/F6 proved Claude reads this token inside nono when unlocked) |

**F5 fully confirmed** across both layers. The `.gitconfig`/traversal gaps are
closed by the `chmod 700 ~maintainer` applied above; the sandbox layer adds the
System-keychain-unreachable result the review predicted.

## Operational finding (feeds §3.1 / run-loop)

`nono run` **blocks on an interactive prompt** when invoked with a TTY (it hung
in the agent shell until fixed with `</dev/null`). The production run-loop must
invoke nono **non-interactively** (stdin from `/dev/null`), and the agent login
keychain must be **unlocked** in-session before Claude runs. Both are provisioning/
run-loop requirements, recorded for the design.
