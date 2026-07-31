#!/usr/bin/env python3
"""Probe 5 — the v5.1 crash-transaction kernel (disposable spike; §0a rule 4).

Authoritative state is ONE SQLite `state.db`. Every state-machine transition is
EXACTLY ONE `BEGIN IMMEDIATE` transaction — never split across two — so a crash
is a clean rollback or a clean commit and never a half-applied transition
(invariant 1).

Crash injection: `PROBE5_CRASH_AT=<point>` makes `crash()` call `os._exit(1)` at
the named boundary. `os._exit` skips atexit/finally, so a crash inside a `with
txn(...)` body deliberately leaves the transaction UNCOMMITTED and un-rolled-back
in this process — recovery must be SQLite's, not ours. That is the point.

Requires a BUNDLED STOCK SQLite (not Apple's system build) — see `assert_stock_sqlite`.
"""
import contextlib
import json
import os
import sqlite3
import subprocess
import sys
import time

CRASH_AT = os.environ.get("PROBE5_CRASH_AT", "")
CRASH_LOG = os.environ.get("PROBE5_CRASH_LOG", "")

# Apple ships SQLite as the system library; the draft requires a stock build
# because the Apple-patched builds have credible reverse-engineered evidence of
# downgrading a requested F_FULLFSYNC (prior-art-research.md §1).
APPLE_SQLITE_HINT = "/Library/Developer/CommandLineTools"


class KernelError(Exception):
    pass


def crash(point):
    """Die hard at a named boundary if it is the armed point."""
    if CRASH_AT and CRASH_AT == point:
        if CRASH_LOG:
            with open(CRASH_LOG, "a") as f:
                f.write(json.dumps({"crashed_at": point, "pid": os.getpid(),
                                    "wall_ts": time.time()}) + "\n")
        os._exit(1)


_BOOT_SESSION = None


def boot_session():
    """kern.bootsessionuuid — macOS's stand-in for Linux's /proc boot_id.

    Any durable liveness claim is only meaningful within one boot session.
    """
    global _BOOT_SESSION
    if _BOOT_SESSION is None:
        _BOOT_SESSION = subprocess.run(
            ["sysctl", "-n", "kern.bootsessionuuid"],
            capture_output=True, text=True).stdout.strip()
    return _BOOT_SESSION


def assert_stock_sqlite():
    """Fail closed rather than silently testing a build whose fullfsync we distrust."""
    if APPLE_SQLITE_HINT in sys.executable:
        raise KernelError(
            f"refusing to run on Apple's system SQLite via {sys.executable}; "
            f"use a bundled stock build (e.g. Homebrew python3.12)")
    return {"python": sys.executable, "sqlite_version": sqlite3.sqlite_version}


SCHEMA = """
CREATE TABLE IF NOT EXISTS lease (
  repo_key        TEXT PRIMARY KEY,
  generation      INTEGER NOT NULL,
  run_id          TEXT,
  manifest_digest TEXT,
  gen_token       TEXT,
  state           TEXT NOT NULL CHECK(state IN ('prepared','active','terminal')),
  stop_intent     INTEGER NOT NULL DEFAULT 0,
  updated_seq     INTEGER
);

CREATE TABLE IF NOT EXISTS meta (
  k TEXT PRIMARY KEY,
  v INTEGER NOT NULL
);

-- `seq` is explicit and gapless. NOT AUTOINCREMENT: a failed insert would leave
-- a gap in a rowid sequence, and a gap falsifies invariant 2.
CREATE TABLE IF NOT EXISTS events (
  seq          INTEGER PRIMARY KEY,
  idem_key     TEXT NOT NULL,
  kind         TEXT NOT NULL,
  repo_key     TEXT,
  generation   INTEGER,
  run_id       TEXT,
  payload      TEXT NOT NULL,
  wall_ts      REAL,
  mono_ts      REAL,
  boot_session TEXT,
  writer       TEXT,
  UNIQUE(idem_key)
);

CREATE TABLE IF NOT EXISTS incarnations (
  repo_key     TEXT NOT NULL,
  generation   INTEGER NOT NULL,
  role         TEXT NOT NULL,
  pid          INTEGER NOT NULL,
  p_uniqueid   INTEGER NOT NULL,
  start_tvsec  INTEGER,
  start_tvusec INTEGER,
  exe          TEXT,
  boot_session TEXT,
  PRIMARY KEY (repo_key, generation, role, p_uniqueid)
);
"""


