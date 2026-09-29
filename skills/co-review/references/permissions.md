# Permissions (approve once)

One-time human setup, done before the first `/co-review` run that dispatches a
local reviewer — the agent never executes this file. Each reviewer's own
exact-match rules live beside it in [`../reviewers/`](../reviewers/).

The reviewer command is **invariant**: everything that varies per PR (the diff and any reviewer-specific requests) travels in the `<INPUT>` file — reached on stdin with a fixed pointer argument (`codex`, `copilot`, `crush` — the last from a fixed neutral cwd too, see [`reviewers/crush.md`](../reviewers/crush.md)), named by its fixed path inside the `-p` pointer (`agy`), or via `--prompt-file "<INPUT>"` from a fixed neutral cwd (`devin` and `grok` — that cwd is load-bearing, not incidental; see [`reviewers/devin.md`](../reviewers/devin.md) and [`reviewers/grok.md`](../reviewers/grok.md)) — so the command string never changes. Approve each reviewer **once** with an **exact-match** rule — no broad wildcard. Two layers of rules:

**Shared rules** (input assembly, the staleness and tree-drift checks, PR metadata) — add once, they cover every reviewer:

```json
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(gh pr diff:*)",
      "Bash(gh pr view:*)",
      "Bash(git diff:*)",
      "Bash(git ls-remote:*)",
      "Bash(git status:*)"
    ]
  }
}
```

<a id="plugin-scripts"></a>

**The plugin's own scripts need no rule: `SKILL.md` grants them.** Its `allowed-tools` front-matter names each script this skill runs — the pre-flights, `await-pr-review.sh`, `pr-fix-guard.sh`, `coreview-conventions.sh`, the anchor-check, the grok telemetry gate and the drift checker — as `${CLAUDE_PLUGIN_ROOT}/scripts/<name>`. The variable is substituted when the skill loads, so the grant follows the plugin into each new version directory and never goes stale.

A settings rule cannot do this. The matcher compares a prefix rule only up to a whole shell token, and the quoted script path is one token that includes the version (`"…/workflow-skills/2.70.4/scripts/preflight-cwd.sh"`). A rule that stops above the version segment matches nothing, with or without the opening quote; only a rule naming the full path does, and that dies at the next release. Measured with `claude -p --permission-mode default` and one rule at a time: `Bash("…/ws/ws/:*)` and `Bash(…/ws/ws/:*)` were denied, `Bash("…/ws/ws/1.0/scripts/x.sh":*)` was allowed. If your settings still carry a `…/workflow-skills/workflow-skills/:*` rule, delete it; it has never fired, and the drift checker reports it DEAD.

<a id="pr-review-comments-outside-a-skill"></a>

**Calling `pr-review-comments.sh` outside a skill takes a full-path rule, one per release.** An agent that edits review comments with it directly, with no skill granting it, gets no `allowed-tools` grant, so the only settings rule that fires is the full versioned path, for the reason above: `Bash("$HOME/.claude/plugins/cache/workflow-skills/workflow-skills/<version>/scripts/pr-review-comments.sh":*)`. It stops matching at the next release and has to be rewritten then. No `rtk`-prefixed twin is needed: `rtk rewrite` leaves a command that starts with a script path unchanged (it exits 1, meaning no rewrite), so the matcher sees the path as typed.

**The grant applies only when the skill starts from a slash command.** Measured on a probe plugin under `claude -p --permission-mode default`, two runs each: a skill started by its slash command ran both script shapes under the grant; the same skill invoked by the **model**, even with a `Skill(<plugin>:<skill>)` allow rule, loaded but had every script denied, silently. So `claude -p "/co-review --non-interactive"` is covered, and co-review invoked by the model, as `/deliver-task` does for its review step, is not. A `/co-review` typed interactively is expected to behave like the slash-command case, and a model-invoked one interactively to prompt per script; neither was measured. Auto-pilot is unaffected because it runs with `bypassPermissions`. Tracked in #919.

**Per-reviewer rules** — each reviewer's own exact-match rule(s) (the review command, plus the pre-flight probe for `agy`/`devin`, and the segments that prepare and enter a neutral cwd for `devin` and `crush`) live in its file under [`reviewers/`](../reviewers/). Add only the ones for the reviewers you use; copy them verbatim. Merge everything into the `permissions.allow` array in `~/.claude/settings.json` (user-wide) or the repo's `.claude/settings.json` — don't overwrite an existing settings file.

<a id="home-rooted-paths"></a>

