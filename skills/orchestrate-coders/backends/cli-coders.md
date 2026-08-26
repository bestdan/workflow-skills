# CLI coder backends — codex, agy, devin, custom

Every external coder is driven through the same shell contract; only the
invocation line differs. The orchestrator never trusts a coder's self-report —
the diff and the verification run are the ground truth.

## Shared contract

Per packet:

1. **Workspace.** Create a dedicated worktree and branch:
   `git worktree add <dir> -b orchestrate/<packet-slug> <base>`, where `<base>`
   is the repo's current branch (`git rev-parse --abbrev-ref HEAD`) — not a
   hardcoded `main`; use the integration branch instead when packets are
   dependent. `<dir>` lives under the session scratchpad, never inside the repo.
2. **Spec file.** Write the packet brief (same shape as the opus packet
   prompt: task, scope, constraints, verify command, report format) to
   `<dir>/.packet-spec.md`. Files, not inline prompt text, carry everything
   that varies — it keeps command lines stable and reviewable. **Format the
   spec at write time** — `dprint fmt "<dir>/.packet-spec.md"` (or the
   project's formatter) — so this untracked file doesn't trip the repo-wide
   `dprint check` inside the coder's verification run. Include any
   `sandbox_workarounds` from `.coders.yml` in the spec's constraints so
   sandboxed coders don't rediscover them (see Environmental failures).
   The first paragraph is the initial prompt every CLI coder sees:

   ```md
   You are a delegated implementation agent for this repository. Your workspace
   root is <dir>; edit only files under that directory. Implement the task below,
   run the verification command if your tool permissions allow it, and finish
   with a short report listing changed files, verification status, and any
   environmental failures separately from content failures. End your report with
   exactly one terminal line: `PACKET_COMPLETE: DONE` if you finished the task,
   or `PACKET_COMPLETE: PARTIAL` if you stopped short of it.
   ```
3. **Invoke** the coder with cwd `<dir>` (invocations below). **Capture both
   stdout and stderr, and read only the final message / tail** — coders stream
   full file contents and reasoning (codex stdout can exceed 10K lines /
   ~1 MB; pass `--json` and take the last agent message where supported).
   Do **not** discard stderr: agy delivers its **structured report on
   stderr** while stdout carries only stray paths, so parse the report from
   whichever stream holds it.
4. **Harvest.** The result is `git -C <dir> diff` plus untracked files
   (`git -C <dir> ls-files --others --exclude-standard -- ':!.packet-spec.md'`
   — exclude the spec: it is orchestrator input, not coder output) — not the
   coder's prose. **An empty worktree diff does not immediately mean failure:** first
   run the main-checkout containment check (SKILL.md step 5) — a CLI coder
   (notably agy) can land correct edits in the user's main checkout instead of
   its worktree. Empty diff **and** clean main checkout = failed packet.
   The coder's own report is never the success signal — the diff plus the
   verify command (step 5 below) are — but a report that stops after a
   preamble or a tool result, with no terminal `PACKET_COMPLETE: DONE` /
   `PACKET_COMPLETE: PARTIAL` line (per the initial prompt in step 2), is
   itself a signal the coder cut off mid-task: note it alongside the
   verification result rather than reading silence as a clean finish. An
   explicit `PACKET_COMPLETE: PARTIAL` is that same signal stated outright:
   record the packet as incomplete even if the step-5 verify passes — a green
   check on half the task is exactly what PARTIAL warns about — and route it
   through the content-failure branch of the retry protocol (SKILL.md step 5):
   back to the **same** coder once with the unfinished remainder appended to
   the spec.
5. **Verify** in `<dir>` per the packet spec, then **commit the packet's
   changes to its branch** — `git -C <dir> add -A -- ':!.packet-spec.md'` and
   commit — so the branch actually carries the work that SKILL.md step 6
   merges. Then hand the branch back to the orchestrator's integrate step.
   After integration, remove the worktree with `git worktree remove --force
   <dir>` (plain `remove` refuses if the packet's verify step populated any
   submodules; `--force` discards anything uncommitted, gitignored files and
   submodule stashes included, so commit everything the branch needs first) —
   not a manual `rm`, which leaves stale `.git/worktrees` metadata.

## Known invocations

**codex** — stateless, local, purpose-built for this
(`codex exec` is its non-interactive mode). Use stdin with `-`; current Codex
CLI versions document `--full-auto` as deprecated in favor of
`--sandbox workspace-write` plus the configured approval policy:

```sh
[ -s "<dir>/.packet-spec.md" ] && codex exec --cd "<dir>" --sandbox workspace-write - < "<dir>/.packet-spec.md"
```

Workspace-write autonomy is safe **only because** the cwd is a throwaway
worktree. Add `--json` if you need structured events; the final message lands
on stdout either way. If an older Codex build lacks `--sandbox workspace-write`,
use that version's equivalent workspace-write/full-auto flag, but keep the
same non-empty spec-file guard and worktree boundary.

**agy** (Google Antigravity CLI) — agentic, **stateful and memory-backed**,
needs an Antigravity login and network (run unsandboxed). Three hard rules,
learned in co-review:

- Fresh conversation per packet — never `--continue`/`--conversation`; a
  packet must depend only on its spec.
- Guard against empty input: on empty stdin agy silently resumes a **prior
  conversation** and works on stale code with full confidence. Always
  `[ -s "<dir>/.packet-spec.md" ] && cat "<dir>/.packet-spec.md" | agy ...`.
- Pin `--model` (default `"Gemini 3.6 Flash (High)"`; the `agy:<model>` suffix
  overrides) so the coder identity doesn't drift between runs.

```sh
[ -s "<dir>/.packet-spec.md" ] && cat "<dir>/.packet-spec.md" | agy -p "Your workspace root is <dir>. Every file you touch must be under it — do not edit any path outside it. Implement the task specified on stdin inside this directory only. If stdin is empty, output exactly NO INPUT and stop." --model "Gemini 3.6 Flash (High)"
```

Run it with cwd `<dir>`. Do **not** pass `--sandbox` here (unlike co-review's
read-only reviewer role, a coder must write). **cwd alone does not contain
agy** — in the pilot it edited the user's main checkout despite a correct
`cd`. Declare the workspace root explicitly in both the prompt (above) and the
spec's constraints, and rely on the mandatory main-checkout check (SKILL.md
step 5) as the backstop. (Open: whether agy exposes a workspace/root flag that
would enforce this — prompt-level declaration is the current lever.)

