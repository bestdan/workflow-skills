# Probe 4 — GitHub authority canary

**Result: PENDING.** Kill sheet only — no fixture run yet. Execution is **blocked
on user-provided provisioning** (a disposable test GitHub App + test repo) and an
**attended agent-uid path** (`sudo -u agent`, maintainer password); see
**Prerequisites** below. Kill sheet written first per §7a rule 1.

**Scope decisions (2026-07-22):** the test App + repo live on the maintainer's
**personal account** — so the **org-level denial test (B4) is N/A** and defers to
Stage 1 (no org to act against). Agent-uid commands run **attended via the `!`
prefix** (`sudo -u agent …`, maintainer password) — **no standing sudo grant**.
The **Linear read+write leg is IN scope** this pass, against a disposable issue.

Disposable spike under §0a's contract (rule 4: never promoted by renaming). The
**production App never enters the spike** — a separate, disposable **test App**
installed on **one disposable test repo** only. No production credentials, no
personal `GH_TOKEN`, no secret is ever persisted to the fixture; the App private
key lives at a maintainer path outside the repo and is referenced, never copied
in.

Exercises §2.1 (token/credential resolution, no-fallback), §2.2 (App +
rulesets / server-side authorization), and the Stage-1 gate's GitHub legs. This
probe is the **canary**; Stage 1's gate later reruns the identical tests against
the **real** App (§7a row 4).

## Kill sheet (from §7a, priority 4)

### Key assumption / falsifier

> _The App identity is both **sufficient** for the delivery loop and
> **constrained** by server-side policy — the latter now the enforcement of "no
> irreversible action unattended" (Decision #5), so the denial tests are
> **safety-critical**, not merely correctness._

Falsified if any required delivery operation is impossible under the App
identity, **or** any denial test is *not* denied (an irreversible/out-of-scope
action escapes server-side policy), **or** credential instrumentation shows the
delivery only worked by falling back to an ambient/personal token.

### Method — under the actual `agent` uid, instrumented

All git/`gh`/GraphQL run **as uid 502 (`agent`)** with credential resolution
instrumented (capture which helper answers; prove the token's origin). Against
the **disposable test App**, installed on the **disposable test repo** only.

**A. Sufficiency (positive path — must all succeed):**

1. `clone` the test repo over the App installation token.
2. `commit` + `push` to a **non-default feature branch**.
3. Open a **PR**, add a **comment**, then **close** it.
4. The **GraphQL reads `gh` actually issues** along that path (e.g. `gh pr`
   view/status) — captured, not assumed, since `gh` mixes REST and GraphQL.
5. Tracker **read + write** (IN scope) — read a disposable **Linear** issue and
   write it (comment / state change) as part of the delivery loop.

**B. Denial tests (safety-critical — must all be DENIED):**

1. Push to the **default branch**.
2. Push to a **non-matching branch** (outside the allowed ruleset pattern).
3. Any operation on a **non-installed repo**.
4. ~~An **org-level** operation.~~ **N/A this pass** — test App is on a personal
   account; org-level denial defers to Stage 1 (real App under the org).
   For B1–B3: confirm the **App is absent from every bypass/allow list** that
   could let it through (ruleset bypass actors, repo admin, branch-protection
   exemptions).

**C. No-fallback credential evidence (positive):**

Instrumented helpers (§2.1) show every successful op used **only** the test
App's installation token — no personal `GH_TOKEN`, no ambient keychain/gh login,
no `~agent` credential store fallback. Removing the token must make the op fail
(not silently succeed via another credential).

**Adjacent (Stage-1-proper, may be noted not run here):** mint fault drills
(expired token, missing key, revoked installation, clock skew) and the broker's
fixed-config refusals — those are broker-side Stage 1 work; this probe is scoped
to the App's **sufficiency + constraint + no-fallback** as the canary.

### Pass threshold

All of: every **A** op succeeds via the App installation token under the agent
uid; every **B** op is **denied server-side** with the App on no bypass list;
**C** shows positive no-fallback evidence (only the App token, and removal fails
closed). The GraphQL reads are captured explicitly.

### Inconclusive condition (rule 3)

Classify **inconclusive**, not pass, if: the test App cannot be scoped/installed
to mirror the intended production permission set (so denials can't be attributed
to policy vs a misconfigured test App); agent-uid execution can't be achieved
cleanly (so results reflect the maintainer's ambient creds, not the agent's);
or the credential instrumentation can't distinguish token origin (so "no
fallback" is unproven).

### Evidence required (rule 4)

Checked in beside this file: the exact commands, **sanitized** transcripts
(tokens/headers/private key **redacted** — never persisted), the captured
GraphQL operations, the allow/deny outcome per test, non-secret env metadata
(agent uid/groups/HOME/PATH), result, decision. The test App's numeric IDs may
be recorded; its private key and any bearer token must not.

### Time cap

Half a working day of **execution** once prerequisites are in place (§7a rule 3
default). Provisioning time (App/repo creation) is not counted against the cap.

### Dependent work gated on this probe

Stage-1 broker hardening and the entire GitHub delivery path. A false permission
model here would misdirect broker/ruleset design.

### Redirect if falsified

Change App permissions/helpers, or replace unsupported `gh` paths with fixed API
calls; **do not build broker hardening around a false permission model.** If a
denial test *fails* (an irreversible action escapes), fix the **server-side
ruleset** first (this is the Decision #5 safety boundary), not the client.

## Prerequisites (execution is blocked until these exist)

**User-manual (personal GitHub account):**

1. **Disposable test GitHub App** on your personal account, permissions scoped to
   the production set (**Contents: R/W, Pull requests: R/W, Issues: R/W**; **no**
   administration, **no** default-branch bypass). Generate a **private key** and
   place it at `~/.autopilot/test-app.pem` (0600) — **outside** the repo. Record
   the App ID + Installation ID (non-secret).
2. **Install** that App on the test repo **only** (below) — not "All repos".

**Automatable (I can run these via `!`, or you delegate):**

3. **Test repo** `autopilot-p4-test` (private) + a **default-branch ruleset**
   (block direct pushes / restrict to the App's allowed pattern) + a second
   **non-installed** repo `autopilot-p4-noinstall` for denial test B3.
4. **Disposable Linear issue** in a throwaway/sandbox project for the tracker
   read+write leg.

**Execution access:**

5. **Attended agent-uid path** — you run each command through the `!` prefix as
   `sudo -u agent …` (maintainer password). No standing sudo grant is made.
6. Runs on the mini where `agent` (uid 502) exists — **confirmed present on this
   host.**

## Environment (non-secret)

_To be filled at execution._

## Results

_To be filled at execution._
