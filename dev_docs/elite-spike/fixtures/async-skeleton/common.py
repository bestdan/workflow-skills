"""Shared state/paths/records for the Probe 3 async-skeleton fixture.

Disposable spike (§0a contract) — never promoted by renaming (rule 4). No real
credentials are ever written: the durable async record is a LOCAL sink
(`slack_outbox.jsonl`); a real Slack POST is attempted only from
`SLACK_WEBHOOK_URL` in the environment, which is never persisted to disk.

The control-plane state lives entirely in files under RUNDIR so the watcher and
canary can each run as a single short-lived launchd pass with no daemon:

    runfile.json     run's durable claim: id, incarnation tuple, generation,
                     stop_deadline, state (active|terminal)
    heartbeat        touched by run.py each loop; mtime = run liveness tripwire
    registry.jsonl   append-only, authoritative log (O_APPEND single lines)
    watcher.pass     touched at the end of each successful watcher pass; its
                     mtime is what the canary COUPLES to (watcher liveness)
    broker.token     stub broker file; mtime = broker freshness the canary
                     couples to
    slack_outbox.jsonl  human-readable async mirror of every notice
    watcher.lock     lockf(1) target for the non-blocking single-pass guard
"""
import json
import os
import time


def rundir():
    d = os.environ.get("PROBE3_RUNDIR")
    if not d:
        raise SystemExit("PROBE3_RUNDIR not set")
    return d


def path(name):
    return os.path.join(rundir(), name)


def now():
    """Wall + monotonic. Wall for durable epochs (matches GitHub/token epochs);
    monotonic for elapsed-time deltas that must survive a clock step."""
    return {"wall": time.time(), "mono": time.monotonic()}


def read_json(name, default=None):
    try:
        with open(path(name)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json_atomic(name, obj):
    tmp = path(name + ".tmp")
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path(name))


def mtime(name):
    try:
        return os.stat(path(name)).st_mtime
    except FileNotFoundError:
        return None


def touch(name):
    p = path(name)
    with open(p, "a"):
        os.utime(p, None)


# --- registry: append-only, authoritative -------------------------------------

def _registry_dedup_seen(dedup_key):
    """True if a record with this dedup key is already present. dedup key is
    `run_id+condition` per §5.1 — prevents a re-alert every pass for the same
    already-terminalized condition."""
    if dedup_key is None:
        return False
    try:
        with open(path("registry.jsonl")) as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("dedup") == dedup_key:
                    return True
    except FileNotFoundError:
        return False
    return False


def registry_append(kind, dedup=None, **fields):
    """Append one record. Returns False without writing if `dedup` already
    present (idempotent). O_APPEND keeps concurrent single-line writes atomic."""
    if dedup is not None and _registry_dedup_seen(dedup):
        return False
    rec = {"kind": kind, "ts": now(), "dedup": dedup, **fields}
    line = json.dumps(rec, sort_keys=True) + "\n"
    fd = os.open(path("registry.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
    finally:
        os.close(fd)
    return True


def registry_read():
    out = []
    try:
        with open(path("registry.jsonl")) as f:
            for line in f:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
    except FileNotFoundError:
        pass
    return out


def registry_has(kind):
    return any(r.get("kind") == kind for r in registry_read())


# --- async notice: local durable mirror + opportunistic real POST -------------

def notice(condition, text, **payload):
    """Durable async record (Decision #5): append to the local Slack mirror and
    opportunistically POST to a real webhook if one is in the environment. The
    webhook is NEVER written to disk. Delivery is best-effort and un-acked — a
    miss is recovered by the registry + the 08:00 canary, not by escalation."""
    mirror = {"condition": condition, "text": text, "ts": now(), **payload}
    fd = os.open(path("slack_outbox.jsonl"), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, (json.dumps(mirror, sort_keys=True) + "\n").encode())
    finally:
        os.close(fd)

    hook = os.environ.get("SLACK_WEBHOOK_URL")
    delivered = None
    if hook:
        delivered = _post_slack(hook, text)
    return delivered


def _post_slack(hook, text):
    """Best-effort single POST, short timeout, no retry-until-acked. Returns
    True/False/None(=not attempted). Import is local so the fixture has no hard
    dependency on network libs."""
    import urllib.error
    import urllib.request
    body = json.dumps({"text": text}).encode()
    req = urllib.request.Request(hook, data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False