**Spell a home-rooted path `$HOME/…`, not `/Users/you/…` — in the rule _and_ in the invocation.** Claude Code's permission matcher compares the two strings **before** either is expanded, so a literal `$HOME` on both sides matches byte-for-byte and the shell expands it at exec time. That makes one rule correct on every machine, which matters for a settings file synced across hosts whose home directories differ: the literal form needs one rule per host, and each host then reports the others' as unusable. The two sides must agree on the _spelling_, not merely on the path they name — `$HOME/co-review-input` in the rule and `/Users/you/co-review-input` in the command are different strings and **do not match**. That mismatch fails in the worst way: under `/co-review --non-interactive` the dispatch is denied rather than queued, so the reviewer simply stops appearing in the run summary with nothing logged. **`~` is not a substitute**, and it fails differently: every path here is double-quoted, and a quoted tilde is never expanded, so `"~/co-review-input"` matches its rule byte-for-byte, is duly approved, and then names a literal `./~/…` directory that does not exist. [`../../../scripts/coreview-rule-drift.py`](../../../scripts/coreview-rule-drift.py) accepts either spelling and expands before checking whether the path resolves here, so a `$HOME` rule is not reported as drift.

Why this is narrow:

- The per-reviewer rules are **exact** — each authorizes only its one read-only review command with that exact prompt/flags, pinning the read-only posture into the approved string (see each reviewer file for the details: `codex --sandbox read-only`; `agy --sandbox --model …`; `devin --permission-mode auto` with a literal `--prompt-file "<INPUT>"` path and a fixed neutral cwd; `copilot --no-ask-user` with **no** `--allow-all-tools`/`--yolo`; `crush --cwd "<NEUTRAL>"`, which points it at the shipped `disabled_tools` config that pins its read-only posture, gated by a `crush --version` match; `grok --cwd "<NEUTRAL>" --sandbox strict --permission-mode auto --tools read_file`, where the sandbox profile confines reads as well as writes and the tool allowlist is the only tool filter that was measured working, gated by a telemetry-pin check and a version match). None grant arbitrary runs of the agent. Edit the pointer, path, or flags and Claude Code re-prompts, so the approval can't silently come to mean something else. The pointer/flags must match **byte-for-byte** between the reviewer file's invocation and its rule — if you edit one, edit the other.
- The `agy`/`devin`/`crush`/`grok` probe rules (`Bash(agy models)`, `Bash(devin auth status)`, `Bash(crush --version)`, `Bash(grok --version)`) are read-only status queries with no varying arguments — exact-match, safe to approve once. `copilot` has no probe rule (no `auth status` command; failures are caught from output). grok's **telemetry-pin gate** needs no rule of its own: it is `scripts/grok-telemetry-gate.py`, one of the [plugin scripts `SKILL.md` grants](#plugin-scripts). It only reads the grok config and the environment, and it is the gate that keeps a reviewed diff out of xAI's trace channel — so if it is denied, grok skips the run.
- `Bash(git ls-remote:*)` covers the staleness pre-flight — it only reads remote ref tips and mutates nothing (no fetch).
- `Bash(git status:*)` covers the step-8 tree-drift snapshot and the step-10 applied-fix evidence. Both are read-only, and both are **mandatory** — step 10 forbids reporting a fix as applied without a `git status --porcelain` behind it. Without this rule that command is denied under `--non-interactive`, silently (a denied command is not queued), so the run reports applied fixes with the one thing that substantiates them missing, in the mode where nobody is watching.
- `Bash(gh pr view:*)` covers the PR-metadata read in step 3 and the `--post` conflict check — both read-only queries against the PR.
- `Bash(cat:*)`, `Bash(gh pr diff:*)`, and `Bash(git diff:*)` cover assembling the input stream — they only **read** repo/PR data (the rubric, the `agent-guidance` plugin's convention files, any requests file, the diff); the sole write is the redirected `<INPUT>` temp file (redirection targets aren't constrained by the rule, and it's written and read in the same shell call). Add only the diff source you use (`gh pr diff` for PRs, `git diff` for `--local`).
- These do **not** cover custom `command:` agents from `.co-review.yml` — those are untrusted by design (see `SKILL.md` → Local reviewers) and must stay prompt-on-every-run. (A skill can self-grant its own fixed commands through `allowed-tools` front-matter, as `SKILL.md` does for the [plugin scripts](#plugin-scripts), but a plugin cannot ship `permissions` entries into your settings — so every rule on this page stays a manual one-time step per user.)
