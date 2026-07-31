# Probe 4 — GitHub authority canary

> **RE-VERIFICATION ATTEMPTED 2026-07-27 — NOT POSSIBLE. The result below stands
> as a historical record and is NOT currently reproducible.**
>
> Probe 5 flagged this probe as asserting something untrue: it certifies work done
> under `agent` **uid 502 on the mac mini**, where that account later vanished and
> **502 was reassigned to an unrelated human**. Re-verification was attempted and
> found the entire test surface gone:
>
> | Prerequisite                                      | State on 2026-07-27                                      |
> | ------------------------------------------------- | -------------------------------------------------------- |
> | `~/.autopilot/test-app.pem` (App private key)     | **absent** — `~/.autopilot/` does not exist on this host |
> | `bestdan/autopilot-p4-test`                       | **deleted** — "Could not resolve to a Repository"        |
> | `bestdan/autopilot-p4-noinstall` (denial test B3) | **deleted** — same                                       |
> | Test App 4366511 / installation 148292770         | not listable with the current token                      |
> | Branch ruleset 19562224                           | gone with its repo                                       |
> | Host: the mini, `agent` uid 502                   | that account no longer exists there                      |
>
> This is disposable spike infrastructure behaving as designed — it was meant to
> be torn down. The consequence is simply that the GitHub-side findings **cannot
> be re-run without re-provisioning** a new App, key, two repos and a ruleset,
> which is an attended task on the maintainer's GitHub account.
>
> **Separate the two kinds of claim, because the uid problem does not touch them
> equally:**
>
> - **Host-dependent (C1: the agent cannot read the App key).** This rests on the
>   maintainer home being `0700`, which is **verified true on the current host**
>   (`drwx------ danielegan:staff`), and was exercised for real by Probe 5's
>   `Writer` row under the escape-proof uid domain (`write_probe=EACCES`). The
>   structural claim holds here; the literal `cat key → Permission denied` cannot
>   be re-run because there is no key.
> - **GitHub-dependent (sufficiency ops, B1–B4 denials, ruleset behaviour).** These
>   are properties of the App, the ruleset and GitHub's server-side policy, **not
>   of which local uid invoked them**. The uid reassignment does not falsify them;
>   it only means the run cannot be repeated as recorded. They were true of that
>   App at that time and should be treated as evidence about a configuration, not
>   about a machine.
>
> **This does not need re-running to unblock anything.** The kill sheet already
> states that Stage 1's gate re-runs the identical tests against the **real** App
> — which is a stronger check than reconstructing a disposable one, and is where
> the evidence should be re-earned. What must not happen is this document
> continuing to read as a live certification of an account that no longer exists.