def connect(path):
    """Open `state.db` with the durability pragmas the draft specifies.

    isolation_level=None puts the driver in autocommit so that `txn()` — not
    sqlite3's implicit transaction management — decides every BEGIN/COMMIT.
    Without this the driver would open its own transactions and the
    one-transaction-per-transition invariant would not be ours to enforce.
    """
    c = sqlite3.connect(path, isolation_level=None, timeout=15.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=FULL")
    c.execute("PRAGMA fullfsync=ON")
    c.execute("PRAGMA busy_timeout=15000")
    return c


def pragma_report(c):
    """What the DB actually honoured — recorded as evidence, not assumed."""
    get = lambda p: c.execute(f"PRAGMA {p}").fetchone()[0]
    return {"journal_mode": get("journal_mode"), "synchronous": get("synchronous"),
            "fullfsync": get("fullfsync"), "sqlite_version": sqlite3.sqlite_version}


def init_db(path):
    c = connect(path)
    c.executescript(SCHEMA)
    c.execute("INSERT OR IGNORE INTO meta(k, v) VALUES('seq', 0)")
    return c


@contextlib.contextmanager
def txn(c):
    """One transition = one BEGIN IMMEDIATE. Takes the write lock up front, so
    concurrent writers serialize here rather than failing late at COMMIT."""
    c.execute("BEGIN IMMEDIATE")
    try:
        yield c
    except BaseException:
        c.execute("ROLLBACK")
        raise
    c.execute("COMMIT")


def append_event(c, *, idem_key, kind, repo_key=None, generation=None,
                 run_id=None, payload=None, writer="supervisor"):
    """Append one event inside the caller's transaction. Returns (seq, replayed).

    Idempotency is checked BEFORE allocation: a duplicate `idem_key` returns the
    existing seq and consumes no sequence number. Allocating first and then
    swallowing the insert (`UPDATE counter; INSERT OR IGNORE`) would burn a seq
    on the duplicate and gap the sequence — the fifth-pass spec bug.
    """
    payload_s = json.dumps(payload or {}, sort_keys=True)
    row = c.execute("SELECT seq, payload FROM events WHERE idem_key=?",
                    (idem_key,)).fetchone()
    if row is not None:
        if row[1] != payload_s:
            raise KernelError(f"idem_key {idem_key!r} replayed with a different payload")
        return row[0], True

    c.execute("UPDATE meta SET v = v + 1 WHERE k = 'seq'")
    seq = c.execute("SELECT v FROM meta WHERE k = 'seq'").fetchone()[0]
    # A UNIQUE(idem_key) violation raises here and txn() rolls the WHOLE
    # transaction back, taking the counter bump with it. No gap either way.
    c.execute(
        "INSERT INTO events(seq, idem_key, kind, repo_key, generation, run_id,"
        " payload, wall_ts, mono_ts, boot_session, writer)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (seq, idem_key, kind, repo_key, generation, run_id, payload_s,
         time.time(), time.monotonic(), boot_session(), writer))
    return seq, False


# --- reads --------------------------------------------------------------------

def get_lease(c, repo_key):
    r = c.execute(
        "SELECT repo_key, generation, run_id, manifest_digest, gen_token, state,"
        " stop_intent, updated_seq FROM lease WHERE repo_key=?", (repo_key,)).fetchone()
    if r is None:
        return None
    return dict(zip(("repo_key", "generation", "run_id", "manifest_digest",
                     "gen_token", "state", "stop_intent", "updated_seq"), r))


def non_terminal_leases(c):
    rows = c.execute("SELECT repo_key FROM lease WHERE state != 'terminal'").fetchall()
    return [get_lease(c, r[0]) for r in rows]


