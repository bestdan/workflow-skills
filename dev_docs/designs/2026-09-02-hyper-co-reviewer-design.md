# co-review — a Hyper-backed reviewer

Add a fifth built-in reviewer to `/co-review` that calls the Charm Hyper
inference gateway over HTTP. It replaces `devin` in the author's roster. The
repo keeps `devin` as a built-in; this design adds, it does not remove.

The implementation lands on the same branch as this document:
`scripts/hyper-review.py`, `skills/co-review/reviewers/hyper.md`, and the
SKILL.md, README, and typecheck edits under Repo housekeeping.

## Bottom line

- **Gating is the user's job, not the plugin's.** `hyper` is a built-in for
  trust and dispatch, and is **never auto-enabled**: it runs only when
  `.co-review.yml` names it. No repo-visibility detection, no key-presence
  trigger. See [Gating](#gating--the-users-decision-per-repo).

- **Add `hyper` as a built-in reviewer, driven by a stdlib-only wrapper at
  `scripts/hyper-review.py`, with a playbook at
  `skills/co-review/reviewers/hyper.md`.** A custom `command:` entry cannot do
  the job: SKILL.md treats every `command:` as untrusted and prompts on each
  run, which `--non-interactive` turns into a silent skip (SKILL.md:142, :183).
- **Pin `deepseek-v4-pro` at `xhigh` reasoning effort.** The reviewer's value
  is training-lineage decorrelation from the four Western-lab reviewers, not
  raw capability. Fallback `kimi-k2.7-code`; upgrade `kimi-k3`.
- **No new permission rule.** The shared prefix rule
  `Bash(python3 <PLUGIN-CACHE>/workflow-skills/workflow-skills/:*)`
  (SKILL.md:203) already approves any python3 script the plugin ships. The
  wrapper's read-only posture comes from its construction, not from a pinned
  command string.
- **No pre-flight auth probe.** An HTTP call with a bad key fails fast. The
  wrapper exits with a distinct code on a missing key before any network call,
  and on `401`/`403` after one.
- **The wrapper never synthesizes the `REVIEW_COMPLETE:` sentinel.** It prints
  the model's text verbatim and reports truncation on stderr. SKILL.md:299
  already routes verdict-less findings to the reconciler.

## The problem

The remaining roster after `devin` is `codex` (OpenAI), `agy` (Gemini),
`copilot` (GitHub-routed OpenAI/Anthropic), and the main Claude agent. All four
descend from Western frontier-lab training pipelines. `devin` on `swe-1.6`
added little in practice, and one plausible reason is that it converged on the
same findings as its neighbours (inferred from the author's experience, not
measured).

A reviewer earns its wall-clock cost when its false-negative set differs from
the others. Capability only has to clear a floor. A model from a Chinese-lab
or open-weights pipeline shares little training data, RLHF process, or
evaluation suite with the other four, so it is the cheapest way to buy that
difference.

## Why Hyper, and which model

Facts about Hyper below were verified by the author against
`https://hyper.charm.land/v1/models` on 2026-09-02. Facts about model quality
are inferred.

- Base URL `https://hyper.charm.land/v1`. Bearer token in `HYPER_API_KEY`,
  prefixed `sk-hyper-`. OpenAI-compatible and Anthropic-compatible endpoints.
  `/v1/models` needs no authentication.
- The catalog holds 32 models. None is Anthropic, OpenAI-proprietary, or
  Gemini, whatever the marketing page says. The families are DeepSeek, GLM,
  Kimi, Qwen, MiniMax, Llama, Gemma, and one open-weights `gpt-oss-120b`.
  That absence is the point: every model here is decorrelated from the roster.
- Credits: 100 hypercredits/month on the free tier, 1 credit ≈ 5¢.

| Model               | Context | Max out | $/Mtok in | $/Mtok out | Effort levels                |
| ------------------- | ------: | ------: | --------: | ---------: | ---------------------------- |
| `deepseek-v4-pro`   |    1.0M |    384k |      2.40 |       4.80 | high, xhigh                  |
| `kimi-k2.7-code`    |    262k |     16k |      1.03 |       4.36 | —                            |
| `kimi-k3`           |    1.0M |     16k |      3.27 |      16.33 | low, high, max (default max) |
| `deepseek-v4-flash` |    1.0M |    384k |      0.20 |       0.40 | high, xhigh                  |

**Primary: `deepseek-v4-pro` at `xhigh`.** The 1M context takes any diff with
no truncation decision. Explicit effort control lets the review spend reasoning
where the rubric asks for it. A 20k-token diff plus a 2k-token review costs
about $0.06, so roughly one credit per review.

**Fallback: `kimi-k2.7-code`** at half the input price. **Upgrade:
`kimi-k3`** at about $0.10 per review if `deepseek-v4-pro` findings
disappoint. The catalog (re-read 2026-09-02) gives `kimi-k3` effort levels
`low`/`high`/`max` under `reasoning.effort_levels`; the brief's catalog rows
listed none.

The quality ranking among these three is inference. Several of these models
postdate the author's knowledge and none has been measured on this task. The
`select-coder` matrix carries board results for `kimi-k3` and `glm-5.2` only,
and only as `devin:` routes (`skills/select-coder/matrix.md:201-202`).

**How to settle it.** Run two candidates on the same three PRs with identical
`<INPUT>`. Feed both into one reconciler pass, labelled neutrally as the skill
already does (SKILL.md:322). Count, per model, findings the reconciler marked
high or medium that no other reviewer raised. That count is the decorrelation
the reviewer is bought for. Pin the winner; keep the other as the documented
fallback.

## Design

### Shape

The reviewer follows `codex.md`, the stateless reviewer. One reviewer file,
one wrapper script, one dispatch line, no probe, no empty-input guard.

**`skills/co-review/reviewers/hyper.md`** carries the invocation, the model
pin rationale, the failure table, and a Permissions section that points at the
shared prefix rule. It ships **no** `json`-fenced rule of its own (see
[Permissions](#permissions) for why).

**`scripts/hyper-review.py`** is a stdlib-only Python 3 script (`urllib`,
`json`). The repo already holds one stdlib HTTP server at
`scripts/local-review/server.py`, and the house rule refuses a new dependency
without discussion; `requests` would be one. Interface:

- **stdin** — the assembled `<INPUT>`: rubric, optional requests, diff. Read
  in full before any network call.
- **argv** — `--model <id>` (default `deepseek-v4-pro`), `--effort <level>`
  (optional; omitted for models with no effort levels), `--max-tokens <n>`
  (default 8192), `--timeout <seconds>` (default 600). No key in argv, ever.
- **env** — `HYPER_API_KEY`. Nothing else is read.
- **request** — `POST /v1/chat/completions`, `system` = the shared
  `<POINTER>` from SKILL.md:128, `user` = the stdin bytes. Non-streaming.
- **stdout** — the response's `content` field, verbatim, and nothing else. Any
  separate reasoning field is dropped.
- **stderr** — exactly one line, `HYPER_REVIEW: <status> model=<id>
  finish_reason=<r> in=<n> out=<n>`, so the run summary can quote it.
- **exit codes** — `0` a response arrived (any `finish_reason`); `2` bad usage
  or `HYPER_API_KEY` unset; `3` auth rejected (`401`/`403`); `4` rate limited
  after retry; `5` credits exhausted; `6` other HTTP or network failure; `7`
  timeout; `8` empty stdin.

Dispatch line, GitHub mode, no requests:

```
cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hyper-review.py" --model deepseek-v4-pro --effort xhigh
```

The `--local` and with-requests variants follow the shared rules at
SKILL.md:117-122. The wrapper reads `<INPUT>` from the pipe, so the path stays
out of the command string, as for `codex`. It needs network, so the line runs
unsandboxed in the Bash tool, as the three cloud reviewers do (SKILL.md:83).

### Gating — the user's decision, per repo

The plugin ships the capability and documents the consideration. It does not
decide where the diff may go. There is no public/private detection and no
allow-list of repos in code.

The gate is the config. SKILL.md:40-48 puts `.co-review.yml` under
`dev_docs/co-review/`, which the setup step adds to `.gitignore`, so the file
is local, per-user, and per-repo. SKILL.md:142's "committed to the repo under
review" is the defensive case for a file someone did commit, not the normal
state. A per-repo local file is exactly where "may this repo's diff leave for
Hyper" belongs.

What that forces: `hyper` must not join the config-absent auto-run path
(SKILL.md:182 and step 4, which probe `PATH` and run available built-ins).
`hyper` has no binary. The only ambient signal would be `HYPER_API_KEY`, and
gating on it would enable `hyper` in every repo where the key is exported —
plugin-side gating under another name. So `hyper` is a built-in for **trust
and dispatch** (no untrusted-`command:` prompt, shared permission rule) and
not for **detection**. It runs only when the config lists it. The SKILL.md
edits implement this in the config-absent bullets, the detection paragraph,
and the non-interactive default.

### Permissions

The shared prefix rule at SKILL.md:203 covers the dispatch. SKILL.md:209
explains why that rule is a prefix and not an exact match: the installed path
carries the plugin version, so an exact rule dies at every release. The same
argument applies to the wrapper, and with more force, because the wrapper is
already covered.

The exact-match discipline exists to pin a read-only posture into an agentic
CLI's flags (SKILL.md:215). The wrapper has no flag that widens what it can
do. It cannot edit, cannot run a shell, and cannot read a file it was not
piped. A changed `--model` changes cost, not capability. So the prefix rule
loses nothing here that exact matching would have kept.

**Why `hyper.md` ships no rule template.** `scripts/coreview-rule-drift.py`
attributes an operator's rules to a reviewer by command word
(`analyze`, lines 210-216), and `python3` is not in `GENERIC_BINARIES`
(line 67). A template beginning `Bash(python3 …` would claim every `python3`
rule as owned, and the shared prefix rule does not full-match such a template,
so the script would report the shared rule as DEAD on every configured
machine. That is exit `1`, a `WARN` in `/doctor` Check 7, and a drift note in
every co-review summary. A reviewer file with no `json` fence is skipped
(`analyze`, line 204). The cost is that the drift check does not cover
`hyper`. That gap is already closed from the other side: the drift check
itself runs under the same shared rule (SKILL.md:218), so a missing shared
rule surfaces as the check being denied.

### Credentials

`HYPER_API_KEY` reaches the wrapper through the environment only. It never
appears in argv, in stdout, in the stderr status line, or in any error text;
the wrapper prints HTTP status codes and never response headers or request
bodies on failure.

Resolution reuses the contract in `dev_docs/auth_key_access.md` rather than
adding a second one: `$HYPER_API_KEY`, else `$HYPER_API_KEY_REF` resolved by
`$HYPER_API_KEY_RESOLVER` (default `op`). The wrapper calls
`resolve_key("HYPER_API_KEY")` from `commands/handlers/assets/_secret_resolve.py`
so the raw secret never passes through the agent's transcript. Two consequences
from the author's `op` conventions:

- `op` never works sandboxed. The dispatch already runs unsandboxed for
  network, so an interactive session with a live `op` session resolves the
  key inside the same call. A lapsed session fails as `promptError`; the
  wrapper maps that to exit `3` and the summary says "sign in with
  `op signin`".
- Unattended runs (`/auto-pilot`, `/deliver-task`) have no `op` session.
  They need `$OP_SERVICE_ACCOUNT_TOKEN` or `$HYPER_API_KEY` injected directly,
  exactly as `skills/auto-pilot/references/launch-preflight.md:46-50` already
  requires for `LINEAR_API_KEY`. The launch preflight should probe
  `HYPER_API_KEY` the same way when `hyper` is configured.

### Auth probe

None. The probes on `agy` and `devin` exist because those CLIs fail slow;
`agy` burns 30s on a doomed OAuth flow per dispatch (SKILL.md:85). A `POST
/v1/chat/completions` with no key or a bad key returns `401` in one round trip
(verified live 2026-09-02: bodies `{"error":"missing authorization"}` and
`{"error":"authentication failed"}`; the wrapper with `sk-hyper-invalid`
exited `3`).

`/v1/models` is the wrong probe. It needs no authentication, so a `200` proves
reachability and nothing about the key. `GET /v1/credits` is the right one if
a probe is ever wanted: authenticated, no tokens spent, returns
`{"balance": N}` (documented in `/docs/llms-full.txt`). It buys nothing over
the dispatch itself, which fails just as fast and reports through exit code
`3`, so the design follows `copilot`: no pre-flight, failure caught from the
dispatch (SKILL.md:85). `hyper.md` documents the `curl` for a manual check.
The one zero-cost check the wrapper does run is credential presence, before
any socket opens, exit `2`.

### Completion sentinel and truncation

A one-shot completion has no agentic loop, so the two failure modes the
sentinel was built against — an agent that stops after a tool result, and an
agent that reviews a stale conversation (SKILL.md:138, :130) — cannot occur.
The remaining risks are a model that ignores the format instruction and a
response cut at `max_tokens`.

The wrapper never appends a sentinel. A synthesized `REVIEW_COMPLETE: PASS`
would be exactly the forbidden inference at SKILL.md:299. The skill's existing
rule handles the rest: findings text without a verdict line goes to the
reconciler with a note; verdict-less output with no findings is a skip.

On `finish_reason == "length"` the wrapper still exits `0`, prints the partial
text, and reports `finish_reason=length` on stderr. The dispatching agent
treats that as findings-without-verdict. The 16k output cap on `kimi-k3` and
`kimi-k2.7-code` is enough for the review text: a review is a list of
findings, and 2k tokens is typical. Whether reasoning tokens count against that
cap on Hyper is not verified; if they do, `kimi-*` at high effort is the model
most likely to truncate. `--max-tokens 8192` bounds cost on every model and is
below every cap in the table.

### Timeouts and failure modes

The skill bounds CLI reviewers at 15 min under `--non-interactive`
(SKILL.md:175). The wrapper's own `--timeout 600` sits inside that bound, so
the wrapper reports a timeout itself (exit `7`, one stderr line) instead of
being killed with nothing captured. The Bash tool's foreground ceiling is about
7 min (SKILL.md:138), so the dispatch backgrounds under `--non-interactive`
and captures stdout by `> <file>` redirection. Redirection is transparent to
the permission matcher, so unlike `codex exec -o <file>` no command string
changes.

| Response                           | Wrapper                                                    | Skill                                     |
| ---------------------------------- | ---------------------------------------------------------- | ----------------------------------------- |
| `2xx`                              | print `content`, exit `0`                                  | normal                                    |
| `401` / `403`                      | exit `3`                                                   | skip; summary says "unauthenticated"      |
| `429`                              | retry twice with backoff inside `--timeout`, then exit `4` | skip; summary says "rate limited"         |
| credits exhausted                  | exit `5`                                                   | skip; summary says "top up Hyper credits" |
| other `4xx` / `5xx` / socket error | one retry on `5xx`, then exit `6`                          | skip; summary quotes the status line      |
| no response by `--timeout`         | exit `7`                                                   | skip; summary says "timed out"            |

Every non-zero exit is noted and never fatal, per SKILL.md:83. Hyper's
published error table (`/docs/llms-full.txt`, read 2026-09-02) gives `402`
`billing_error` for insufficient Hypercredits, `429` `rate_limit_error`, `5xx`
`server_error`, and `401` for a missing or bad key; the wrapper maps exactly
those. The documented error body is a nested object, but the live `401` body
was a flat string, so the wrapper accepts both shapes. Every completion also
returns `usage.cost.hypercredits` and `usage.remaining.hypercredits`, which
the status line carries so the summary shows the balance without a second
call.

### What this reviewer is safe from

Every CLI reviewer in the roster carries a hazard that comes from being a
process with a filesystem, a shell, or a memory. The wrapper has none of the
three, so it inherits none of the hazards:

- **No stale-conversation review.** `agy` keeps a conversation store and, when
  its pointer was wrong, wandered that store for a diff it was never handed
  (`agy.md:18`). The wrapper has no store. Its only input is the bytes on
  stdin.
- **No cwd-relative prompt injection.** `devin` discovers rules from
  `.windsurf/`, `.cursor/`, and `.devin/` relative to its cwd, so it is
  dispatched from a fixed empty `<NEUTRAL>` directory (`devin.md:27`). The
  wrapper reads no directory. It needs no neutral cwd, no `mkdir -p`, no `cd`,
  and no two extra allow rules.
- **No OS-sandbox downgrade.** `devin` lost its `--sandbox` to an org policy
  and now rests on the tool layer alone (`devin.md:19-26`). The wrapper has no
  tools to gate.
- **No 30s auth hang.** There is no OAuth flow; a bad key is a `401`.
- **No `$TMPDIR` desync.** The wrapper reads stdin in the same shell call that
  assembles `<INPUT>`, as `codex` does (SKILL.md:136).

The diff still leaves the machine, as it does for every cloud reviewer. What
the diff can influence is bounded to the review text, which passes through the
reconciler. Contributor-controlled text in the diff can steer the findings; it
cannot steer a tool call, because there is none.

### Repo housekeeping

Each row was checked by reading the named file.

| Item                                               | Change? | Evidence                                                                                                      |
| -------------------------------------------------- | ------- | ------------------------------------------------------------------------------------------------------------- |
| `README.md` "N skills, M commands, K subagents"    | no      | `validate.py:176,204,216,391-403` counts `skills/*/`, `commands/*.md`, `agents/*.md`; a reviewer file is none |
| `README.md` reviewer names in prose                | yes     | lines 24 and 51-53 list `codex, agy, devin, copilot`; unvalidated prose, edit for accuracy                    |
| `commands/task-config.md` capability matrix        | no      | lines 15-27 cover task handlers only; no reviewer column                                                      |
| `skills/doctor` Check 7 / `coreview-rule-drift.py` | no code | `analyze` globs `reviewers/*.md`; a file with no `json` fence is skipped (see Permissions)                    |
| `skills/co-review/SKILL.md`                        | yes     | table at 76-81, built-in list at 70, 72, 142, 297 (five mentions); body is 398 of 500 lines, warn at 450      |
| `scripts/typecheck.sh`                             | yes     | `DEFAULT_FILES` at lines 75-79 is an explicit list; add the wrapper or it is never typechecked                |
| `scripts/build-claude-ai-zips.sh`                  | no      | it bundles `scripts/research-spike.py` only (lines 92-93, 113-114); co-review has no zip that ships scripts   |
| `dev_docs/external-agents.md`                      | maybe   | describes CLI backgrounding; the wrapper's `> <file>` capture is a simpler case, one sentence suffices        |

Every row marked yes is done on this branch. `dev_docs/external-agents.md`
was left alone.

`--reviewer-set cheap-single` (SKILL.md:29-33) stays `codex` then `agy`.
`hyper` costs money per call and the cheap set is for unattended runs where
nobody sees the credit balance drop.

## Rejected alternatives

- **A custom `command:` in `.co-review.yml`.** SKILL.md:142 classifies it as
  untrusted and requires a prompt per run; SKILL.md:183 skips it under
  `--non-interactive` unless `--allow-command` carries the exact string. The
  auto-pilot caller would have to thread a byte-exact command through every
  launch, and the key-sourcing problem stays unsolved.
- **An exact-match rule for the wrapper.** Dies at every release because the
  installed path carries the version (SKILL.md:209), and it would make the
  drift script report the shared rule dead (see Permissions).
- **A `/v1/models` pre-flight.** Proves reachability, not the key.
- **The Anthropic-compatible Messages endpoint.** Hyper serves no Anthropic
  model, so the endpoint buys nothing, and the OpenAI shape is what
  `reasoning_effort` is documented against.
- **Routing a Chinese-lab model through `devin --model`.** `devin.md:30` warns
  against exactly this because Cognition names no serving provider. Hyper is
  the documented serving party, which is the difference. See the first open
  question for what is still unknown.
- **Streaming responses.** Avoids idle-connection timeouts at intermediaries
  but complicates the wrapper. Start non-streaming with a long socket timeout;
  switch if gateway timeouts appear in practice.
- **Adding `hyper` to `cheap-single`.** Metered; see above.
- **Plugin-side gating** — repo-visibility detection, an allow-list of repos,
  or enabling on `HYPER_API_KEY` presence. Ruled out by the user: the plugin
  cannot know which repos may send a diff to a third party, and a key in the
  environment says the user has an account, not that this repo may use it.

## Data handling — recorded, not decided

Resolved as **not the plugin's call**. What was found, so the user can decide
per repo:

- **Hyper FAQ** (fetched 2026-09-02): "full control over the infrastructure
  and stack for inference", multi-region serving across MENA, Europe, North
  America, South America, and Asia, a ZDR policy with its cloud providers,
  and no training on user data.
- **Privacy policy** at <https://hyper.charm.land/privacy> (fetched
  2026-09-02, effective February 2026): prompts and outputs "not stored by
  default; up to 30 days if temporarily retained" for debugging, abuse
  prevention, or legal compliance; requests are routed to "third-party AI
  model providers" selected for no-training terms under contractual
  commitments; "providers may process data in jurisdictions outside your
  location". Operational usage data is kept 90 days.
- **Not found:** which provider or region serves which model family. The FAQ's
  "full control over the infrastructure" and the policy's "third-party
  providers" are both Charm's statements, and they do not obviously describe
  the same arrangement. Neither is audited.

`devin.md:30`'s warning against `glm-*`/`kimi-*`/`deepseek-*` on proprietary
code rested on Cognition naming no serving provider. Hyper names itself as the
contracting party and publishes a retention schedule, which is more than
`devin` offered; that distinction rests on Charm's own claims, not on an audit.
`hyper.md` carries the same summary and points at the policy.

## Open questions

1. **Request field for effort, and where reasoning text returns.** Hyper's
   docs say "all standard parameters are accepted" and name none; the catalog
   exposes `reasoning.effort_levels` but no request field. The wrapper sends
   OpenAI's `reasoning_effort` and prints `message.content` only. Settling it
   needs an authenticated call; no key was in the environment, and the `op`
   read of the "Charm" item was denied by the auto-mode classifier on
   2026-09-02, so it stays open until the user runs one review and reads the
   status line. A rejected field would surface as `http-error http=400`.
2. **Per-call minimum charge.** `usage.cost.hypercredits` on the first real
   review answers it; moot for probing, since `/v1/credits` is free.
3. **Whether reasoning tokens count against `max output`** on the `kimi-*`
   16k models. Decides whether `kimi-k3` can run at high effort on a large
   diff.
4. **Model ranking.** Run the comparison in "How to settle it" before pinning
   for good. The pin in this document is the starting hypothesis.
5. **Does the permission matcher expand `${CLAUDE_PLUGIN_ROOT}` before
   matching the literal cache path in the shared rule?** SKILL.md:98 and :203
   assert it does for the drift pre-flight; this design inherits that claim
   rather than re-verifying it.
