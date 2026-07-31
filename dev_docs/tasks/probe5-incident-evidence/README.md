# Probe5 orphan-reaper incident — evidence

On 2026-07-23 a `com.probe5.sup.orphan` supervisor from this crash-kernel spike
was bootstrapped into per-user launchd (`gui/501`) in **uid domain mode** with
`PROBE5_AGENT_UID=501` — the maintainer's *own* uid, not the dedicated `agent`
account (uid 503).

In uid mode a reap is `kill(-1, sig)` issued as `agent_uid` (see
`reaper.Domain.signal_all`), plus the privileged helper `/usr/local/probe5/p5-reap`
running `/bin/kill -s <SIG> -1`. Sent against uid 501 that signals **every
process the maintainer owns**, including every SSH login. With `KeepAlive=true`
the supervisor killed itself in its own reap, launchd relaunched it ~every
1–2 s, and the loop ran from **Jul 23 07:55 until Jul 27 01:20** — when it was
manually booted out. Symptom: SSH authenticated, then the whole session died
within ~0.6 s.

## Files

| File | What it shows |
|---|---|
| `orphan.3b_m82ui.log-summary.txt` | head/tail + count of the 15 MB `supervisor.launchd.out`: **242,568** `stop_intent committed (reap_stop_intent)` lines (the line right before `reaper.reap()`), spanning the four-day loop. |
| `orphan.3b_m82ui.plist`, `orphan.oio4lb1x.plist` | the bootstrapped uid-mode plists — `PROBE5_DOMAIN_MODE=uid`, `PROBE5_AGENT_UID=501`. |
| `orphan.*.reconcile.jsonl` | reconcile decisions; `domain_live` is a 400+ PID list of all uid-501 processes, `escape_proof: true`. |
| `orphan.3b_m82ui.state.db` | the durable kernel state at the time. |
| `privileged-surface.txt`, `usr-local-probe5.sha` | proof the `/usr/local/probe5` helper tree (incl. `p5-reap`) was owned by `danielegan`, not `root` — a broken trust boundary. |
| `mycelium-data/` | archived `~/src/mycelium-lib/data/` from the unrelated, inert `com.mycelium.memory-*` jobs removed in the same cleanup. |

## Fix (this commit)

- `reaper.py`: `Domain.__init__` refuses uid mode when `agent_uid ∈ {0, caller's uid}`; `gentoken` tokens are validated (whitespace-free, len ≥ 12); `list_gentoken_pids` excludes the caller's own uid. The reaper now **fails closed** instead of reaping the maintainer.
- `scenarios.py`: `install_supervisor` resolves the dedicated `agent` uid instead of writing `str(UID)`, and refuses to install a uid-mode supervisor whose domain is root or the caller.

## Remediation performed on the host (not in git)

launchd jobs `com.probe5.sup.{orphan,readopt}` booted out + disabled; the
`/usr/local/probe5` helper tree and `/etc/sudoers.d/probe5` removed; stray uid-503
`runsurrogate.py` killed; stale `$TMPDIR/probe5.*` rundirs deleted; the six
`com.mycelium.memory-*` jobs + plists + `~/src/mycelium-lib` removed.
