---
type: handoff
title: Handoff — Probe 5b task 2 (fixture harness)
status: active
owner: Daniel Egan
created: 2026-07-28
expires_with: probe5b_task_2
---

# Handoff — Probe 5b, task 2

**DELETE THIS FILE when task 2 is complete.** It is scaffolding, not evidence:
it exists to carry three local traps that live nowhere in the repo, plus the
facts the review round already established, into a fresh context. Once task 2
is done it is stale by construction — the next reader would be taking direction
from a snapshot of a state that has moved. `git rm -f
dev_docs/tasks/probe5b_plan/probe5b_task2_handoff.md` and say so in the task-2
commit. Everything durable belongs in the kill sheet, the plan tasks, or
`results.json`; if something here is worth keeping, move it there before
deleting rather than leaving this file alive as its home.

---

Continue the auto-pilot "E-lite" workstream, Probe 5b, task 2.

Repo:     github.com/bestdan/workflow-skills
Branch:   bestdan/autopilot-e-lite-design  (PR #243, draft)
Worktree: ~/src/worktrees/workflow-skills/e-lite
Design:   dev_docs/auto-pilot-e-lite-design-2026-07-21.md

STATE: Probes 1-5 are closed. Probe 5b was falsified on 2026-07-28 — by
inventory, before any fixture ran — and the design doc carries that verdict,
its redirect, and the consequences. The kill sheet is written, was reviewed
(two independent reviewers, one of which ran experiments), was revised against
that review, and is now APPROVED. Plan task 1 is `done`. The approval gate is
open: fixture code may be written, starting at task 2. Do not re-derive any of
the above, and do not re-litigate row 5b's verdict — no leg can change it.

READ FIRST (source of truth; this file deliberately does not restate them):
  dev_docs/elite-spike/fixtures/runaway/probe5b-runaway.md
      <- THE governing document. Every threshold, every containment assertion,
         the verdict lattice, the evidence schema, and the "Review round"
         section recording what was already wrong once and why.
  dev_docs/tasks/probe5b_plan/probe5b_task_2.md   <- your task
  dev_docs/tasks/probe5b_plan/probe5b_plan.md     <- decisions (A)-(D)
  dev_docs/tasks/probe5b_plan/probe5b_task_3.md,_4.md,_5.md
      <- the legs you are building the harness FOR; build to their thresholds,
         not to a design you prefer
  dev_docs/tasks/probe5-incident-evidence/
      <- READ BEFORE WRITING ANYTHING THAT SPAWNS OR SIGNALS. Four days of
         reaped SSH logins. Do not modify this directory; it outlives the spike.
  §7a rules 1-6, §0a rule 4, §5.1, §4.2, §6 of the design doc

Commits 097c1bc, 233210d, 83103b3 (the falsification and redirect), 67b88ca
(kill sheet), f14e125 (its revision), 722ae67 (plan sync), 29692a9 (approval).
`git show` any of them for the reasoning behind an edit.

YOUR TASK: task 2 only — the disposable fixture harness plus its smoke leg.
`runaway.py`, `driver.sh`, `common.py`, `scenarios.py` under
dev_docs/elite-spike/fixtures/runaway/. Legs 1-4 are tasks 3-5; do not build
them yet. Stop at task 2's acceptance criteria and hand back.

THINGS THE REVIEW ALREADY ESTABLISHED — inherit them, do not rediscover:
- A Seatbelt profile deny returns EPERM (errno 1), NOT EACCES. Measured. EACCES
  belongs to the filesystem-uid substitute in the rule-6 repeat method. The
  control write to the same directory must succeed in the same run or the
  denial is not attributable.
- /usr/bin/python3 CANNOT exec under the rendered profile: it re-execs into
  CommandLineTools/Library/..., Seatbelt matches the RESOLVED path, and that is
  outside the granted CommandLineTools/usr subpath. Pin a Homebrew interpreter
  (resolves into the granted /opt/homebrew/Cellar) and smoke-test the exec
  under the profile BEFORE any leg. This is a construction-time wall that will
  otherwise eat the time box.
- Probe 2's p_uniqueid reader
  (dev_docs/elite-spike/fixtures/process-binding/incarnation.py) works
  unprivileged same-uid here — the cross-uid EPERM that forced Probe 2 to root
  does not apply. Use it for the pre-signal identity re-validation.
- Nothing on the supervisor_scan call graph (:3019-:3133) reads the process
  table; the only ps/pgrep/proc_pidinfo sites are :1584 and :5178.

CONTAINMENT IS THE PART THAT CAN HURT SOMEONE. The kill sheet states these as
construction-time assertions, not prohibitions, because the failure mode is an
implementer who satisfies the words and still kills the wrong thing:
- pgid(surrogate) != pgid(driver) AND pgid(surrogate) == pid(surrogate).
  "The fixture's own group" alone is NOT sufficient — a child spawned without
  setpgid/setsid inherits the driver's group, and killpg then takes the driver,
  the shell, and the SSH session with it.
- same_incarnation(recorded, live) immediately before EVERY signal; on mismatch
  or ESRCH, skip the signal and record escaped/already-dead.
- The deadline must survive a wedged driver: parent-death pipe AND an
  independent watchdog in its own group. Predicate: kill -9 the driver mid-run,
  everything downstream exits within N seconds.
- Unprivileged only. No sudo, no launchd bootstrap into a real uid domain, no
  root helpers. Resolve the `agent` account BY NAME, never by uid number; the
  account stays and nothing here creates or deletes it.
- Every signal validates its target pgid is non-empty, numeric, positive. An
  empty variable in `kill -- -$pgid` signals the caller's own group. That is
  the outage's exact symptom shape.

CONSTRAINTS:
- Spike code is disposable and is NEVER promoted to production by renaming
  (§0a rule 4). You are not writing production code.
- dev_docs/elite-spike/fixtures/ is excluded from dprint and shfmt ON PURPOSE —
  results.json pins sha256s of that tree. Do NOT "fix" that exclusion.
- dev_docs/tasks/ is gitignored, so commits there need `git add -f`. Deliberate
  (decision (C)): spike plans are evidence and live on the branch.
- Never commit to main. Branch is bestdan/autopilot-e-lite-design.
- No real `claude` launch, no network, no GitHub App, no real remote or tracker.
- Task 2 delivers the harness and the smoke leg (quiescent healthy agent plus
  BOTH near-misses: a short legitimate pause well inside --pause-exempt-max,
  and a run completing just before --until). Both must halt nothing.

LOCAL TRAPS THAT ARE NOT IN THE DOCS — the reason this file exists:
- scripts/check.sh FAILS with ~62 pre-existing failures. Cause is environmental
  and confirmed: a global pre-commit hook fires inside the fixtures' own scratch
  git repos ("Direct commits to 'main' are blocked"), so the seed commit never
  lands and restack/doctor/exit-contract tests cascade from "not a git
  repository". They pre-date this work and are not yours to fix. The count
  drifts run to run (70/64/62 observed) — compare against a baseline you take
  yourself, not against a number in this file.
- RUN check.sh UNSANDBOXED. Sandboxed runs add ~6 more failures because
  sandbox-exec cannot nest, so profile-compile tests fail spuriously. This also
  means the fixture's own profile work needs the sandbox disabled.
- $TMPDIR differs sandboxed vs unsandboxed. A file written under one is not
  visible under the other; it will look like the file vanished.

ONE OPEN DECISION — ask the maintainer, do not assume:
Tasks 2-6 each carry "scripts/check.sh passes" as a code-enforced acceptance
criterion, and it CANNOT pass in this environment for reasons unrelated to this
work. It has to mean "no new failures against a baseline recorded before the
change". Confirm that reading, and record the baseline in the fixture's
evidence so the claim is checkable rather than asserted.

---

**Reminder: delete this file as part of finishing task 2.**