**Result: CONFIRMED (GitHub authority) — Linear leg deferred.** Under the actual
`agent` uid (502), every sufficiency operation succeeded via the App
installation token (clone / commit / push to `autopilot/**` / PR open+comment+
close, **plus the GraphQL reads `gh` actually issues**), every safety-critical
denial was **denied server-side** (default-branch and non-matching-branch push
both `GH013` ruleset violations; non-installed repo `Not Found`/404), and the
no-fallback evidence held (agent **cannot read the App key**; removing the token
**fails closed**). The falsification redirect is **not** triggered. The **Linear
read+write operations are confirmed** (read + comment + state change + revert on
a disposable issue); the **identity-scoping** half (agent-scoped tracker token +
denials) defers to Stage 1 — see Results. Kill sheet written first per §7a rule
1; evidence in `results.json`.

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
identity, **or** any denial test is _not_ denied (an irreversible/out-of-scope
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
denial test _fails_ (an irreversible action escapes), fix the **server-side
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

- Host: the mini; commands ran under **agent uid 502** via attended
  `sudo -u agent` (maintainer password), the token piped on **stdin** — never
  argv, env, or disk, and **redacted** from all output.
- **Test App 4366511**, installation **148292770**, `repo_selection: selected`,
  scoped to **`bestdan/autopilot-p4-test`** only. Second repo
  `bestdan/autopilot-p4-noinstall` (App **not** installed) for B3.
- Branch-policy **ruleset 19562224** (`probe4-agent-branch-policy`): push allowed
  only to `refs/heads/autopilot/**`; creation/update/deletion denied on every
  other ref; **bypass_actors empty** (App not exempt).
- Minting: `mint.py` (maintainer-side, `uv run --with 'pyjwt[crypto]'`) reads the
  App key at 0600 and mints a short-lived installation token. `gh` 2.93 at
  `/opt/homebrew` (agent-reachable); agent-side `driver.sh` staged to
  `/Users/Shared/p4/` because the maintainer home is 0700 — which is itself the
  C1 evidence. No `claude`, no production App, no persisted secret.

## Results

**Classification: CONFIRMED (GitHub authority).** Raw evidence: `results.json`.

| Check                          | Verdict   | Evidence                                                                                    |
| ------------------------------ | --------- | ------------------------------------------------------------------------------------------- |
| C1 — agent cannot read App key | ✅ PASS   | `cat key → Permission denied` (agent can't traverse the 0700 maintainer home)               |
| A1 — clone via App token       | ✅ PASS   | cloned                                                                                      |
| A2 — push to `autopilot/**`    | ✅ PASS   | `autopilot/probe4-*` accepted (matches the allowed pattern)                                 |
| A3 — PR open + comment + close | ✅ PASS   | PR #2 created, commented, closed                                                            |
| A3 — GraphQL captured          | ✅        | `RepositoryInfo`, `PullRequestForBranch` — the actual GraphQL `gh` issues                   |
| B1 — default-branch push       | ✅ DENIED | `remote: error: GH013: Repository rule violations found for refs/heads/main`                |
| B2 — non-matching branch push  | ✅ DENIED | `GH013 … refs/heads/random/foo-*`                                                           |
| B3 — non-installed repo        | ✅ DENIED | git `Repository not found`; `gh api → 404 Not Found`                                        |
| B4 — org-level operation       | ⚠️ N/A     | personal account — deferred to Stage 1 (real App under the org)                             |
| C2 — remove token, no fallback | ✅ PASS   | `could not read Username … terminal prompts disabled` — fails closed, no ambient credential |

### Two driver bugs the run surfaced (fixed, re-run clean)

1. **Inherited cwd.** `sudo -u agent` inherits the caller's cwd; when that was
   inside the 0700 maintainer home the agent couldn't `getcwd()`, so every
   `git`/`cd` failed with "Unable to read current working directory" — and the
   naïve denial checks (non-zero exit ⇒ denied) **false-passed** B1/B2. Fixed by
   `cd`-ing to an agent-safe dir first **and** requiring denial verdicts to match
   a real policy-denial reason (`GH013`/ruleset/not-found/…), not merely a
   non-zero exit. The clean re-run shows genuine `GH013` ruleset violations.
2. **No-cred hang risk.** Added `GIT_TERMINAL_PROMPT=0` so a credential-less
   clone fails closed instead of prompting — making C2 a clean, non-interactive
   fail-closed.

### Linear read+write leg — operations confirmed; identity-scoping deferred

Against disposable issue **PRE-627** (project `auto-pilot-gh-app-test`, PRE
team), all tracker operations the delivery loop needs succeeded: **read**
(`get_issue` → Backlog), **write comment** (created, then deleted), **write
state** (Backlog → In Progress, then reverted). Cleanup left the issue in
Backlog with no comment.

**Caveat (why it isn't the full leg):** this ran via the **maintainer MCP
identity**, not an agent-scoped Linear token — **agent→Linear auth is not
provisioned** in this session. So the tracker _operations_ are confirmed, but the
**identity-scoping and denial** half (agent-scoped token; can it only touch what
it should?) defers to **Stage 1**, where the delivery loop's tracker credential
is provisioned and validated under the agent identity.

### What this closes / does not close

Closes: the App identity is **sufficient** for the GitHub delivery loop and
**constrained** by server-side policy — the denial tests (the Decision #5 "no
irreversible action unattended" boundary) are enforced by GitHub, with the App on
no bypass list, and delivery uses **only** the App token (no personal/ambient
fallback). Does **not** close: the **org-level** denial (B4, personal-account
N/A), the **tracker** leg (Linear, deferred), and the broker's mint fault drills
(expired/missing-key/revoked/clock-skew) — Stage-1-proper work. Stage 1's gate
reruns these identical tests against the **real** App. Spike code is disposable;
never promoted by renaming (rule 4).
