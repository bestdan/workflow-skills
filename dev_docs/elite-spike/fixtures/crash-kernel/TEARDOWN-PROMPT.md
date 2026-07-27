# Probe 5 — Part E teardown handoff

**Paste the block at the bottom into a fresh session.** Written 2026-07-27, after
Probe 5 was classified CONFIRMED and every documentation item closed. Teardown is
the only thing left.

## Why this needs its own session

Teardown is destructive and mostly irreversible, and the things it removes are the
things that made the evidence possible. Two of the four steps need a real tty
(`sudo` outside the probe5 sudoers alias), and one of them — deleting the rundirs
— destroys the raw `state.db` and reconcile logs that `results.json` was derived
from. After that, the evidence is whatever is committed to git.

## The one non-obvious trap

**Do not `launchctl disable` the `com.probe5r2.sup.*` labels at teardown.**

The instinct is to disable them as a tripwire, the way the incident's
`com.probe5.sup.*` labels were disabled on the mac mini. That would be wrong here
and would leave a trap for future work. The fixture's own rule (`probe5-todo.md` →
Gotchas) is that a disabled label may only be re-enabled if it has **no load
history** — and these have been loaded, repeatedly, by the passing rows. So
disabling them creates labels that the project's own rule says must not be
re-enabled without investigation, on a probe that is finished and correct.

Boot them out (they are already not loaded) and leave the override database alone.

`com.probe5r2.drill` is a sacrificial decoy from a dead-man drill and is already
`disabled`. **Leave it disabled** — that is its correct final state.

---

## PASTE THIS INTO THE NEW SESSION

```
Perform Part E teardown of "Probe 5 — crash kernel", a disposable falsification
spike that is now COMPLETE. Nothing is being tested; this is cleanup only.

Repo:   github.com/bestdan/workflow-skills
Branch: bestdan/elite-probe5-crash-kernel   (pushed through 37fcf01, tree clean)
Fixture: dev_docs/elite-spike/fixtures/crash-kernel/
Host:   Daniel's MacBook Pro, maintainer danielegan (uid 501), agent uid 502

READ FIRST:
  dev_docs/elite-spike/fixtures/crash-kernel/TEARDOWN-PROMPT.md  <- why, and the trap
  dev_docs/elite-spike/fixtures/crash-kernel/probe5-todo.md      <- Part E + Gotchas

STATE AS OF HANDOFF (verify, do not assume — it is a live host):
  - launchd: NOTHING loaded. Override DB has com.probe5r2.sup.{readopt,orphan}
    => enabled and com.probe5r2.drill => disabled.
  - /usr/local/probe5 exists, root:wheel 755, five files (three helpers + staged
    runsurrogate.py and incarnation.py).
  - /etc/sudoers.d/probe5 exists, root:wheel 0440.
  - 57 rundirs under $TMPDIR/probe5.* holding state.db + reconcile logs.
  - uid 502 has 1 process (a respawned system daemon, not ours).

RULES:
1. LEAVE THE `agent` ACCOUNT IN PLACE. Stage 1 and Stage 2 both need it, and
   deleting/recreating it is what produced the uid-reassignment hazard that
   caused a four-day outage (dev_docs/tasks/probe5-incident-evidence/).
2. DO NOT `launchctl disable` the com.probe5r2.sup.* labels. They have load
   history, and the project's own rule forbids re-enabling a disabled label that
   does. Boot out only. Leave com.probe5r2.drill disabled — it is a decoy.
3. DO NOT delete dev_docs/tasks/probe5-incident-evidence/. That is the incident
   record and outlives the spike.
4. DO NOT uninstall Homebrew python@3.12. It is a general tool, was not installed
   solely for this, and removing it is not part of Part E.
5. Deleting the rundirs destroys the raw state.db and reconcile logs behind
   results.json. ASK ME FIRST whether I want any archived before you do it.
6. sudo steps need a real tty. Prepare the exact commands and hand them to me;
   once /etc/sudoers.d/probe5 is gone the NOPASSWD helper path is gone too, so
   order matters — remove the helper tree BEFORE or WITH the sudoers fragment,
   not after.

STEPS (verify state before and after each):
  a. Confirm nothing is loaded and no probe5 processes are alive; kill any stray
     runsurrogate.py / supervisor.py daemon, and any leaked `sleep`-based dead-man
     timers.
  b. launchctl bootout the three com.probe5r2.sup.* labels (no-ops if unloaded).
  c. I run: sudo rm -f /etc/sudoers.d/probe5 && sudo rm -rf /usr/local/probe5
  d. After my approval on rule 5: rm -rf "$TMPDIR"/probe5.*
  e. Verify: no probe5 in launchctl list, /usr/local/probe5 and
     /etc/sudoers.d/probe5 gone, no rundirs, uid 502 has only system daemons,
     `id agent` still works, and my login sessions are intact.
  f. Update probe5-todo.md Part E to record teardown done + date, and commit.
     Do not push to main; the branch is bestdan/elite-probe5-crash-kernel.

SANITY CHECK BEFORE YOU START: `git status` must be clean and the branch pushed.
If it is not, stop and tell me — the fixture code is about to lose its runtime
surface and anything uncommitted becomes unreproducible.
```