def get_incarnation(c, repo_key, generation, role="run"):
    r = c.execute(
        "SELECT pid, p_uniqueid, start_tvsec, start_tvusec, exe, boot_session"
        " FROM incarnations WHERE repo_key=? AND generation=? AND role=?",
        (repo_key, generation, role)).fetchone()
    if r is None:
        return None
    d = dict(zip(("pid", "p_uniqueid", "start_tvsec", "start_tvusec", "exe",
                  "boot_session"), r))
    d["alive"] = True  # shape-compatible with incarnation.measure() for comparison
    return d


def events(c):
    return [dict(zip(("seq", "idem_key", "kind", "repo_key", "generation", "run_id"), r))
            for r in c.execute(
                "SELECT seq, idem_key, kind, repo_key, generation, run_id"
                " FROM events ORDER BY seq").fetchall()]


# --- transitions (one txn each) ----------------------------------------------

def launch_prepare(c, *, repo_key, run_id, gen_token, manifest_digest=""):
    """LAUNCH step 1. Admission gate + generation reservation + `prepared`.

    ONE transaction. The gate is "zero non-terminal leases ANYWHERE", not just
    for this repo: because termination is uid-wide `kill(-1)`, a second live
    lease elsewhere would be collateral. One globally-active lease is what makes
    the uid-wide kill correct (invariant 3).
    """
    with txn(c):
        live = c.execute("SELECT COUNT(*) FROM lease WHERE state != 'terminal'").fetchone()[0]
        if live:
            raise KernelError(f"admission gate: {live} non-terminal lease(s) exist")
        hwm = c.execute("SELECT COALESCE(MAX(generation), 0) FROM lease").fetchone()[0]
        g = hwm + 1
        seq, _ = append_event(c, idem_key=f"generation_reserved:{repo_key}:{g}",
                              kind="generation_reserved", repo_key=repo_key,
                              generation=g, run_id=run_id)
        c.execute(
            "INSERT INTO lease(repo_key, generation, run_id, manifest_digest,"
            " gen_token, state, stop_intent, updated_seq)"
            " VALUES(?,?,?,?,?,'prepared',0,?)"
            " ON CONFLICT(repo_key) DO UPDATE SET generation=excluded.generation,"
            " run_id=excluded.run_id, manifest_digest=excluded.manifest_digest,"
            " gen_token=excluded.gen_token, state='prepared', stop_intent=0,"
            " updated_seq=excluded.updated_seq",
            (repo_key, g, run_id, manifest_digest, gen_token, seq))
        crash("prepare.pre_commit")
    crash("prepare.post_commit")  # G1: prepared is durable, nothing spawned yet
    return g


def launch_activate(c, *, repo_key, generation, run_id, incarnation):
    """LAUNCH step 2. Record the run's incarnation and move `prepared` → `active`.

    ONE transaction. The incarnation was measured by the SUPERVISOR from the
    kernel (libproc), not taken on the child's word — the child only tells us
    which pid to measure (invariant 8: agent never writes state.db).
    """
    with txn(c):
        lease = get_lease(c, repo_key)
        if lease is None or lease["state"] != "prepared" or lease["generation"] != generation:
            raise KernelError(f"activate: lease not in prepared/{generation}: {lease}")
        seq, _ = append_event(c, idem_key=f"run_registered:{repo_key}:{generation}",
                              kind="run_registered", repo_key=repo_key,
                              generation=generation, run_id=run_id,
                              payload={"p_uniqueid": incarnation["p_uniqueid"]})
        c.execute(
            "INSERT OR REPLACE INTO incarnations(repo_key, generation, role, pid,"
            " p_uniqueid, start_tvsec, start_tvusec, exe, boot_session)"
            " VALUES(?,?,'run',?,?,?,?,?,?)",
            (repo_key, generation, incarnation["pid"], incarnation["p_uniqueid"],
             incarnation["start_tvsec"], incarnation["start_tvusec"],
             incarnation["exe"], boot_session()))
        c.execute("UPDATE lease SET state='active', updated_seq=? WHERE repo_key=?",
                  (seq, repo_key))
        crash("activate.pre_commit")
    crash("activate.post_commit")  # G3: active is durable, the gate is still shut


