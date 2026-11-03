---
created: 2026-11-03
---

# Orchestrator jail: nono or Seatbelt + bwrap?

**Measured 2026-11-03** on the Linux test host (Ubuntu 24.04.5, kernel 7.0,
Claude Code 2.1.317, nono 0.81.0 from a release tarball in a scratch dir). No
macOS host was available, so every macOS statement below is from documents or
from PR #212's earlier measurements, and is marked that way. This record backs
the design in
[`../designs/2026-11-03-orchestrator-jail.md`](../designs/2026-11-03-orchestrator-jail.md).

## Verdict

- **Claude Code's own sandbox cannot be a layer under either Linux jail
  candidate as things stand, and under nono it cannot start at all.** A Landlock
  filesystem domain forbids the mount calls bwrap makes, so Claude Code's inner
  bwrap fails inside nono (E2, E3, E4b; verified). Under an outer bwrap it could
  start (nested bwrap works, E0), but on this host it does not start anywhere,
  because `socat` is not installed (E5; verified). Even when it runs, it covers
  Bash commands only, not the `claude` process, its MCP servers or its hooks
  (Claude Code docs; verified).
- **nono enforces a host allowlist on Linux today.** An allowlisted host
  reached, another host got `403` from the proxy, a raw IP was refused, and a
  UDP socket could not be created (E4c; verified). The aiutopilot charter's
  claim that "nono's network containment does not hold on Linux" does not
  describe 0.81.0's behaviour; its evidence is not in that repo (inferred that
  it was about an older version or about Unix sockets, see E7).
- **nono on Linux cannot keep one file read-only inside a writable directory.**
  `--read-file` inside `--allow` leaves the file writable (E9), and a
  `filesystem.deny` there makes nono refuse to start (E10; verified). bwrap can
  (E9). That breaks two rules launch-runtime.md depends on: `--cred-ro` and the
  supervisor-state deny inside the run worktree.
