# Permissions (approve once)

One-time human setup, done before the first `/co-review` run that dispatches a
local reviewer — the agent never executes this file. Each reviewer's own
exact-match rules live beside it in [`../reviewers/`](../reviewers/).

The reviewer command is **invariant**: everything that varies per PR (the diff and any reviewer-specific requests) travels in the `<INPUT>` file — reached on stdin with a fixed pointer argument (`codex`, `copilot`, `crush` — the last from a fixed neutral cwd too, see [`reviewers/crush.md`](../reviewers/crush.md)), named by its fixed path inside the `-p` pointer (`agy`), or via `--prompt-file "<INPUT>"` from a fixed neutral cwd (`devin` and `grok` — that cwd is load-bearing, not incidental; see [`reviewers/devin.md`](../reviewers/devin.md) and [`reviewers/grok.md`](../reviewers/grok.md)) — so the command string never changes. Approve each reviewer **once** with an **exact-match** rule — no broad wildcard. Two layers of rules:

**Shared rules** (input assembly, staleness and conflict pre-flights, allow-rule pre-flight) — add once, they cover every reviewer:

```json
{
  "permissions": {
    "allow": [
      "Bash(cat:*)",
      "Bash(gh pr diff:*)",
      "Bash(gh pr view:*)",
      "Bash(git diff:*)",
      "Bash(git ls-remote:*)",
      "Bash(git status:*)",
      "Bash(<PLUGIN-CACHE>/workflow-skills/workflow-skills/:*)",
      "Bash(python3 <PLUGIN-CACHE>/workflow-skills/workflow-skills/:*)"
    ]
  }
}
```

Replace `<PLUGIN-CACHE>` with your own plugin cache root (`~/.claude/plugins/cache`, spelled as a literal absolute path). **This one is a prefix rule, not an exact match, and that is deliberate** — the installed plugin's path carries its version (`…/workflow-skills/2.13.2/scripts/…`), so an exact rule would die at the next release. A rule that breaks on every update is precisely the drift the allow-rule pre-flight exists to catch, and it would be catching itself. Stopping the prefix above the version segment survives updates; what it widens to is python3 scripts shipped by this plugin, which you already trust by having installed it.

**Per-reviewer rules** — each reviewer's own exact-match rule(s) (the review command, plus the pre-flight probe for `agy`/`devin`, and the segments that prepare and enter a neutral cwd for `devin` and `crush`) live in its file under [`reviewers/`](../reviewers/). Add only the ones for the reviewers you use; copy them verbatim. Merge everything into the `permissions.allow` array in `~/.claude/settings.json` (user-wide) or the repo's `.claude/settings.json` — don't overwrite an existing settings file.

<a id="home-rooted-paths"></a>

**Spell a home-rooted path `$HOME/…`, not `/Users/you/…` — in the rule _and_ in the invocation.** Claude Code's permission matcher compares the two strings **before** either is expanded, so a literal `$HOME` on both sides matches byte-for-byte and the shell expands it at exec time. That makes one rule correct on every machine, which matters for a settings file synced across hosts whose home directories differ: the literal form needs one rule per host, and each host then reports the others' as unusable. The two sides must agree on the _spelling_, not merely on the path they name — `$HOME/co-review-input` in the rule and `/Users/you/co-review-input` in the command are different strings and **do not match**. That mismatch fails in the worst way: under `/co-review --non-interactive` the dispatch is denied rather than queued, so the reviewer simply stops appearing in the run summary with nothing logged. **`~` is not a substitute**, and it fails differently: every path here is double-quoted, and a quoted tilde is never expanded, so `"~/co-review-input"` matches its rule byte-for-byte, is duly approved, and then names a literal `./~/…` directory that does not exist. [`../../../scripts/coreview-rule-drift.py`](../../../scripts/coreview-rule-drift.py) accepts either spelling and expands before checking whether the path resolves here, so a `$HOME` rule is not reported as drift.

Why this is narrow:

