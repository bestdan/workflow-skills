---
created: 2026-11-03
status: accepted
tracks: https://github.com/bestdan/workflow-skills/issues/956
---

# One jail for the orchestrator on macOS and Linux: nono

**Recommendation: run the detached orchestrator under nono on both operating
systems, with nono as its only enforcing layer. Ship Linux first, replacing
#1349's bwrap renderer. Move macOS off the Seatbelt renderer as a second step.**
Confidence is **medium** overall. It is high for Linux, where every load-bearing
claim was measured on the Linux test host (Ubuntu 24.04, kernel 7.0), and lower
for macOS, where nothing was run. Evidence:
[`../research/2026-11-03-orchestrator-jail-nono-vs-bwrap.md`](../research/2026-11-03-orchestrator-jail-nono-vs-bwrap.md)
(cited below as E0–E10).

## Why

- **The two-layer plan has no second layer on either OS.** On macOS a nested
  Seatbelt profile is refused (PR #212). Inside nono on Linux, Claude Code's
  bwrap cannot mount, and its proxy cannot listen (E3, E4b, E4d). On this host
  Claude Code's sandbox does not start even unjailed, because `socat` is
  missing (E5). And where it does run, it filters Bash only: the `claude`
  process, its MCP servers and its hooks are outside it (Claude Code docs, in
  E5). #956's "egress left to the harness allowlist" is therefore open on both
  OSs today, and `render-settings` makes it fail open
  (`spawn-orchestrator.sh:1255`).
- **nono closes egress for the whole process tree, on both OSs, with no
  privileges.** On Linux, verified: an allowlisted host reached, another host
  got `403`, a raw IP and a UDP socket were refused (E4c), and `claude -p` ran
  under it (E5a). On macOS, from documents: the same proxy with the port pin PR
  #212 recommended as option A. It needs no AppArmor profile and no user
  namespace.
- **One renderer instead of two.** A nono profile is one JSON file with a small
  `linux` / `unsafe_macos_seatbelt_rules` tail per OS. The bwrap route keeps the
  Seatbelt renderer on macOS and adds a second, unrelated one on Linux.
- **bwrap's advantages are real but smaller.** It can keep one file read-only
  inside a writable directory, and nono on Linux cannot (E9, E10). It would let
  Claude Code's sandbox nest (E0), but that sandbox only filters Bash and is not
  installable here without `sudo`.

## The options

|                                    | **A. nono, both OSs**                                                                                   | **B. bwrap on Linux, Seatbelt on macOS** (#956 as decided)                                                                |
| ---------------------------------- | ------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| §1 write scopes and harness paths  | Yes (E4a, E4e, E5a)                                                                                     | Yes                                                                                                                       |
| One read-only file inside a RW dir | macOS yes (Seatbelt deny); **Linux no** (E9, E10), so two layouts must change (below)                   | Yes, both (E9; Seatbelt today)                                                                                            |
| Starting an unjailed unit or job   | Linux: closed with `af_unix_mediation: pathname` (E7). macOS: launchd denies must be added as raw rules | Linux: closed only with a tmpfs over `$XDG_RUNTIME_DIR` (E8). macOS: closed today                                         |
| Exec wall                          | Shims; a staged copy runs (E6). The socket rule above carries the integrity claim                       | Linux has no per-directory exec deny (inferred), so the same holds. macOS: RW scopes never exec, which is stronger        |
| Claude Code's inner sandbox        | Cannot start on either OS; turned off on purpose                                                        | Cannot start on macOS; may start nested on Linux once `socat` is installed (unmeasured)                                   |
| Egress                             | **Closed by host allowlist on both OSs**, for the whole tree                                            | Open on macOS. On Linux, Bash only, and only after `socat` plus `failIfUnavailable` and `allowUnsandboxedCommands: false` |
| Privileges and install             | Unprivileged. Install a pinned binary (tarball, brew or .deb). No AppArmor                              | AppArmor `userns` profile on Ubuntu 24.04+ (installed on the test host). `socat` for any egress                           |
| Third-party risk                   | Pre-1.0, weekly releases, an update check unless `NONO_NO_UPDATE_CHECK` is set                          | None beyond distro packages                                                                                               |
| Codepaths to maintain              | One renderer, one smoke                                                                                 | Two renderers, two smokes                                                                                                 |
| Cost to reach Linux `go`           | About 15 points (plan items 1–4)                                                                        | About 13 points (#1349 + #1350 + #1351), plus the socket fix #1349 does not list                                          |

Both options cost about the same to reach a Linux `go`. A adds about 7 points
to move macOS, and that work closes macOS egress. B would need PR #212's option
A, built by hand, to close it.

## What changes in the posture

- **nono is the only enforcing layer.** `render-settings` emits
  `sandbox.enabled: false`, and the profile header says why, as
  `orchestrator.sb.tmpl`'s does today. Keeping it `true` means a dead layer
  reported as live (PR #212's point), and on Linux it fails open.
- **The egress allowlist moves into the nono profile.** The hosts
  `render-settings` resolves today (launch-runtime.md §2) become `allow_domain`
  entries. This reverses #956's "parity, egress open" decision, so it is the
  owner's call (open question 2).
- **Two layouts change, because Landlock cannot deny inside a grant.**
  - The supervisor-state ledger moves out of the run worktree, into a
    supervisor-owned directory the profile never grants. Its deny exists only
    so the agent cannot write it. A file the jail cannot see does that on both
    OSs, and removes a renderer special case.
  - On Linux, `--cred-ro` becomes narrower write grants. Grant the `~/.claude`
    subdirectories Claude Code writes, and leave `.credentials.json` outside
    every write grant. Which subdirectories, measured by a real run, is part of
    the renderer task. If the list cannot be closed, the fallback is to accept a
    writable token on Linux and say so (open question 3).
- **Linux profiles set `linux.af_unix_mediation: "pathname"`**, granting only
  the sockets the harness needs. Without it a staged `systemctl` starts unjailed
  units (E7).
- **macOS profiles carry the template's launchd, LaunchServices and
  `process-exec` denies** in `unsafe_macos_seatbelt_rules`. That this field is
  honoured at the top level of a file profile is untested, and the macOS task
  must prove it.
- **The profile is rendered and vendored, never pulled.** No `extends:
  nolabs-ai/claude`. The registry pull prompts, exits without a TTY, and grants
  the whole login keychain on macOS. The renderer emits the full grant list
  itself, as E5 did.

## Plan

Each item is a child of #956. Estimates use the issues' existing scale.

1. **Move the supervisor-state ledger out of the run worktree (#1360, est 2).**
   This is OS-agnostic, can land first, and also simplifies the Seatbelt
   template. Blocks 2.
2. **#1349, re-scoped: nono profile renderer and confinement smoke, Linux
   first (est 5).** `spawn-orchestrator.sh render-nono-profile` writes a JSON
   profile from §1's table: RW run and worker worktrees, `$TMPDIR` and the
   harness runtime paths; RO repo; credential stores narrowed as above; the
   `allow_domain` list; Unix-socket mediation; the exec deny list as a second
   line. The smoke runs through the exact wrapper and asserts:
   - a write outside the grants is denied, and a write inside succeeds;
   - the credential file is not writable;
   - a non-allowlisted host and a raw IP are refused, and an allowlisted host
     reaches, which proves the proxy was alive;
   - a staged `systemctl` cannot reach the user bus;
   - `claude -p 'ok' --max-turns 1` succeeds.

   Drop the bwrap branch, the AppArmor `no-go` text and the `--unshare-net`
   test from the issue. Its acceptance tests become renderer fixtures, and
   "the profile grants no `$HOME` write" replaces "nothing broader than `/`
   read-only".
3. **#1350, unchanged in scope (est 5).** The generated wrapper runs `nono run
   --profile <file> --silent -- claude -p …` where macOS runs `sandbox-exec -f`.
   Set `NONO_NO_UPDATE_CHECK=1` and `NONO_NO_MIGRATE=1` in the unit's
   environment. Liveness, teardown and the PID record are unaffected: the
   wrapper still waits on its child.
4. **#1351, unchanged in scope (est 3).** The broker stays unjailed. The
   "jailed agent cannot start or stop the broker unit" test is the bus-socket
   assertion from 2, run against the broker's unit name.
5. **macOS migration (#1361, est 5).** Switch `write-launch`, the confinement
   smoke and pre-flight §5's consent probe to the same renderer on macOS, and
   prove the raw launchd denies hold. This closes macOS egress. It is optional
   for correctness, because Seatbelt keeps today's posture, and required for
   one codepath.
6. **Delete the Seatbelt renderer (#1362, est 2), after 5 passes on a Mac.** That
   removes `orchestrator.sb.tmpl`, `render-profile` / `check-profile`,
   `smoke-confinement.sh`, and the profile-coupled tests: most of
   `test-spawn-orchestrator-profile.sh` (132 references), plus 17 in
   `test-preflight-consent.sh` and about 40 across the alarm, exit-contract,
   handoff, status-report and supervisor suites. The doctor, reserve and restack
   suites have none. Until then the renderer stays as the macOS path, and the
   "RETIRED" headers are rewritten to point here instead of at aiutopilot, which
   never built it.

Items 1 to 4 total 15 points, against 13 for #1349–#1351 as filed. The extra
two points are item 1.

### Pre-flight

Section 3b's launcher check becomes: `nono` on `PATH` (or at the pinned path),
`nono --version` equal to the pinned version, and the binary's SHA-256 equal to
the pinned one. On Linux it also requires Landlock in
`/sys/kernel/security/lsm` and ABI 4 or later; below that, TCP rules fail
closed and the run cannot reach the API. Any miss is `no-go`, and the fix text
names the install command. Then `nono profile validate` on the rendered profile,
then the smoke from item 2. An AppArmor `userns` probe is no longer needed for
the jail.

## Owner decisions (2026-11-03)

- **Adopt nono, Linux first** (question 1). Pin its version and checksum.
- **Egress parity first** (question 2): ship with `network.block: false`, then
  turn the allowlist on as a follow-up once a real run (#1024) has shown which
  hosts it needs.
- **Install from a pinned tarball** (question 4), into a plugin-managed
  directory with no `sudo`.
- **A writable credential file is acceptable on Linux** (question 3), as the
  documented fallback. The owner also asked whether a 1Password service account
  could supply the credential instead. It can, and that is the preferred route:
  see "Credential source" below.

## Credential source

The jail does not need the `/login` credential file at all. `claude setup-token`
issues a one-year OAuth token, saves it nowhere, and Claude Code reads it from
`CLAUDE_CODE_OAUTH_TOKEN`. That variable ranks above the `/login` credential in
the documented precedence
([authentication](https://code.claude.com/docs/en/authentication.md), sections
"Generate a long-lived token" and "Authentication precedence"). So:

- The token lives in a secret store. The unjailed wrapper resolves it on each
  wake and exports it into the jailed `claude -p` only. The plugin takes a
  **token command** (`--claude-token-cmd '<cmd>'`) rather than naming a store,
  so a host using a 1Password service account passes an `op read …`
  invocation, and another host passes its own.
- `~/.claude/.credentials.json` is then left out of every grant: not writable,
  and not readable either. That closes question 3 instead of accepting it.
- The run no longer depends on a refreshing OAuth credential, which is the
  failure that halted detached run #2 with a `401` (launch-runtime.md,
  "supervisor halt").

Costs and unknowns:

- The token is still readable inside the jail, through the environment of the
  orchestrator and every child. That is the same exposure as the file today.
  A leaked token lasts a year unless revoked, against a few hours for the
  `/login` access token.
- `claude --bare` does not read the variable. The orchestrator does not use
  `--bare`.
- **Unmeasured:** that `claude -p` with the variable set neither reads nor
  writes `.credentials.json`. #1349's smoke must prove it, by running with the
  file outside every grant.
- The token command runs in the unjailed wrapper, so the service-account
  credential it uses never enters the jail.

## Questions put to the owner

Questions 1–4 are answered under "Owner decisions" above. 5 and 6 stay open.

1. **Is a pre-1.0 third-party binary acceptable as the boundary?** It is
   mitigated by pinning the version and checksum and vendoring the profile, and
   bounded by nono's own scope: it is a capability sandbox, not a VM. If not,
   take option B with E8's tmpfs fix and accept open egress on both OSs.
2. **Close egress now?** It was decided open on 2026-11-03 for parity. nono
   closes it on both OSs, but every host a run touches must be on the list, and
   a missed host fails the run rather than leaking. nono can be run with
   `network.block: false` to keep parity first.
3. **On Linux, if the narrowed `~/.claude` grants cannot be closed, accept a
   writable credential file?** The token is readable inside the jail either
   way. Read-only only stops it being overwritten.
4. **Install route:** a pinned tarball in a plugin-managed directory (no
   `sudo`, the version is under our control), or brew / .deb (updates arrive
   outside the pin)?
5. **Keep the bwrap AppArmor profile on the Linux host?** The jail no longer
   needs it. Claude Code's own interactive sandbox does, together with `socat`.
6. **Evaluate Anthropic's sandbox runtime as well?** Claude Code's docs offer it
   for wrapping "the Claude Code process itself". It was not looked at here.

## Not decided here

- Per-worker credential subtraction (launch-runtime.md §4). nono's command
  sandboxes may express it, but that is a separate change.
- Whether verify can move back inside the jail. On Linux nothing in `check.sh`
  needs a nested sandbox, but the broker also exists to run verify with full
  network, and #1351 keeps it.

## Graduation

- A decision record: nono as the single enforcing layer on both OSs, and why
  Claude Code's sandbox is off inside it.
- launch-runtime.md "Sandbox profile" is rewritten to one layer, with the
  Linux deny-in-grant limit and the socket rule. The "two layers" text and the
  harness srt-mux row go.
- The `spawn-orchestrator.sh` and `orchestrator.sb.tmpl` headers point at the
  decision record instead of aiutopilot.
