# Probe 5 — v5 fixture execution brief (for a fresh session)

You are executing **Probe 5** of the auto-pilot E-lite spike. The flat-file crash
kernel was falsified across four review rounds; **v5 takes the redirect** — a
SQLite state machine + a launchd supervisor + dedicated-uid containment. Your job:
**build the disposable v5 fixture, run the fault matrix, classify the result, and
record evidence**, following the §7a spike rules. *Redirect taken ≠ replacement
validated* — this fixture is the validation.

Working branch: `bestdan/elite-probe5-crash-kernel` (a worktree under
`~/src/worktrees/workflow-skills/probe5-crash-kernel`). Do the write-work there.

## 0. Read first (required, in order)

All paths relative to `dev_docs/elite-spike/fixtures/crash-kernel/`:
1. **`draft-state-machine.md` (v5.1)** — the design you are falsifying (the spec:
   SQLite schema, state machine, startup reconciliation, invariants 1–8, matrix).
2. **`probe5-crash-kernel.md`** — the kill sheet: falsifier, method, **pass /
   inconclusive / falsify bar**, the matrix, the two-working-day cap, and the
   design-doc deltas.
3. **`coreview-2026-07-22.md`** — the full 4-round review history **plus the
   latest codex v5 pass**. **Before building, apply any still-open v5 fixes it
   names** (in particular, resolve *re-adopt vs reap on supervisor restart*, the
   *DB-commit→side-effect* crash windows, and the *privilege path* for uid-kill —
   see §3 below). Do not build against a spec the last review already faulted.
4. **`prior-art-research.md`** — why this shape (SQLite/launchd/uid), the macOS
   gotchas (Apple system-SQLite `F_FULLFSYNC` downgrade; no cgroups; `kill(-1)`),
   and the containment fork.
5. **`../process-binding/incarnation.py`** — reuse Probe 2's libproc `p_uniqueid`
   incarnation reader (copy it in; it is the identity primitive).
6. **`../../provisioning.md`** — the `agent` uid (502) facts.

Look at the **Probe 3 fixture** (`../async-skeleton/`) for the launchd + armed-
crash-point + orchestrator patterns, and the **Probe 4 fixture**
(`../github-authority/`) for the attended-`sudo -u agent` execution pattern.

## 1. Environment & prerequisites (this is the mini)

- **agent uid 502** exists (non-admin, zero sudo); maintainer = `danielegan`.
  `/Users/danielegan` is `0700` — the agent **cannot** read it, so stage any
  agent-side scripts somewhere agent-readable (`/Users/Shared/p5/…`, `1777`).
- **launchd** per-user jobs must run **unsandboxed** (`launchctl bootstrap
  gui/$(id -u) …` fails `EIO` under the command sandbox; `rc=0` unsandboxed).
- **Running as the agent uid needs attended `sudo -u agent …`** entered in a
  **real terminal** (the `!`-prefix path has no tty for the password). Prepare the
  exact commands and have the human run them; read back the output.
- **Privilege for reaping:** the supervisor runs as maintainer, but `kill(-1)` of
  the **agent** uid's processes requires root/sudo (a non-root process can't
  signal another uid). Use `sudo -u agent kill …` or a privileged kill; this is
  consistent with the one-time-admin-provisioned agent uid + sudoers. Confirm the
  exact privilege path against the latest coreview before relying on it.
- **SQLite:** Python's stdlib `sqlite3` is acceptable **for process-crash
  testing** (which is all SIGKILL can prove). Real power-loss durability needs a
  bundled build with `fullfsync=ON` **and** a VM/loopback power-fail harness —
  out of scope here → classify power-loss **inconclusive**. **Document this
  explicitly**; do not claim durability you didn't test.
- Everything is **disposable**: a temp dir + a disposable `state.db`. Nothing under
  `/usr/local/autopilot`. Rule 4: spike code is **never promoted by renaming**.

## 2. Build the fixture (only the v5.1 assembly)

- **`state.db`** — schema per the draft: `lease`, `meta` (the explicit gapless
  `seq` counter), `events` (append-only, `UNIQUE(repo_key,generation,kind,idem_key)`),
  `incarnations`. Open WAL; set `synchronous=FULL`, `fullfsync=ON` (best-effort on
  stdlib — note it).
- **`kernel.py`** — the transitions, **each exactly one `BEGIN IMMEDIATE`
  transaction** (never split a transition across two transactions). `append_event()`
  does `UPDATE meta SET v=v+1 WHERE k='seq'`, reads it back, inserts the event.
  **Armed crash-points**: `PROBE5_CRASH_AT=<point>` → `os._exit(1)` at every
  transition boundary **and** at the DB→side-effect windows: after commit-prepared
  / before spawn; after spawn / before commit-active; after commit-terminal /
  before the process actually dies.
