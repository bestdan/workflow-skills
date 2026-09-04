# Permissions (approve once)

One-time human setup, done before the first `/co-review` run that dispatches a
local reviewer — the agent never executes this file. Each reviewer's own
exact-match rules live beside it in [`../reviewers/`](../reviewers/).

The reviewer command is **invariant**: everything that varies per PR (the diff and any reviewer-specific requests) travels in the `<INPUT>` file — reached on stdin with a fixed pointer argument (`codex`, `copilot`, `crush` — the last from a fixed neutral cwd too, see [`reviewers/crush.md`](../reviewers/crush.md)), named by its fixed path inside the `-p` pointer (`agy`), or via `--prompt-file "<INPUT>"` from a fixed neutral cwd (`devin` — that cwd is load-bearing, not incidental; see [`reviewers/devin.md`](../reviewers/devin.md)) — so the command string never changes. Approve each reviewer **once** with an **exact-match** rule — no broad wildcard. Two layers of rules:

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
      "Bash(<PLUGIN-CACHE>/workflow-skills/workflow-skills/:*)",
      "Bash(python3 <PLUGIN-CACHE>/workflow-skills/workflow-skills/:*)"
    ]
  }
}
```

Replace `<PLUGIN-CACHE>` with your own plugin cache root (`~/.claude/plugins/cache`, spelled as a literal absolute path). **This one is a prefix rule, not an exact match, and that is deliberate** — the installed plugin's path carries its version (`…/workflow-skills/2.13.2/scripts/…`), so an exact rule would die at the next release. A rule that breaks on every update is precisely the drift the allow-rule pre-flight exists to catch, and it would be catching itself. Stopping the prefix above the version segment survives updates; what it widens to is python3 scripts shipped by this plugin, which you already trust by having installed it.

**Per-reviewer rules** — each reviewer's own exact-match rule(s) (the review command, plus the pre-flight probe for `agy`/`devin`, and the segments that prepare and enter a neutral cwd for `devin` and `crush`) live in its file under [`reviewers/`](../reviewers/). Add only the ones for the reviewers you use; copy them verbatim. Merge everything into the `permissions.allow` array in `~/.claude/settings.json` (user-wide) or the repo's `.claude/settings.json` — don't overwrite an existing settings file.

Why this is narrow:

- The per-reviewer rules are **exact** — each authorizes only its one read-only review command with that exact prompt/flags, pinning the read-only posture into the approved string (see each reviewer file for the details: `codex --sandbox read-only`; `agy --sandbox --model …`; `devin --permission-mode auto` with a literal `--prompt-file "<INPUT>"` path and a fixed neutral cwd; `copilot --no-ask-user` with **no** `--allow-all-tools`/`--yolo`; `crush --cwd "<NEUTRAL>"`, which is what pins its read-only config). None grant arbitrary runs of the agent. Edit the pointer, path, or flags and Claude Code re-prompts, so the approval can't silently come to mean something else. The pointer/flags must match **byte-for-byte** between the reviewer file's invocation and its rule — if you edit one, edit the other.
- The `agy`/`devin` probe rules (`Bash(agy models)`, `Bash(devin auth status)`) are read-only status queries with no varying arguments — exact-match, safe to approve once. `copilot` has no probe rule (no `auth status` command; failures are caught from output).
- `Bash(git ls-remote:*)` covers the staleness pre-flight — it only reads remote ref tips and mutates nothing (no fetch).
- The bare `<PLUGIN-CACHE>/…` prefix covers the shell fixtures this skill invokes by path — `preflight-freshness.sh`, `preflight-conflict.sh`, `await-pr-review.sh`, `pr-fix-guard.sh`. It is a prefix rather than an exact match for the same reason as the `python3` rule below it: the installed path carries the plugin's version, so an exact rule dies at the next release. What it widens to is shell scripts shipped by this plugin, which you already trust by having installed it. Note that a rule on a script's _inner_ commands does not authorize the script — the matcher sees the invocation, not what it runs — so without this prefix the pre-flights are denied under `--non-interactive`, silently, since a denied command is not queued, and the run reviews a conflicting branch exactly as if the check had passed.
- `Bash(gh pr view:*)` covers the PR-metadata read in step 3 and the `--post` conflict check — both read-only queries against the PR.
- The `python3 <PLUGIN-CACHE>/…` prefix covers the allow-rule pre-flight. That checker only reads the plugin's own reviewer files and your settings, and never writes (see `scripts/coreview-rule-drift.py`). Without this rule the pre-flight is denied under `--non-interactive` — silently, since a denied command is not queued — so the check meant to explain a missing reviewer goes missing itself.
- `Bash(cat:*)`, `Bash(gh pr diff:*)`, and `Bash(git diff:*)` cover assembling the input stream — they only **read** repo/PR data; the sole write is the redirected `<INPUT>` temp file (redirection targets aren't constrained by the rule, and it's written and read in the same shell call). Add only the diff source you use (`gh pr diff` for PRs, `git diff` for `--local`).
- These do **not** cover custom `command:` agents from `.co-review.yml` — those are untrusted by design (see `SKILL.md` → Local reviewers) and must stay prompt-on-every-run. (Plugins can't ship permission rules — only `agent`/`subagentStatusLine` settings — so this is a manual one-time step per user.)