def commit_stop_intent(c, *, repo_key, reason):
    """Saga step 1. Durably record the intent to stop BEFORE any signal.

    ONE transaction, and it does NOT kill anything: the kill runs outside every
    transaction (saga step 2). Holding BEGIN IMMEDIATE across a bounded-wait
    reap would block every other writer for the length of the reap.
    """
    with txn(c):
        lease = get_lease(c, repo_key)
        if lease is None or lease["state"] == "terminal":
            raise KernelError(f"stop_intent: lease not stoppable: {lease}")
        seq, _ = append_event(
            c, idem_key=f"stop_requested:{repo_key}:{lease['generation']}:{reason}",
            kind="stop_requested", repo_key=repo_key,
            generation=lease["generation"], payload={"reason": reason})
        c.execute("UPDATE lease SET stop_intent=1, updated_seq=? WHERE repo_key=?",
                  (seq, repo_key))
        crash("stop_intent.pre_commit")
    crash("stop_intent.post_commit")  # Saga1: intent durable, nothing killed yet


def terminalize(c, *, repo_key, kind="observed_terminal", reap_evidence=None):
    """Saga step 3. `terminal` + release — ONLY legal after a verified-zero reap.

    ONE transaction. The caller must have proven the containment domain is empty;
    `reap_evidence["converged"]` is re-checked here so a non-converging reap can
    never terminalize by accident (invariants 4 and 6).
    """
    if not (reap_evidence or {}).get("converged"):
        raise KernelError("terminalize refused: reap did not verify zero survivors")
    with txn(c):
        lease = get_lease(c, repo_key)
        if lease is None:
            raise KernelError("terminalize: no lease")
        if lease["state"] == "terminal":
            return  # idempotent: a re-run of the saga's tail is a no-op
        g = lease["generation"]
        append_event(c, idem_key=f"{kind}:{repo_key}:{g}", kind=kind,
                     repo_key=repo_key, generation=g,
                     payload={"survivors": 0, "rounds": reap_evidence.get("rounds")})
        seq, _ = append_event(c, idem_key=f"lease_release:{repo_key}:{g}",
                              kind="lease_release", repo_key=repo_key, generation=g)
        c.execute("UPDATE lease SET state='terminal', stop_intent=0, updated_seq=?"
                  " WHERE repo_key=?", (seq, repo_key))
        crash("terminalize.pre_commit")
    crash("terminalize.post_commit")


def takeover_publish(c, *, repo_key, run_id, gen_token, reap_evidence=None):
    """TAKEOVER step 2 — publish `g+1/prepared` AFTER generation g is verified dead.

    ONE transaction, and it is deliberately NOT the whole takeover: the caller
    must have already run the reap saga against generation g and proven the
    containment domain empty. Publishing first and reaping after is the v1
    live-orphan hazard arriving through the side door (third pass, HIGH-6) —
    g+1 would be admitted while g's workers were still mutating the repo.

    Unlike LAUNCH there is no zero-non-terminal-lease gate: a takeover exists
    precisely to replace the one live lease. The verified-zero reap is what
    stands in for the admission gate here.
    """
    if not (reap_evidence or {}).get("converged"):
        raise KernelError("takeover refused: generation g was not reaped to zero")
    with txn(c):
        lease = get_lease(c, repo_key)
        if lease is None:
            raise KernelError("takeover: no lease to take over")
        g = lease["generation"] + 1
        seq, _ = append_event(c, idem_key=f"generation_reserved:{repo_key}:{g}",
                              kind="generation_reserved", repo_key=repo_key,
                              generation=g, run_id=run_id,
                              payload={"takeover_from": lease["generation"]})
        c.execute(
            "UPDATE lease SET generation=?, run_id=?, gen_token=?, state='prepared',"
            " stop_intent=0, updated_seq=? WHERE repo_key=?",
            (g, run_id, gen_token, seq, repo_key))
        crash("takeover.pre_commit")
    crash("takeover.post_commit")
    return g


def claimed_exit(c, *, repo_key, generation, run_id):
    """An agent-authored 'I finished' record. Appended, INERT: it never releases
    the lease. Only a verified-zero reap does (invariant 4)."""
    with txn(c):
        append_event(c, idem_key=f"claimed_exit:{repo_key}:{generation}",
                     kind="claimed_exit", repo_key=repo_key, generation=generation,
                     run_id=run_id, writer="agent-claim")
