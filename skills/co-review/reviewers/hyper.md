# co-review reviewer — `hyper` (Charm Hyper inference gateway)

`hyper` is a built-in reviewer that is **not a CLI**. It is an HTTP call to
[Charm Hyper](https://hyper.charm.land) made by the plugin's own
`scripts/hyper-review.py`, which reads the assembled input on stdin, sends one
chat completion, and prints the reply verbatim. Like `codex` it is **stateless
and read-only** — no filesystem, no shell, no conversation memory — so it needs
**no pre-flight probe, no empty-input guard, and no neutral cwd.** Its voice is
a Chinese-lab or open-weights model, which is the point: every other reviewer
(`codex`, `agy`, `copilot`, and Claude itself) descends from a Western
frontier-lab pipeline, and a reviewer from a different lineage has a different
false-negative set. The design and its evidence are in
[`dev_docs/designs/2026-09-02-hyper-co-reviewer-design.md`](../../../dev_docs/designs/2026-09-02-hyper-co-reviewer-design.md).

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` placeholders, per-agent paths — before using the invocation below.

## Never auto-enabled

**`hyper` runs only when `.co-review.yml` names it.** Every other built-in is
auto-run when the config is absent and its binary is on `PATH`
(SKILL.md → Non-interactive mode, step 4). `hyper` has no binary, and the only
ambient signal — an exported `HYPER_API_KEY` — would switch it on in every repo
where the key happens to be set. Whether a diff may leave for Hyper is the
user's decision per repo, and the local, git-ignored config is where that
decision lives. So: config absent → `hyper` does not run; `local_reviewers`
lists `hyper` → it runs without the untrusted-`command:` prompt, like any
built-in. Never add it on the strength of the key alone.

## Data handling — decide per repo

The diff leaves the machine, as with every cloud reviewer. What Hyper says
about it (read 2026-09-02; claims, not audited): the FAQ says Hyper does not
train on user data, keeps a zero-data-retention policy with its cloud
providers, and serves multi-region across MENA, Europe, North and South
America, and Asia. The [privacy policy](https://hyper.charm.land/privacy)
(effective February 2026) says prompts and outputs are "not stored by default;
up to 30 days if temporarily retained", that requests are routed to
**third-party model providers** under contractual no-training terms, and that
"providers may process data in jurisdictions outside your location". The FAQ's
"full control over the infrastructure" and the policy's "third-party
providers" are Charm's two statements; which one applies to a given model
family is not documented. Read the policy and decide for the repo. On a
public repo none of this matters.

## Model pin — a hypothesis, not a measurement

The invocation pins **`deepseek-v4-pro` at `xhigh` effort**: 1M context takes
any diff without truncation, effort is controllable, and a 20k-token diff plus
a 2k-token review costs about $0.06 (about one Hypercredit). Fallback
`kimi-k2.7-code` (262k context, half the input price, no effort levels);
upgrade `kimi-k3` (1M context, effort `low`/`high`/`max`, about $0.10 per
review). None of the three has been measured on this task; several postdate
the author's knowledge. Settle the ranking by running two on the same PRs
through one reconciler pass and counting the high/medium findings no other
reviewer raised — that count is what this reviewer is bought for.

Override the model with the config object form `- {name: hyper, model: <id>}`
and, if the model lists effort levels in `/v1/models`, `effort: <level>`.
Unlike the CLI reviewers a changed model does **not** re-prompt: the shared
prefix rule covers every argv (see Permissions). Pass `--effort` only for
models whose catalog entry has `reasoning.effort_levels`; the request field
sent is OpenAI's `reasoning_effort`, which Hyper's docs cover only as "all
standard parameters are accepted" — unverified by a live call as of
2026-09-02.

## Credential

`HYPER_API_KEY` (prefixed `sk-hyper-`) from the environment, else
`HYPER_API_KEY_REF` resolved by `HYPER_API_KEY_RESOLVER` (default `op`)
through the shared contract in
[`dev_docs/auth_key_access.md`](../../../dev_docs/auth_key_access.md). The
script resolves it in-process, so the key never appears in argv, in the
transcript, or in any output. `op` needs an unsandboxed call — the dispatch
already runs unsandboxed for network — and a live session; a lapsed session
maps to exit `3` (`resolver-failed category=…`), so run `op signin` and retry.
Unattended runs need `$OP_SERVICE_ACCOUNT_TOKEN` or `$HYPER_API_KEY` injected
directly. To check a key by hand without spending tokens:
`curl -H "Authorization: Bearer $HYPER_API_KEY" https://hyper.charm.land/v1/credits`
(`/v1/models` is unauthenticated and proves nothing about the key).

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hyper-review.py" --model deepseek-v4-pro --effort xhigh`
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> >> "<INPUT>"; cat "<INPUT>" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/hyper-review.py" --model deepseek-v4-pro --effort xhigh`
- **`--local` mode** → swap `gh pr diff <n>` for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

There is no `<POINTER>` argument: the script embeds the shared pointer as the
system message. The script needs network, so the line runs **unsandboxed** in
the Bash tool, like the cloud reviewers. Under `--non-interactive`, background
the call and capture stdout with `> <file>` — redirection is transparent to
the permission matcher, so no command string changes. The script's own
`--timeout` (default 600s) sits inside the skill's 15-min CLI bound, so a slow
model reports `timeout` itself instead of being killed with nothing captured.

## Output and the completion sentinel

stdout is the model's text, verbatim. The script **never appends**
`REVIEW_COMPLETE:` — a synthesized `PASS` is exactly the inference SKILL.md
step 5 forbids. Findings without a verdict line go to the reconciler with a
note, per that step. A response cut at `--max-tokens` (default 8192) still
exits `0`; the status line says `finish_reason=length`, so treat it as
findings-without-verdict. The `kimi-*` models cap output at 16k tokens, enough
for a findings list; whether reasoning tokens count against that cap is not
verified.

stderr is exactly one line per run, `HYPER_REVIEW: <state> model=… finish_reason=… in=… out=… cost=… remaining=…`
(`cost`/`remaining` are Hypercredits from the response's `usage` block). Quote
it in the run summary beside the reviewer.

| Exit | State                             | Cause                                          | Skill                                      |
| ---- | --------------------------------- | ---------------------------------------------- | ------------------------------------------ |
| `0`  | `ok`                              | a response arrived (check `finish_reason`)     | normal                                     |
| `2`  | `no-credential`                   | `HYPER_API_KEY` and `HYPER_API_KEY_REF` unset  | skip; summary says "no Hyper credential"   |
| `3`  | `auth-rejected`/`resolver-failed` | HTTP `401`/`403`, or `op` could not resolve    | skip; summary says "unauthenticated"       |
| `4`  | `rate-limited`                    | HTTP `429` after two retries                   | skip; summary says "rate limited"          |
| `5`  | `credits-exhausted`               | HTTP `402` (`billing_error`)                   | skip; summary says "top up Hypercredits"   |
| `6`  | `http-error`/`network-error`      | other `4xx`/`5xx` (one retry on `5xx`), socket | skip; summary quotes the status line       |
| `7`  | `timeout`                         | no response by `--timeout`                     | skip; summary says "timed out"             |
| `8`  | `empty-input`                     | nothing on stdin                               | skip; assembly failed — check `gh pr diff` |

Every non-zero exit is noted and never fatal. The status codes come from
Hyper's published error table (`/docs/llms-full.txt`, read 2026-09-02); the
live `401` body was `{"error":"missing authorization"}`, a flat string where
the docs show a nested object, and the script accepts both.

## Permissions

`hyper` ships **no** rule of its own. The dispatch is a python3 script under
the installed plugin, which the shared prefix rule
`Bash(python3 <PLUGIN-CACHE>/workflow-skills/workflow-skills/:*)` in
SKILL.md → Permissions already approves — the same rule that approves the
allow-rule pre-flight. An exact rule would die at every release because the
installed path carries the version, and the exact-match discipline exists to
pin a read-only posture into an agentic CLI's flags; this script has no flag
that widens what it can do. A `--model` change alters cost, not capability.

Do not add a `json`-fenced rule to this file: `scripts/coreview-rule-drift.py`
attributes rules by command word, `python3` is not in its generic set, and a
`Bash(python3 …` template would make it report the shared prefix rule as DEAD
on every machine. The drift check therefore does not cover `hyper`; the
shared rule's absence already surfaces as the pre-flight itself being denied.
