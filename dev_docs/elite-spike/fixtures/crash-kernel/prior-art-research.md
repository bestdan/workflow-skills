# Probe 5 — prior-art research: are we reinventing the wheel?

Fable web-research pass (2026-07-22), after four review rounds kept falsifying the
hand-rolled flat-file crash kernel on the same two things. **Answer: yes — two of
the pieces are solved wheels, and they are exactly the two that keep failing.**

## 1. Durable registry + exactly-once sequence → **SQLite (WAL), bundled**

The failing class (torn-interior line, duplicate seq after crashed head-publish,
gap-blessing adoption) is precisely what SQLite's commit machinery is built and
crash-tested for. One transaction gives append + monotone seq + owner-state update
atomically ("seq committed, head not published" becomes structurally impossible);
idempotency is a `UNIQUE(repo_key, generation, kind)` constraint, not a convention.

- SQLite [Atomic Commit](https://www.sqlite.org/atomiccommit.html) documents the
  exact torn-write/recovery protocol the drafts keep re-deriving wrong.
- [How SQLite Is Tested](https://www.sqlite.org/testing.html) — a crash-simulation
  VFS spawns children and kills them mid-write: a vastly more exhaustive version
  of our fault matrix, run for two decades.
- Serious tools persist to embedded transactional stores, not flat files (Nomad →
  BoltDB).

**macOS gotcha 1 (corrected by codex):** power-loss durability needs `PRAGMA
synchronous=FULL` + `PRAGMA fullfsync=ON` (fullfsync defaults OFF). There is
credible **reverse-engineered** evidence that a *particular* macOS 12 **system**
SQLite build mapped requested `F_FULLFSYNC` to `F_BARRIERFSYNC`
([bonsaidb ACID-on-Apple](https://bonsaidb.io/blog/acid-on-apple/),
[mjtsai/Johnson](https://mjtsai.com/blog/2025/09/05/sqlite-on-macos-not-acid/)) —
but that is not a stable public macOS contract, and Apple itself describes
`F_FULLFSYNC` as **best-effort** ([Apple disk-write guidance](https://developer.apple.com/documentation/xcode/reducing-disk-writes)).
Correct conclusion: **bundle and pin a known-good stock SQLite; avoid unverified
Apple-patched builds** — not "all Apple SQLite silently downgrades."

**macOS gotcha 2:** same epistemic ceiling — SIGKILL still can't prove power-loss
durability ([howtocorrupt](https://www.sqlite.org/howtocorrupt.html)); the win is
inheriting SQLite's crash-test corpus instead of a hand-built one.

The temp→fsync→rename→dir-fsync atomic-publish primitive is the accepted pattern
for anything *outside* the DB — but with SQLite holding owner+seq, almost nothing
is left that needs it.

## 2. Lock-as-liveness-beacon → **known-broken; use launchd KeepAlive + parenthood**

The pitfalls the reviews kept hitting are canonical:
- POSIX `fcntl` locks dropping on the first `close()` of *any* fd to the inode is
  so notorious that **SQLite's `os_unix.c` carries an ~86-line comment: "POSIX
  advisory locks are broken by design."** The 4th-pass close-trap CRIT *is* that
  bug. ([os_unix.c](https://github.com/sqlite/sqlite/blob/master/src/os_unix.c),
  [erouault](http://erouault.blogspot.com/2018/06/sqlite-and-posix-advisory-locks.html))
- OFD locks exist *because* of this ([LWN 586904](https://lwn.net/Articles/586904/))
  but are fork-shared. Trichotomy confirmed: flock fork-shared, POSIX close-trapped,
  OFD fork-shared ([gavv.net file-locks](https://gavv.net/articles/file-locks/)).

There is **no** established "perfect process-lifetime liveness lock" because the
field abandoned the design. Established patterns instead:
1. **Liveness by parenthood** — don't daemonize; supervisor is the direct parent,
   learns of death via `SIGCHLD`/`wait()` (s6, runit,
   [supervisord](https://supervisord.org/subprocess.html)). systemd
   [daemon(7)](https://man7.org/linux/man-pages/man7/daemon.7.html) calls pidfiles
   race-prone and tracks by containment domain instead.
2. **Supervise the supervisor** — init/launchd `KeepAlive` restarts it, and it
   re-reaps from durable state ([launchd.plist](https://keith.github.io/xcode-man-pages/launchd.plist.5.html)).

If a run-lifetime lock is kept at all, the one macOS-viable topology is **`flock` +
`FD_CLOEXEC` + `posix_spawn`-only children** (no un-exec'd child ever exists, so
fork-sharing can't bite; unrelated opens/closes don't drop an OFD lock) — but only
as a **mutex**, never the source of truth (the lease record lives in SQLite).

## 3. Tree-kill without cgroups on macOS → **the dedicated uid IS the primitive**

No Unix without cgroups can reliably kill a process tree — a double-fork escapes
any pgroup/session scheme
([OSnews](https://www.osnews.com/story/130444/killing-a-process-and-all-of-its-descendants/)).
macOS has no subreaper / `PR_SET_PDEATHSIG` / cgroup-kill. What it has:
- launchd SIGTERMs the job's process **group** on stop (SIGKILL after `ExitTimeOut`)
  unless `AbandonProcessGroup` — pgroup-level; a `setsid` child escapes (= R4b).
- kqueue `EVFILT_PROC`/`NOTE_EXIT` gives race-free exit notice for a *watched pid*
  (good for the watcher; tracks nothing transitively).
- **`kill(-1, sig)` sent as the throwaway uid signals *every* process of that uid**
  — no setsid/double-fork/pgid escape. This is the historical batch-system answer
  (HTCondor dedicated execution accounts) and Android's app-sandbox foundation.

So the elaborate pgid/sid revalidation machinery (R4a, Tkr, PGID-reuse, fork-churn
convergence) is a best-effort *narrowing* no shipping system made airtight — and
**moot** in the uid design, where "kill everything of uid X, verify zero remain"
converges trivially. The draft already conceded the uid is the fence; research
says it is the load-bearing mechanism, not a footnote.

## 4. Off-the-shelf job managers

None does the whole job, but all serious ones validate the component split:
durable state in an embedded transactional store (or accepted lossy); liveness by
parenthood + an init restarter; tree-kill via cgroups or admitted best-effort.
launchd (`KeepAlive`, no registry), pueue (JSON snapshot, no lease/generation),
supervisord (its docs concede it *cannot* kill daemonized grandchildren), Nomad
single-node (BoltDB). **No tool ships a lock-liveness-beacon.**

## Bottom line — the partition

**Adopt off-the-shelf (the two failing wheels):**
1. **SQLite** (bundled stock build; WAL; `synchronous=FULL`; `fullfsync=ON`) for
   registry, seq, owner records, generation HWM, and lease *records* → deletes
   CRIT-1/2, the 4th-pass registry CRIT, tail-reconciliation, `seq.head`,
   month-rollover, and ~a third of the fault matrix.
2. **launchd `KeepAlive`** to supervise the supervisor → converts "supervisor died,
   prove-it-with-a-lock" (the liveness CRITs) into "supervisor restarts in seconds
   and runs the recovery table from SQLite state." If a lock is kept, `flock` +
   CLOEXEC + `posix_spawn`-only, as a mutex only.
3. **Dedicated uid + `kill(-1)`** as the termination primitive → retires
   PGID-reuse, fork-churn, and kill-TOCTOU findings wholesale.

**Genuinely bespoke (~40%, unavoidable):** the lease/generation/takeover
*semantics* (state machine, recovery table, fencing), incarnation identity
(`p_uniqueid` + start-time + boot-session), `gen_token` discovery of
pre-registration crashes, and escape detection. No tool provides these.

**The smaller thing to build:** a state machine over SQLite, launched by launchd,
killing by uid — not a filesystem + lock manager + process-containment kernel
hand-rolled at once. The four review rounds were the market signaling this
partition; this is a legitimate Probe 5 result (the falsification-redirect the
kill sheet named, now evidenced).

## codex review (2026-07-23): corrections + off-the-shelf verdict + containment fork

An independent codex pass (web search) confirmed the direction but corrected
three overclaims and answered "is there really nothing off-the-shelf."

**Corrections to the partition:**
- **SQLite — right, with caveats.** Do **not** equate `AUTOINCREMENT` with a
  gapless sequence (failed inserts leave gaps) — use an explicit singleton counter
  in the same transaction. SQLite gives exactly-once *durable state recording*,
  **not** exactly-once *worker execution* (idempotent side-effects stay the app's
  job). `BEGIN IMMEDIATE` takes the write position up front.
- **launchd restarts the supervisor; it does NOT re-adopt workers.** The restarted
  supervisor must reconcile its durable roster against live incarnations itself.
  launchd removes "who restarts the supervisor," nothing more.
- **Dedicated uid is strong but NOT escape-proof and NOT no-root.** `kill(-1)` as
  the uid is **uid-wide** (kills every worker of that uid across every repo — not
  per-repo/per-generation), catastrophic under the ordinary desktop uid; a
  per-user LaunchAgent **cannot** switch uid (`UserName` only applies in the
  privileged system domain), so it needs **one-time admin provisioning**; and it
  doesn't contain credential-changing helpers/XPC/remote subprocesses. So "no
  root" must mean "no root in normal operation, after one-time privileged setup."
- **"~40% bespoke" understates it.** SQLite deletes much *code*, but most of the
  *safety argument* — generation fencing, takeover, identity, reconciliation,
  stopping convergence, containment — stays bespoke.

**Off-the-shelf verdict (answers the "surely something exists" question):** no
product satisfies the **full conjunction** — {durable fenced ownership + arbitrary
native macOS subprocess supervision + automatic orphan safe-stop + escape-resistant
tree-kill + no root + no external server}. Every category fails on one axis:
Temporal/Cadence/Prefect/Dagster/Airflow/Windmill (cooperative cancellation, don't
prove a subprocess tree is dead; add a server); River/Faktory (in-language, need
Postgres/server); OTP/Akka/Ray (must move execution into their runtime; an OS
`Port` process survives a VM crash); Nomad (`exec` isolation is Linux-only,
`raw_exec` unisolated); k3s/k0s/Quadlet/Compose (Linux/VM/systemd/cgroups or paid);
runit/s6/daemontools/immortal (no transactional registry/lease/incarnation;
`setsid` escapes); PM2/circus/overmind/hivemind (operational managers, not
crash-atomic ownership). **Research misses codex flagged:** **Pueue** (closest
generic local-job daemon — but no fencing/incarnation/escape-safe stop) and, most
notably, **Claude Code Agent View** (purpose-built per-user supervisor with
persistent sessions + respawn — but a research preview, Claude-specific, manual
stall recovery, resume-favoring, no lease/generation/incarnation/reaping contract).
Relaxing **any** constraint changes the answer (Prefect for cooperative jobs, Pueue
for human queues, Agent View for Claude-only, containers for strong containment) —
which is exactly *why* nothing packages it: the **conjunction**, not the pieces.

**Verdict: (c) a corrected Fable assembly** — bundled/pinned SQLite for all
authoritative state + one launchd-managed global supervisor + the bespoke
generation/takeover/recovery state machine. **Containment is now an explicit fork**
(pick one, don't assume uid):
1. one-time privileged provisioning of an **exclusive service uid**, then uid-wide
   `kill(-1)` — *aligns with the already-provisioned `agent` uid (Probe 1)*;
2. workers inside a **Linux VM / container** boundary;
3. native no-root host processes with the guarantee **weakened** to "cooperative
   process-group containment + escape detection" (arbitrary hostile descendants +
   absolute escape-proof reaping are **not** simultaneously achievable on Darwin
   no-root).

**Probe 5 reclassification: "flat-file crash kernel falsified → storage redirect
taken"** — but *redirect taken ≠ replacement validated*. The SQLite/launchd
assembly still needs the fixture. **Smallest build+test target:** one SQLite DB
(lease/current-generation, append-only events, worker incarnations, unique
idempotency keys); one transaction per transition (seq allocation + lease/gen
movement); one launchd-owned supervisor + one IPC mutation path; startup
reconciliation that fences stale generations, records stop-intent, drives
TERM→bounded-wait→KILL, verifies absence, then terminalizes+releases; a containment
decision from the three above; fault injection at every transaction boundary +
supervisor SIGKILL with live workers + concurrent launch/takeover + PID reuse +
fork/setsid escape + fork churn + ENOSPC/IO + a real VM power-loss test.
