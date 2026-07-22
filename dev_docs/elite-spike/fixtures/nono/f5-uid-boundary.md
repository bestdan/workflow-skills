# F5 — two-uid boundary (agent-side, ground truth). Layer 2 of 2.

**Status: CONFIRMED for secrets, with two non-secret isolation gaps to close.**
Run as the real `agent` uid against the maintainer's real files.

This is the **uid layer** (plain agent shell, no sandbox). The **sandbox layer**
(same battery from inside `nono run` as agent, under the `claude` profile) is the
remaining follow-up.

## Results

| Check | Outcome |
| --- | --- |
| Agent login keychain scoped dump | **only `Claude Code-credentials`** — keychain-clean ✓ |
| `cat ~maintainer/.autopilot-sentinel` (0600) | denied ✓ |
| `cat ~maintainer/.config/gh/hosts.yml` (gh token, 0600) | denied ✓ |
| `ls ~maintainer/.ssh` (0700) | denied ✓ |
| `cat ~maintainer/.claude.json` (0600) | denied ✓ |
| read maintainer Claude token via maintainer keychain | denied ✓ |
| `cat ~maintainer/.gitconfig` (0644, non-secret) | **readable — gap** |
| `ls ~maintainer` (home 0750, group=staff) | **traversable — gap** |

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

## Remaining for a complete F5

- **Sandbox layer:** run this same battery from inside `nono run` as agent under
  the `claude` profile (agent must `nono pull nolabs-ai/claude` first). Expect the
  same denials **plus** confirmation that the System keychain
  (`/Library/Keychains`) is unreachable (profile grants only `~/Library/Keychains`).
- **Re-verify** traversal + `.gitconfig` denied after `chmod 700 ~maintainer`.
