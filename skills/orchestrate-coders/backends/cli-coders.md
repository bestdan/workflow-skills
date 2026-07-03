# CLI coder backends — codex, agy, devin, custom

Every external coder is driven through the same shell contract; only the
invocation line differs. The orchestrator never trusts a coder's self-report —
the diff and the verification run are the ground truth.

## Shared contract

Per packet:

1. **Workspace.** Create a dedicated worktree and branch:
   `git worktree add <dir> -b bestdan/<packet-slug> main` (base on the
   integration branch when packets are dependent). `<dir>` lives under the
   session scratchpad, never inside the repo.
2. **Spec file.** Write the packet brief (same shape as the opus packet
   prompt: task, scope, constraints, verify command, report format) to
   `<dir>/.packet-spec.md`. Files, not inline prompt text, carry everything
   that varies — it keeps command lines stable and reviewable.
3. **Invoke** the coder with cwd `<dir>` (invocations below). Capture stdout;
   stream/ignore stderr progress.
4. **Harvest.** The result is `git -C <dir> diff` plus untracked files
   (`git -C <dir> ls-files --others --exclude-standard`) — not the coder's
   prose. Empty diff = failed packet.
5. **Verify** in `<dir>` per the packet spec, then hand the branch back to the
   orchestrator's integrate step. Remove the worktree after integration.

## Known invocations

**codex** — stateless, local, purpose-built for this
(`codex exec` is its non-interactive mode):

```sh
cat "<dir>/.packet-spec.md" | codex exec --cd "<dir>" --full-auto "Implement the task specified on stdin. Work only inside the current directory. Run the verification command in the spec before finishing."
```

`--full-auto` (or your codex version's equivalent workspace-write sandbox
flag) is safe **only because** the cwd is a throwaway worktree. Add `--json`
if you need structured events; the final message lands on stdout either way.

**agy** (Google Antigravity CLI) — agentic, **stateful and memory-backed**,
needs an Antigravity login and network (run unsandboxed). Three hard rules,
learned in co-review:

- Fresh conversation per packet — never `--continue`/`--conversation`; a
  packet must depend only on its spec.
- Guard against empty input: on empty stdin agy silently resumes a **prior
  conversation** and works on stale code with full confidence. Always
  `[ -s "<dir>/.packet-spec.md" ] && cat "<dir>/.packet-spec.md" | agy ...`.
- Pin `--model` (default `"Gemini 3.5 Flash (High)"`; the `agy:<model>` suffix
  overrides) so the coder identity doesn't drift between runs.

```sh
[ -s "<dir>/.packet-spec.md" ] && cat "<dir>/.packet-spec.md" | agy --model "Gemini 3.5 Flash (High)" -p "Implement the task specified on stdin inside the current directory only. If stdin is empty, output exactly NO INPUT and stop."
```

Run it with cwd `<dir>`. Do **not** pass `--sandbox` here (unlike co-review's
read-only reviewer role, a coder must write) — the worktree is the containment.

**devin** — a cloud service, not a local editor: it works in its own remote
workspace and delivers results as a session, typically ending in a PR or a
patch. Drive it through whatever surface is installed (`command -v devin` for
a CLI; otherwise its API) — create a session from the packet spec against the
repo, poll until it completes, then fetch the branch/PR it produced and treat
that as the harvested diff. Because invocation details vary by installation,
prefer an explicit `command:` in `.coders.yml` for devin; if none is
configured and no `devin` CLI is on PATH, report it unavailable rather than
guessing an API call.

**Custom `command:`** — from `.coders.yml`, with `{SPEC}` and `{WORKTREE}`
placeholders substituted. Untrusted: show the exact command and confirm
before the first run (see SKILL.md Safety).

## Retry protocol

On verification failure, append the failure output under a `## Previous
attempt failed` heading in the spec file and re-invoke the same coder once —
stateless coders (codex) get the full context back this way, and stateful ones
don't need session resumption to use it. Second failure parks the packet.