- **Neither jail walls exec on Linux the way Seatbelt does.** nono's command
  denies are shims, beaten by a staged copy (E6); bwrap has no per-directory
  `noexec` either (inferred). What stops a staged `systemctl` from starting an
  unjailed unit is hiding the user bus socket: nono's `af_unix_mediation:
  pathname` (E7) or a tmpfs over `$XDG_RUNTIME_DIR` in bwrap (E8). Both are
  verified, and both jails leak the bus without them.
- **aiutopilot never implemented nono.** It adopted nono in a design document
  only (details below; verified).

## The current design and its gap

- The filesystem table, including the three harness runtime paths, is
  `skills/auto-pilot/references/launch-runtime.md:177-262`. The credential
  file-RO / state-RW split is §3 (`:297`), and the supervisor-state deny is
  `scripts/orchestrator.sb.tmpl` (`@@SUPERVISOR_STATE_DENY@@`), which
  `spawn-orchestrator.sh:959-970` resolves to `<workdir>/.auto-pilot/supervisor-state`,
  inside the writable run worktree.
- The Seatbelt profile grants `(allow network-outbound)` outright
  (`orchestrator.sb.tmpl:57`). The header (`spawn-orchestrator.sh:7-18`) says the
  jail has one enforcing layer and egress is unfiltered.
- `render-settings` still emits `{"sandbox":{"enabled":true,…}}` with neither
  `failIfUnavailable` nor `allowUnsandboxedCommands`
  (`spawn-orchestrator.sh:1255`), so wherever Claude Code's sandbox fails to
  start, the harness runs commands unsandboxed.
- PR #212 (open, docs only) measured on macOS that a nested Seatbelt profile is
  refused unless it is identical to the outer one
  (`sandbox_apply: Operation not permitted`). Claude Code's sandbox therefore
  dies inside the jail, and the harness fails open. Its option A, a loopback
  egress proxy with `network-outbound` pinned to the proxy's port, is how nono
  works on macOS (below).
- #956's decisions (2026-11-03) for Linux: bwrap with an AppArmor `userns`
  profile, egress parity with macOS (filesystem and exec only, egress "left to
  the harness allowlist"), a `systemd-run --user` timer, and a GitHub-comment
  alarm. #1348 and #1352 are closed. #1349, #1350 and #1351 are open.

## What aiutopilot says about nono, and what it built

Read through `gh api repos/bestdan/aiutopilot/...` on 2026-11-03. The last
commit is 2026-08-23.

- `docs/charter/auto-pilot-e-lite-design-2026-07-21.md` §3.2 adopts nono "at the
  defense-in-depth tier" as "the sandbox + network layer over a hand-rolled
  Seatbelt profile", with a local proxy enforcing a host allowlist
  (`api.anthropic.com`, `github.com`, `api.github.com`, `api.linear.app`,
  loopback). It says this "closes the `gh` hole" and that "the hand-rolled
  `orchestrator.sb.tmpl` renderer is retired."
- The same section bounds the adoption: allowlist by host only, because nono's
  path-level endpoint policy is bypassable; no credential injection, because
  nono is "self-declared alpha on an active escape-and-fix cadence"; pin the
  binary, vendor the profile, use only the ephemeral CA. It also says:
  "**macOS-only** assumption is load-bearing — nono's network containment does
  not hold on Linux (SR-1/SR-2)."
- The cited evidence is not in the repo. The tree at `main` has no
  `docs/charter/nono-evaluation.md`, no `dev_docs/elite-spike/`, and no
  `nono_security_review.md`. So SR-1 and SR-2 cannot be read.
- Nothing implements it. `gh search code nono --repo bestdan/aiutopilot` returns
  three Markdown files: the charter and two agent-account task cards. None of
  the 211 commit messages mentions nono. The charter's §3.2 also records that
  "the interactive+launchd matrix" and the write loop through the proxy were
  still to be run.
- aiutopilot's substrate is different: a dedicated non-admin `agent` user with
  its own keychain, which is what makes nono's broad `~/Library/Keychains` grant
  acceptable there.

## nono today

All from `github.com/nolabs-ai/nono` at `main` (shallow clone, 2026-11-03) and
its release list, unless marked.

| Property             | Finding                                                                                                                                                                                                                                                                                                                                                                                |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Platforms            | Linux kernel 5.13+ (Landlock), macOS (Seatbelt via `sandbox_init`), WSL2 at reduced coverage; no native Windows (`docs/cli/getting_started/installation.mdx`). No minimum macOS version is stated.                                                                                                                                                                                     |
| Linux feature floors | TCP port rules need Landlock ABI 4 (6.7+); signal and abstract-socket scoping need ABI 6 (`docs/cli/internals/landlock.mdx`). The test host reports ABI 8 (E2).                                                                                                                                                                                                                        |
| Filesystem           | `--allow` (RW), `--read`, `--write`, per-file variants, Unix-socket grants. On Linux "deny is expressed by omitting grants" (`internals/capability-manifest.mdx:58`). Landlock read includes `Execute` (`internals/landlock.mdx`).                                                                                                                                                     |
| Network              | Domain allowlist through a CONNECT proxy in the unsandboxed supervisor; the child may reach only the proxy port. Linux enforces it with Landlock plus a seccomp filter that denies Internet sockets and checks every `connect()` destination; macOS with `(allow network-outbound (remote tcp "localhost:PORT"))` (`features/networking.mdx`, `internals/security-model.mdx:240-251`). |
| Exec denies          | `command_policies` shims plus a direct-path deny; documented as not covering "renamed binaries" (`usage/flags.mdx:692-699`).                                                                                                                                                                                                                                                           |
| macOS escape hatch   | A user profile may append raw Seatbelt rules (`unsafe_macos_seatbelt_rules`, `features/tool-sandbox.mdx:112`). The generated macOS profile allows `process-exec*` and `mach-lookup`, denying only keychain services (`crates/nono/src/sandbox/macos.rs:529-582`); it has no launchd deny.                                                                                              |
| Privileges           | Unprivileged: Landlock and seccomp need no user namespace. Namespace isolation is opt-in (`--isolate`) and on Ubuntu 24.04 needs its own AppArmor profile (`features/namespace-isolation.mdx:100-112`).                                                                                                                                                                                |
| Claude Code          | nono's docs: "prefer using `nono` as the only sandbox layer. Leave Claude Code's built-in sandbox disabled" (`clients/claude.mdx`). The packaged `nolabs-ai/claude` profile is pulled from a registry and prompts; non-TTY runs exit instead.                                                                                                                                          |
| Install              | `brew install nono` (macOS, Linux); `.deb`/`.rpm`; release tarballs for `x86_64`/`aarch64` Linux (gnu, musl) and macOS; `curl … \| sh`; Nix. SHA256SUMS ships with each release.                                                                                                                                                                                                       |
| Maturity             | Apache-2.0, Rust, about 4.8k stars, created 2026-01-31. Pre-1.0: "In the lead-up to a 1.0 release, APIs are stabilizing" (README). 31 minor releases from v0.50.0 (2026-05-08) to v0.81.0 (2026-10-29), roughly weekly. Last push 2026-11-03.                                                                                                                                          |
| Side effects         | Writes `~/.config/nono/` and `~/.local/state/nono/` (audit, sessions) and runs an update check that stores a UUID; `NONO_NO_UPDATE_CHECK` disables it (`usage/flags.mdx:1721`). Observed after E4.                                                                                                                                                                                     |

## Experiments

Run on the Linux test host. Scripts were run with the Claude Code Bash sandbox
disabled, except E1's first run. `$HOME` replaces the home path in the output.

### E0. Host state, and plain and nested bwrap

```console
$ uname -r; rg PRETTY /etc/os-release; cat /sys/kernel/security/lsm
7.0.0-42-generic
PRETTY_NAME="Ubuntu 24.04.5 LTS"
lockdown,capability,landlock,yama,apparmor,bpf,ima,evm
$ sysctl kernel.apparmor_restrict_unprivileged_userns kernel.unprivileged_userns_clone user.max_user_namespaces
kernel.apparmor_restrict_unprivileged_userns = 1
kernel.unprivileged_userns_clone = 1
user.max_user_namespaces = 116127
$ bwrap --version; command -v nono || echo "nono: not installed"
bubblewrap 0.9.0
nono: not installed
$ cat /etc/apparmor.d/bwrap
abi <abi/4.0>,
include <tunables/global>
# Lets bubblewrap create the user namespace it sandboxes into, under
# kernel.apparmor_restrict_unprivileged_userns=1. Installed for the auto-pilot
# Linux jail (bestdan/workflow-skills #1349).
profile bwrap /usr/bin/bwrap flags=(unconfined) {
  userns,
  include if exists <local/bwrap>
}
$ bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/true; echo "rc=$?"
rc=0
$ bwrap --ro-bind / / --dev /dev --proc /proc bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/true; echo "rc=$?"
rc=0
```

Plain and nested bwrap both work with the AppArmor profile installed.

### E1. Is this session's own Bash sandbox active?

```bash
# e1-harness-nesting.sh
echo "userns: $(readlink /proc/self/ns/user)"
echo "mntns:  $(readlink /proc/self/ns/mnt)"
probe="$HOME/.sbx-probe-e1"
if : >"$probe" 2>/dev/null; then echo "write to \$HOME: allowed"; rm -f "$probe"
else echo "write to \$HOME: denied"; fi
bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/true
echo "bwrap inside this shell: rc=$?"
```

Sandboxed and unsandboxed runs printed the same thing:

```
userns: user:[4026531837]
mntns:  mnt:[4026531832]
write to $HOME: allowed
bwrap inside this shell: rc=0
```

The "sandboxed" call ran in the init namespaces and could write `$HOME`. E5
shows why: Claude Code's Linux sandbox needs `socat`, which is not installed.

### E2. Can a Landlock-confined process start bwrap?

`landlock_exec.py` applies a ruleset with raw syscalls, then execs its
arguments:

```python
"""Apply a Landlock ruleset (or only no_new_privs) to this process, then exec argv.

usage: python3 -I landlock_exec.py MODE -- CMD [ARGS...]
  MODE = none     exec CMD unchanged (control)
         nnp      set PR_SET_NO_NEW_PRIVS only
         fs       nnp + Landlock: handle every fs right the ABI knows;
                  allow read+exec on /, full access on /tmp and /dev
         fsproc   as fs, plus full access on /proc
         net      nnp + Landlock: handle TCP connect/bind only (ABI >= 4),
                  allow connect to 443 only; no fs rights handled
x86_64 syscall numbers. Never needs root.
"""