- The per-reviewer rules are **exact** — each authorizes only its one read-only review command with that exact prompt/flags, pinning the read-only posture into the approved string (see each reviewer file for the details: `codex --sandbox read-only`; `agy --sandbox --model …`; `devin --permission-mode auto` with a literal `--prompt-file "<INPUT>"` path and a fixed neutral cwd; `copilot --no-ask-user` with **no** `--allow-all-tools`/`--yolo`; `crush --cwd "<NEUTRAL>"`, which points it at the shipped `disabled_tools` config that pins its read-only posture, gated by a `crush --version` match; `grok --cwd "<NEUTRAL>" --sandbox strict --permission-mode auto --tools read_file`, where the sandbox profile confines reads as well as writes and the tool allowlist is the only tool filter that was measured working, gated by a telemetry-pin check and a version match). None grant arbitrary runs of the agent. Edit the pointer, path, or flags and Claude Code re-prompts, so the approval can't silently come to mean something else. The pointer/flags must match **byte-for-byte** between the reviewer file's invocation and its rule — if you edit one, edit the other.
- The `agy`/`devin`/`crush`/`grok` probe rules (`Bash(agy models)`, `Bash(devin auth status)`, `Bash(crush --version)`, `Bash(grok --version)`) are read-only status queries with no varying arguments — exact-match, safe to approve once. `copilot` has no probe rule (no `auth status` command; failures are caught from output). grok's **telemetry-pin gate** needs no rule of its own: it is `scripts/grok-telemetry-gate.py`, already covered by the `python3 <PLUGIN-CACHE>/…` prefix below. It only reads the grok config and the environment, and it is the gate that keeps a reviewed diff out of xAI's trace channel — so if you narrow that prefix, grok skips every run.
- `Bash(git ls-remote:*)` covers the staleness pre-flight — it only reads remote ref tips and mutates nothing (no fetch).
- `Bash(git status:*)` covers the step-8 tree-drift snapshot and the step-10 applied-fix evidence. Both are read-only, and both are **mandatory** — step 10 forbids reporting a fix as applied without a `git status --porcelain` behind it. Without this rule that command is denied under `--non-interactive`, silently (a denied command is not queued), so the run reports applied fixes with the one thing that substantiates them missing, in the mode where nobody is watching.
- The bare `<PLUGIN-CACHE>/…` prefix covers the shell fixtures this skill invokes by path — `preflight-cwd.sh`, `preflight-freshness.sh`, `preflight-conflict.sh`, `await-pr-review.sh`, `pr-fix-guard.sh`, `coreview-conventions.sh`. It is a prefix rather than an exact match for the same reason as the `python3` rule below it: the installed path carries the plugin's version, so an exact rule dies at the next release. What it widens to is shell scripts shipped by this plugin, which you already trust by having installed it. Note that a rule on a script's _inner_ commands does not authorize the script — the matcher sees the invocation, not what it runs — so without this prefix the pre-flights are denied under `--non-interactive`, silently, since a denied command is not queued, and the run reviews a conflicting branch exactly as if the check had passed.
- `Bash(gh pr view:*)` covers the PR-metadata read in step 3 and the `--post` conflict check — both read-only queries against the PR.
- The `python3 <PLUGIN-CACHE>/…` prefix covers the `--post` anchor-check (`scripts/diff-anchor-check.py`, step 12), which only reads (the diff and the candidate comments) and never writes. Without it the anchor-check is denied under `--non-interactive` — silently, since a denied command is not queued — so its split of unanchored comments goes missing and the whole review POST is rejected instead. The allow-rule pre-flight (`scripts/coreview-rule-drift.py`) no longer needs this rule: `SKILL.md` self-grants it in `allowed-tools`. Keeping the prefix does the pre-flight no harm, and the anchor-check still requires it.
- `Bash(cat:*)`, `Bash(gh pr diff:*)`, and `Bash(git diff:*)` cover assembling the input stream — they only **read** repo/PR data (the rubric, the `agent-guidance` plugin's convention files, any requests file, the diff); the sole write is the redirected `<INPUT>` temp file (redirection targets aren't constrained by the rule, and it's written and read in the same shell call). Add only the diff source you use (`gh pr diff` for PRs, `git diff` for `--local`).
- These do **not** cover custom `command:` agents from `.co-review.yml` — those are untrusted by design (see `SKILL.md` → Local reviewers) and must stay prompt-on-every-run. (A skill can self-grant its own fixed commands through `allowed-tools` front-matter, as `SKILL.md` does for the drift-check pre-flight, but a plugin cannot ship `permissions` entries into your settings — so every rule on this page stays a manual one-time step per user.)