- **`supervisor.py`** — launchd-hosted; **sole writer** of `state.db` (maintainer
  uid). On (re)start runs **startup reconciliation** over every non-terminal lease:
  check the run incarnation's liveness by `p_uniqueid` (not bare pid) and take the
  action the draft's reconciliation table + the latest coreview dictate
  (re-adopt a live, identity-verified run via kqueue `NOTE_EXIT`; reap a dead or
  wedged one; terminalize/`launch_aborted` a stale `prepared`). The agent-uid run
  **reports its incarnation to the supervisor** (pipe/socket) — it does **not**
  write `state.db` (avoids cross-uid DB writes; matches "agent never writes the
  registry").
- **`reaper`** — as the agent uid: `kill(-1, SIGTERM)` → bounded wait →
  `kill(-1, SIGKILL)` → **re-scan the uid's process list until zero remain**;
  verify absence before terminalizing.
- **launchd plist template** for the supervisor (StartInterval or KeepAlive as the
  scenario needs); **run surrogate** = a trivial process spawned under the agent
  uid carrying `gen_token`, registering its incarnation, then blocking on a
  start-gate.
- **`scenarios.py`** — orchestrator over the matrix → sanitized `results.json`.

## 3. Settled decisions (v5.1 — codex's fifth pass, already in the draft)

These were open in v5 and are now **decided** in `draft-state-machine.md` v5.1 —
implement them as written (don't re-litigate):
1. **Monitored re-adoption on supervisor restart (ratified).** Reconciliation
   **re-adopts** a live, `p_uniqueid`-verified run (register `EVFILT_PROC/NOTE_EXIT`
   by pid, then **re-read `p_uniqueid`** to close the attach/PID-reuse race —
   observation, never `wait()` a nonchild) and **reaps** only the dead / wedged /
   `stop_intent` case. A benign restart must **not** kill healthy work — test both
   Sup-readopt (healthy) and Sup-orphan (dead run, live descendants).
2. **DB↔side-effect atomicity** is closed by (a) the **start-gate EOF = "exit,
   don't go"** rule, and (b) **every recovery uid-scans + reaps + verifies zero
   before terminalizing** — terminalization is gated on `uid==zero`, never on the
   recorded run's liveness. Inject G1–G3 + Sup-orphan and prove each.
3. **Privilege:** spawn and reap run **as the agent uid** via a scoped
   `sudo -u agent` helper (Stage-2 sudoers); **never root `kill(-1)`**. Pin the
   exact helper invocation; it's a runtime privileged-mediation dependency.
4. **Termination is a saga** (`stop_intent` commit → kill/rescan **outside** any
   txn → `terminal` after uid==zero); non-convergence stays fenced in `stop_intent`
   (retry+alert), never terminalizes.
5. **Sole writer:** the agent run reports its incarnation over an inherited pipe;
   the maintainer supervisor is the only `state.db` writer (never inherit SQLite
   fds). **One globally-active lease** (admission gate) makes uid-wide kill correct.

## 4. Run the matrix (kill sheet §Fault-injection matrix)

Deterministically (armed crash-points); if a point can't be injected
deterministically, classify that row **inconclusive**, not pass. Rows: **T1–Tn**
(SIGKILL at every transition boundary → clean rollback/commit), **Idem** (replay →
inert), **Sup** (supervisor SIGKILL with live workers → restart → reconcile →
reap-to-zero → terminal, no human), **Rec** (crash during reconciliation, repeated
→ idempotent convergence), **Race** (concurrent launch/launch, launch/takeover →
`BEGIN IMMEDIATE` serializes), **Tw** (takeover with live gen-g workers → reap
first), **Pid** (PID reuse → `p_uniqueid` rejects), **Esc** (`fork`/`setsid`
escape → `kill(-1)` still reaps), **Churn** (fork churn → TERM/KILL/re-scan
converges), **Io** (ENOSPC/EIO → atomic failure, no partial state), **Uid**
(uid-changing helper → documented **limitation**, not a falsifier), **PL**
(power-loss via VM harness, else **inconclusive**). Verify **invariants 1–7** after
each row.

## 5. Classify & record (rules 3–4)

- Classify **`confirmed` / `falsified` / `inconclusive`** at the two-day cap.
  For this probe **`confirmed` = "not falsified in the tested process-crash
  environment"** (reboot/power-loss stays inconclusive). "Nearly done" is not a
  state.
- Write **`results.json`** (sanitized — no secrets, no absolute rundir noise), fill
  **Environment** + **Results** in `probe5-crash-kernel.md`, and update **§7a row
  5** in `dev_docs/auto-pilot-e-lite-design-2026-07-21.md` with the classification
  (mirror the Probe 3/4 row style).
- **If falsified:** stop dependent work, record the invalidated assumption; the
  named redirect is **containment option 2** (Linux VM / container). **Do not
  return to the flat-file kernel.**

## 6. Guardrails

- Attended: any agent-uid / `launchctl` / privileged-kill step is a command you
  prepare and the human runs in a real terminal; read back and interpret the output.
- Commit incrementally on the working branch (Conventional Commits, `Claude-Session`
  trailer). Do not commit to `main`.
- **Cleanup at the end:** `bootout` every launchd job, `pkill` stray surrogates +
  agent-uid processes, remove `/Users/Shared/p5` staging and the disposable
  `state.db`. Leave no launchd job or agent process behind.

## Deliverable

A committed disposable fixture (`state.db` schema + `kernel.py` + `supervisor.py`
+ reaper + plist + `scenarios.py` + `incarnation.py`), a sanitized `results.json`,
the filled-in `probe5-crash-kernel.md` Environment/Results, an updated §7a row 5,
and a one-line classification: **confirmed / falsified / inconclusive**, with the
process-crash-ceiling and power-loss-inconclusive caveats stated.