import ctypes
import os
import struct
import sys

SYS_create, SYS_add_rule, SYS_restrict = 444, 445, 446
PR_SET_NO_NEW_PRIVS = 38
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long


def sc(*args):
    r = libc.syscall(*args)
    if r < 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return r


def abi():
    return libc.syscall(SYS_create, None, ctypes.c_size_t(0), ctypes.c_uint32(1))


FS = {
    "EXECUTE": 1,
    "WRITE_FILE": 2,
    "READ_FILE": 4,
    "READ_DIR": 8,
    "REMOVE_DIR": 16,
    "REMOVE_FILE": 32,
    "MAKE_CHAR": 64,
    "MAKE_DIR": 128,
    "MAKE_REG": 256,
    "MAKE_SOCK": 512,
    "MAKE_FIFO": 1024,
    "MAKE_BLOCK": 2048,
    "MAKE_SYM": 4096,
    "REFER": 8192,
    "TRUNCATE": 16384,
    "IOCTL_DEV": 32768,
}


def fs_all(v):
    m = 0
    for name, bit in FS.items():
        if name == "REFER" and v < 2:
            continue
        if name == "TRUNCATE" and v < 3:
            continue
        if name == "IOCTL_DEV" and v < 5:
            continue
        m |= bit
    return m


def nnp():
    if libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "prctl")