**devin** — driven as a **local CLI** (the installed 2026.x edits files
directly in the worktree cwd; the older cloud-session model below is a
fallback). Canonical invocation, cwd `<dir>`:

```sh
[ -s "<dir>/.packet-spec.md" ] && devin -p --prompt-file "<dir>/.packet-spec.md" --permission-mode accept-edits --model swe-1.6
```

Use `--prompt-file`, **not** piped stdin — `devin -p` with piped stdin panics.
Under `accept-edits`, devin makes edits but **cannot run the verify command**
(permission restrictions), so **devin packets always return unverified** — the
orchestrator's check run (SKILL.md step 5) is the verification, not optional.
(Open: whether a permission mode grants worktree verify access.)

**Always pin `--model` explicitly on devin.** As of 2026-07 the CLI exposes a
**model marketplace** — 37 families across Anthropic, OpenAI, Google, xAI,
Moonshot, Zhipu, DeepSeek, NVIDIA, and Thinking Machines — so the model suffix
selects a _vendor and jurisdiction_, not merely a capability tier. Two traps
that follow from that:

- **The bare `swe` alias now resolves to `swe-1.7-lightning`, which is billed
  ($2.50/$12.50 per Mtok), not to a free tier.** Relying on the alias silently
  moves a "cheap" packet onto a paid model. `swe-1.6` (pinned above) and
  `swe-1.7` are the free ones on Pro; `swe-1.7` is newer but **beta** and
  carries no published absolute benchmark, which is why the pin stays at
  `swe-1.6` — changing it would also re-prompt the exact-match approval.
- **Routing a frontier model through devin is the wrong call.** Devin passes
  through `claude-opus-5`, `gpt-5.6-*`, and `gemini-3.6-flash` at roughly
  native list price, so there is no economic argument for it — it just inserts
  an undocumented intermediary in front of a vendor you can reach directly.
  Use the native backend.

Which passthroughs are worth reaching for, and which the secret-exposure gate
removes, is in [`select-coder/matrix.md`](../../select-coder/matrix.md) → devin.

Fallback — cloud session: if only a remote/API surface is installed, create a
session from the packet spec against the repo, poll to completion, and fetch
the branch/PR it produced as the harvested diff. Prefer an explicit `command:`
in `.coders.yml` for that mode; if neither a `devin` CLI nor a configured
command exists, report it unavailable rather than guessing an API call.

**Custom `command:`** — from `.coders.yml`, with `{SPEC}` and `{WORKTREE}`
placeholders substituted. Untrusted: show the exact command and confirm
before the first run (see SKILL.md Safety).

## Environmental failures

Some verification failures are the coder's **sandbox**, not its edits, and must
not be treated as content failures (no retry — the orchestrator's re-run
outside the sandbox is authoritative, per SKILL.md step 5):

- **Home-dir cache/permission errors.** Inside `codex exec --full-auto`,
  `dprint check` fails writing `~/Library/Caches/dprint` and `uv run` fails on
  `~/.cache/uv` ("Operation not permitted"), so `check.sh` reports FAIL on
  correct edits. Expect this signature from codex; the orchestrator re-runs the
  check in the worktree outside the sandbox.
- The packet-spec boilerplate should tell CLI coders to **classify home-dir
  cache/permission errors as environmental** — report them separately, do not
  mark the task failed.
- Ship known workarounds in the spec so coders don't burn turns rediscovering
  them (codex spent multiple turns and a dead-end `DPRINT_CACHE_DIR` attempt
  finding `dprint fmt --incremental=false`). Carry project-specific values in a
  `sandbox_workarounds` block in `.coders.yml` and inject them into the spec
  constraints (step 2).

## Retry protocol

Two distinct failure classes (see SKILL.md step 5):

- **Content failure** — the edits are wrong per the verify command run outside
  the sandbox. Append the failure output under a `## Previous attempt failed`
  heading in the spec file and re-invoke the **same** coder once — stateless
  coders (codex) get the full context back this way, and stateful ones don't
  need session resumption to use it. Second failure parks the packet.
- **Environmental failure / empty diff / error / timeout** — re-dispatch to a
  **different** coder once (SKILL.md Safety), never the same one.