def add_path(rs, path, access):
    fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    attr = struct.pack("=Qi", access, fd)  # packed landlock_path_beneath_attr
    buf = ctypes.create_string_buffer(attr, len(attr))
    sc(SYS_add_rule, rs, ctypes.c_int(1), buf, ctypes.c_uint32(0))
    os.close(fd)


def main():
    mode = sys.argv[1]
    cmd = sys.argv[sys.argv.index("--") + 1 :]
    v = abi()
    print(f"[landlock_exec] mode={mode} abi={v}", file=sys.stderr)
    if mode in ("nnp", "fs", "fsproc", "net"):
        nnp()
    if mode in ("fs", "fsproc"):
        handled = fs_all(v)
        attr = struct.pack("=QQQ", handled, 0, 0)
        buf = ctypes.create_string_buffer(attr, len(attr))
        rs = sc(SYS_create, buf, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
        add_path(rs, "/", FS["READ_FILE"] | FS["READ_DIR"] | FS["EXECUTE"])
        add_path(rs, "/tmp", handled)
        add_path(rs, "/dev", handled)
        if mode == "fsproc":  # also let it write /proc (uid_map, setgroups)
            add_path(rs, "/proc", handled)
        sc(SYS_restrict, rs, ctypes.c_uint32(0))
    elif mode == "net":
        if v < 4:
            sys.exit("ABI < 4: no TCP rules")
        attr = struct.pack("=QQQ", 0, 1 | 2, 0)  # BIND_TCP | CONNECT_TCP
        buf = ctypes.create_string_buffer(attr, len(attr))
        rs = sc(SYS_create, buf, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
        net = struct.pack("=QQ", 2, 443)  # landlock_net_port_attr: CONNECT_TCP, port
        nbuf = ctypes.create_string_buffer(net, len(net))
        sc(SYS_add_rule, rs, ctypes.c_int(2), nbuf, ctypes.c_uint32(0))
        sc(SYS_restrict, rs, ctypes.c_uint32(0))
    os.execvp(cmd[0], cmd)


main()
```

```bash
# e2-landlock-bwrap.sh
LL=/tmp/claude/exp/landlock_exec.py
BW=(bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/true)
for mode in none nnp fs net; do
  echo "== mode=$mode"
  out="$(python3 -I "$LL" "$mode" -- "${BW[@]}" 2>&1)"; rc=$?
  printf '%s\n' "$out"; echo "bwrap rc=$rc"
done
echo "== fs arm proves Landlock is live: write to \$HOME"
python3 -I "$LL" fs -- /usr/bin/touch "$HOME/.landlock-probe-e2" 2>&1; echo "touch \$HOME rc=$?"
echo "== fs arm: write to /tmp"
python3 -I "$LL" fs -- /usr/bin/touch /tmp/claude/exp/landlock-probe-e2 2>&1; echo "touch /tmp rc=$?"
echo "== fs arm: unshare a user namespace only (no mount)"
python3 -I "$LL" fs -- python3 -I -c 'import os; os.unshare(os.CLONE_NEWUSER); print("userns ok")' 2>&1; echo "rc=$?"
echo "== fs arm: bwrap without --proc/--dev (bind only)"
python3 -I "$LL" fs -- bwrap --ro-bind / / /usr/bin/true 2>&1; echo "rc=$?"
```

```
== mode=none
[landlock_exec] mode=none abi=8
bwrap rc=0
== mode=nnp
[landlock_exec] mode=nnp abi=8
bwrap rc=0
== mode=fs
[landlock_exec] mode=fs abi=8
bwrap: setting up uid map: Permission denied
bwrap rc=1
== mode=net
[landlock_exec] mode=net abi=8
bwrap rc=0
== fs arm proves Landlock is live: write to $HOME
[landlock_exec] mode=fs abi=8
/usr/bin/touch: cannot touch '$HOME/.landlock-probe-e2': Permission denied
touch $HOME rc=1
== fs arm: write to /tmp
[landlock_exec] mode=fs abi=8
touch /tmp rc=0
== fs arm: unshare a user namespace only (no mount)
[landlock_exec] mode=fs abi=8
userns ok
rc=0
== fs arm: bwrap without --proc/--dev (bind only)
[landlock_exec] mode=fs abi=8
bwrap: setting up uid map: Permission denied
rc=1
```

`no_new_privs` alone and a network-only Landlock domain leave bwrap working. A
filesystem domain lets the process create a user namespace but stops bwrap
writing its `uid_map`, since `/proc` was only readable.

### E3. With `/proc` writable, does bwrap get past the mount step?

```bash
# e3-landlock-proc.sh
LL=/tmp/claude/exp/landlock_exec.py
echo "== fsproc: bwrap --ro-bind / / --dev /dev --proc /proc true"
python3 -I "$LL" fsproc -- bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/true 2>&1; echo "rc=$?"
echo "== fsproc: bwrap --ro-bind / / true"
python3 -I "$LL" fsproc -- bwrap --ro-bind / / /usr/bin/true 2>&1; echo "rc=$?"
```

```
== fsproc: bwrap --ro-bind / / --dev /dev --proc /proc true
[landlock_exec] mode=fsproc abi=8
bwrap: Failed to make / slave: Operation not permitted
rc=1
== fsproc: bwrap --ro-bind / / true
[landlock_exec] mode=fsproc abi=8
bwrap: Failed to make / slave: Operation not permitted
rc=1
```

The run also had a third arm, a hand-written unshare-and-mount in Python. It
failed earlier, on writing `/proc/self/setgroups` (`PermissionError`), so it
says nothing more. The bwrap arms are the result. bwrap's first `mount()` is
refused, which matches the kernel's Landlock documentation: a thread with
filesystem restrictions "cannot modify filesystem topology, whether via
mount(2) or pivot_root(2)"
([docs.kernel.org/userspace-api/landlock.html](https://docs.kernel.org/userspace-api/landlock.html)).
**So any Landlock filesystem jail, nono's included, stops Claude Code's inner
bwrap sandbox from starting.** This is the Linux form of PR #212's nested
Seatbelt finding.

### E4. nono 0.81.0 on Linux: filesystem, nesting, egress, listen, Unix socket

```console
$ curl -fsSL -o nono.tgz https://github.com/nolabs-ai/nono/releases/download/v0.81.0/nono-v0.81.0-x86_64-unknown-linux-gnu.tar.gz
$ rg x86_64-unknown-linux-gnu SHA256SUMS.txt; sha256sum nono.tgz
7ecc33ff9c349bdfa84b4496e7a7555f67bcc33f1e125c7df9efb653c6c4e569  nono-v0.81.0-x86_64-unknown-linux-gnu.tar.gz
7ecc33ff9c349bdfa84b4496e7a7555f67bcc33f1e125c7df9efb653c6c4e569  nono.tgz
$ ./nono --version
nono 0.81.0
```

The first attempt ran from a directory nono had not been granted. Every call
then failed with `nono: CWD access requires --allow-cwd in non-interactive
mode`. The script below `cd`s into the granted directory first, as the
orchestrator runs from its worktree.

```bash
# e4-nono.sh
NONO=/tmp/claude/nono-bin/nono
W=/tmp/claude/exp/nono-wt
mkdir -p "$W"; cd "$W" || exit 2
export NONO_NO_MIGRATE=1
echo "== a. write inside the granted dir, write to \$HOME"
"$NONO" run --silent --allow "$W" -- /usr/bin/touch "$W/ok" 2>&1; echo "touch granted dir rc=$?"
"$NONO" run --silent --allow "$W" -- /usr/bin/touch "$HOME/.nono-probe-e4" 2>&1; echo "touch \$HOME rc=$?"
echo "== b. bwrap inside nono (what Claude Code's inner sandbox does)"
"$NONO" run --silent --allow "$W" -- bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/true 2>&1; echo "bwrap rc=$?"
echo "== c. egress: allowlisted host vs other host vs raw IP"
"$NONO" run --silent --allow "$W" --allow-domain api.github.com -- \
  /usr/bin/curl -sS -o /dev/null -w '%{http_code}\n' --max-time 10 https://api.github.com/zen 2>&1; echo "api.github.com rc=$?"
"$NONO" run --silent --allow "$W" --allow-domain api.github.com -- \
  /usr/bin/curl -sS -o /dev/null -w '%{http_code}\n' --max-time 10 https://example.com 2>&1; echo "example.com rc=$?"
"$NONO" run --silent --allow "$W" --allow-domain api.github.com -- \
  /usr/bin/curl -sS -o /dev/null -w '%{http_code}\n' --noproxy '*' --max-time 10 https://1.1.1.1 2>&1; echo "raw 1.1.1.1 bypassing proxy env rc=$?"
"$NONO" run --silent --allow "$W" --allow-domain api.github.com -- \
  /usr/bin/python3 -I -c 'import socket; s=socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.sendto(b"x", ("1.1.1.1", 53)); print("udp sent")' 2>&1; echo "udp to 1.1.1.1:53 rc=$?"
echo "== d. loopback listen inside nono (Claude Code's own egress proxy needs this)"
"$NONO" run --silent --allow "$W" --allow-domain api.github.com -- \
  /usr/bin/python3 -I -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); s.listen(); print("listen ok", s.getsockname()[1])' 2>&1; echo "listen rc=$?"
echo "== e. pathname unix socket bind in a granted dir"
"$NONO" run --silent --allow "$W" -- \
  /usr/bin/python3 -I -c "import socket; s=socket.socket(socket.AF_UNIX); s.bind('$W/srt-mux-test.sock'); s.listen(); print('unix bind ok')" 2>&1; echo "unix bind rc=$?"
```

```
== a. write inside the granted dir, write to $HOME
touch granted dir rc=0
/usr/bin/touch: cannot touch '$HOME/.nono-probe-e4': Permission denied
touch $HOME rc=1
== b. bwrap inside nono (what Claude Code's inner sandbox does)
bwrap: setting up uid map: Permission denied
bwrap rc=1
== c. egress: allowlisted host vs other host vs raw IP
200
api.github.com rc=0
curl: (56) CONNECT tunnel failed, response 403
000
example.com rc=56
curl: (7) Failed to connect to 1.1.1.1 port 443 after 0 ms: Couldn't connect to server
000
raw 1.1.1.1 bypassing proxy env rc=7
Traceback (most recent call last):
  …
PermissionError: [Errno 1] Operation not permitted
udp to 1.1.1.1:53 rc=1
== d. loopback listen inside nono (Claude Code's own egress proxy needs this)
Traceback (most recent call last):
  …
PermissionError: [Errno 13] Permission denied
listen rc=1
== e. pathname unix socket bind in a granted dir
unix bind ok
unix bind rc=0
```

(Two `apt_pkg` warnings that Ubuntu's Python prints when `/etc/apt` is
unreadable are cut from c and d.) The UDP socket is refused at creation, which
is nono's seccomp baseline closing Landlock's TCP-only gap. Claude Code's
sandbox would hit two walls under nono: its bwrap (b), and the loopback listen
of its egress proxy (d), the same listen PR #212 found Seatbelt refusing.

### E5. `claude -p` under nono, and Claude Code's sandbox on this host

```bash
# e5-nono-claude.sh
NONO=/tmp/claude/nono-bin/nono
W=/tmp/claude/exp/nono-wt
mkdir -p "$W"; cd "$W" || exit 2
export NONO_NO_MIGRATE=1
CLAUDE_BIN="$(readlink -f "$(command -v claude)")"
GRANTS=(
  --allow "$W"
  --allow "$HOME/.claude"
  --allow-file "$HOME/.claude.json"
  --read "$(dirname "$CLAUDE_BIN")"
  --allow /tmp/claude-<uid>
  --allow-domain api.anthropic.com
  --allow-domain statsig.anthropic.com
)
echo "== a. claude -p ok under nono"
"$NONO" run --silent "${GRANTS[@]}" -- claude -p 'Reply with exactly: ok' --max-turns 1 \
  --output-format json >"$W/a.out" 2>"$W/a.err"
echo "rc=$?"
python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print("result:", d.get("result"), "| is_error:", d.get("is_error"))' "$W/a.out"
echo "== b. same, Claude Code sandbox enabled + failIfUnavailable, one Bash call"
cat >"$W/settings.json" <<'JSON'
{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false}}
JSON
"$NONO" run --silent "${GRANTS[@]}" -- claude -p \
  'Use the Bash tool to run exactly: readlink /proc/self/ns/user . Then reply with its output, or with the exact error text if it failed.' \
  --settings "$W/settings.json" --allowedTools Bash --max-turns 3 \
  --output-format json >"$W/b.out" 2>"$W/b.err"
echo "rc=$?"
echo "-- stderr lines mentioning sandbox:"; rg -i sandbox "$W/b.err" | head -5
```

```
== a. claude -p ok under nono
rc=0
result: ok | is_error: False
== b. same, Claude Code sandbox enabled + failIfUnavailable, one Bash call
rc=1
-- stderr lines mentioning sandbox:
Error: sandbox required but unavailable: sandbox is enabled but dependencies are missing: socat not installed · install missing tools (e.g. apt install bubblewrap socat) or see https://code.claude.com/docs/en/sandboxing
  sandbox.failIfUnavailable is set — refusing to start without a working sandbox.
```

(Arm b's JSON parse of the empty `b.out` raised a traceback, which is cut.)
The same refusal happens with no jail at all:

```console
$ command -v socat || echo "socat: not installed"
socat: not installed
$ claude -p 'Reply with exactly: ok' --settings settings.json --max-turns 1 2>&1 | rg -i sandbox
Error: sandbox required but unavailable: sandbox is enabled but dependencies are missing: socat not installed · …
  sandbox.failIfUnavailable is set — refusing to start without a working sandbox.
```

So `claude -p` runs under nono with an explicit grant list and no registry
profile (verified). On this host, Claude Code's sandbox is unavailable with or
without any outer jail (verified). Because `render-settings` sets no
`failIfUnavailable`, the harness would run Bash unsandboxed (inferred from the
docs and #212's macOS measurement; not run here). Arm b never got as far as
nesting, so it does not test the Claude-under-nono case; E3 and E4b do.

Claude Code's docs add a scope limit that holds even where its sandbox runs:
"The sandbox covers shell commands only. Claude's file tools, MCP servers, and
hooks run outside it", and WebFetch and WebSearch "follow permission rules
instead"
([code.claude.com/docs/en/sandboxing](https://code.claude.com/docs/en/sandboxing)).
The harness allowlist was never going to filter the `claude` process's own
egress.

### E6. nono's command denies as an exec wall

```json
{
  "meta": { "name": "e6-exec-wall", "version": "0.0.1" },
  "filesystem": { "allow": ["/tmp/claude/exp/nono-wt"] },
  "command_policies": {
    "commands": {
      "systemctl": { "from": { "session": "deny" } },
      "systemd-run": { "from": { "session": "deny" } }
    }
  }
}
```

```
  [ok]  All 18 group references valid
  Result: valid
== via PATH
nono: command policy denied systemctl: Tool execution chain denied. 'systemctl' is blocked because direct session invocation is explicitly denied in profile 'e6-exec-wall'. Policy: command_policies.commands."systemctl".from.session is "deny".
== by absolute path
/usr/bin/bash: line 1: /usr/bin/systemctl: Permission denied
== a copy staged in a writable dir
systemd 255 (255.4-1ubuntu8.18)
== an allowed command still runs
warning: unable to access '$HOME/.gitconfig': Permission denied
git version 2.43.0
```

The three commands were `bash -c 'systemctl --user --version'`, the same with
`/usr/bin/systemctl`, and `cp /usr/bin/systemctl ./sc && ./sc --user --version`.
The deny catches PATH and canonical-path calls, and a copy in a writable
directory runs. The Seatbelt template closes that route by never granting exec
on RW scopes (`orchestrator.sb.tmpl:165-185`). Landlock cannot, because its read
right includes `Execute` and a writable grant includes read.

### E7. Can that staged copy reach the user service manager?

Profile e7 is e6 plus `"linux": { "af_unix_mediation": "pathname" }`. Each run
is `cp /usr/bin/systemctl ./sc && ./sc --user is-system-running`.

```
== profile e6-profile.json
degraded
sc rc=1
== profile e7-profile.json
Failed to connect to bus: Permission denied
sc rc=1
```

With nono's default Linux settings, the copy talks to systemd (it reports the
manager's `degraded` state), so it could `systemd-run --user` a unit outside the
jail. Pathname Unix-socket mediation, which nono leaves off by default
(`internals/landlock.mdx`), blocks it.

### E8. The same escape under a bwrap jail shaped like #1349

```bash
W=/tmp/claude/exp/nono-wt; RT="/run/user/$(id -u)"
bwrap --ro-bind / / --dev /dev --proc /proc --bind "$W" "$W" --chdir "$W" --die-with-parent --new-session \
  bash -c 'cp /usr/bin/systemctl ./sc && ./sc --user is-system-running; echo "sc rc=$?"'
bwrap --ro-bind / / --dev /dev --proc /proc --tmpfs "$RT" --bind "$W" "$W" --chdir "$W" --die-with-parent --new-session \
  bash -c 'cp /usr/bin/systemctl ./sc && ./sc --user is-system-running; echo "sc rc=$?"'
bwrap --ro-bind / / --dev /dev --proc /proc /usr/bin/touch "$HOME/.bwrap-probe-e8"; echo "rc=$?"
```

```
== ro-bind / only
degraded
sc rc=1
== ro-bind / plus a tmpfs over $XDG_RUNTIME_DIR
Failed to connect to bus: No such file or directory
sc rc=1
== write to $HOME under ro-bind /
/usr/bin/touch: cannot touch '$HOME/.bwrap-probe-e8': Read-only file system
rc=1
```

A read-only bind does not stop `connect()` on a socket. #1349's "nothing broader
than `/` read-only" leaks the user bus, and so the ability to start an unjailed
unit, unless the renderer also hides `$XDG_RUNTIME_DIR`.

### E9. One read-only file inside a writable directory

```bash
W=/tmp/claude/exp/rw-state; mkdir -p "$W"; echo token >"$W/cred"; cd "$W"
bwrap --ro-bind / / --dev /dev --proc /proc --bind "$W" "$W" --ro-bind "$W/cred" "$W/cred" \
  bash -c 'echo x >"$1/other" && echo "write other: ok"; echo y >"$1/cred" || echo "write cred: denied"; mv "$1/other" "$1/cred" 2>&1 || echo "rename over cred: denied"; cat "$1/cred"' _ "$W"
NONO_NO_MIGRATE=1 nono run --silent --allow "$W" --read-file "$W/cred" -- \
  bash -c 'echo y >"$1/cred" && echo "write cred: ALLOWED"; cat "$1/cred"' _ "$W"
```

```
== bwrap: --bind dir, then --ro-bind the cred file over itself
write other: ok
_: line 1: /tmp/claude/exp/rw-state/cred: Read-only file system
write cred: denied
mv: cannot move '/tmp/claude/exp/rw-state/other' to '/tmp/claude/exp/rw-state/cred': Device or resource busy
rename over cred: denied
token
== nono: --allow dir plus --read-file cred (Landlock grants are a union)
write cred: ALLOWED
y
```

### E10. nono `filesystem.deny` inside an allowed directory

```json
{
  "meta": { "name": "e10-deny-in-allow", "version": "0.0.1" },
  "filesystem": {
    "allow": ["/tmp/claude/exp/rw-state"],
    "deny": ["/tmp/claude/exp/rw-state/cred"]
  }
}
```

```
nono: Sandbox initialization failed: Landlock deny-overlap is not enforceable on Linux. Refusing to start with conflicting policy.
2 deny rule(s) cannot apply under an allowed parent directory.
Conflicts:
- deny '/tmp/claude/exp/rw-state/cred' overlaps allowed parent '/tmp' (source: group:system_write_linux)
- deny '/tmp/claude/exp/rw-state/cred' overlaps allowed parent '/tmp/claude/exp/rw-state' (source: profile)
Remove the broad allow path, remove the deny path, or restructure permissions.
rc=1
```

It fails closed, which is right, but the policy cannot be written. The second
conflict also shows that nono's built-in `system_write_linux` group grants all
of `/tmp` read-write. launch-runtime.md §1 grants only `$TMPDIR` and the
harness's `/tmp/claude-<N>` tree. Whether a profile can exclude that group was
not tested.

## macOS, from documents only

- nono applies its profile with `sandbox_init()` (`internals/seatbelt.mdx`).
  PR #212 measured that a second, different Seatbelt profile cannot be applied
  under a first one. So Claude Code's Seatbelt sandbox cannot start under nono
  on macOS, as it cannot under `sandbox-exec` today (inferred, same mechanism;
  nono's docs tell users to disable it).
- That costs nothing new. nono's egress proxy and the
  `(remote tcp "localhost:PORT")` pin do the filtering outside `claude`
  (`internals/security-model.mdx:240-251`). This is PR #212's option A, built and
  maintained upstream. The aiutopilot charter reports the proxy blocking
  non-allowlisted hosts for `git` and `gh` on macOS. Its evidence is not in that
  repo.
- On macOS, Seatbelt deny rules override allows, so `--cred-ro` and the
  supervisor-state deny stay expressible (`internals/capability-manifest.mdx:58`).
- nono's generated profile has no launchd or LaunchServices `mach-lookup` deny
  (`macos.rs:557-582`). The Seatbelt template's denies would have to be carried
  in `unsafe_macos_seatbelt_rules`. Whether that field is honoured at the top
  level of a `--profile <file>` profile was not tested.
- The TCC consent probe (`scripts/preflight.sh` §5) runs the entry path under
  launchd through `sandbox-exec`. With nono it would run through `nono run`;
  whether attribution changes is untested.

## What this record does not settle

- **macOS behaviour of nono 0.81.0.** Nothing was run on a Mac.
- **Claude Code's sandbox inside an outer bwrap.** Nested bwrap works (E0), but
  the harness refuses to start its sandbox without `socat`, which needs `sudo`
  to install. So whether the bwrap route's "harness allowlist" layer would run
  is unmeasured.
- **A full orchestrator run under nono.** E5 is one turn. `git`, `gh`, `uv`,
  `npm`, codex and MCP servers through the proxy, DNS with Unix-socket
  mediation on (glibc's resolver may use a Unix socket on this distro), and a
  run under `systemd-run --user` with no TTY are all untested.
- **SR-1 and SR-2.** They are cited in the aiutopilot charter but not present
  in that repo.
- **Anthropic's sandbox runtime.** Claude Code's docs point at it for "the
  Claude Code process itself". It may be a third candidate, and it was not
  evaluated.
