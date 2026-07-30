# Calling external CLI agents (codex, agy, …) from a workflow

_A reliable pattern for invoking a second AI agent — `codex exec`, `agy`, or any
CLI reviewer — from inside a Claude Code workflow. Written after a PRE-83 design
review where a foreground `codex exec` call hung to the Bash tool's 7-minute
ceiling and lost its output because everything was routed through `$TMPDIR`._

---

## The failure modes this avoids

Three things went wrong invoking `codex exec` from a workflow, and each has a
one-line fix:

1. **Foreground timeout.** A long `codex exec` review ran past the Bash tool's
   ~7-minute foreground ceiling and was killed with **no captured output** — the
   whole run was wasted. → **Run long agents in the background.**
2. **Unstable temp paths.** The prompt file, the `-o` output file, and the log
   were all written under `$TMPDIR`, which is **not stable across tool
   invocations or turns** (and resolves differently across the sandbox
   boundary — see the co-review skill's "Why a single shell call" note). The
   files vanished before they could be read, forcing a re-run. → **Write every
   file to the session scratchpad, never `$TMPDIR`/`/tmp`.**
3. **Silent empty prompt.** The prompt was passed inline as
   `"$(cat "$TMPDIR/prompt.txt")"`. When `$TMPDIR` was stale the substitution
   expanded to an empty string and codex was handed an **empty prompt** with no
   error. → **Never inline `$(cat …)` off an unstable path: write the prompt to
   a stable scratchpad file, verify it's non-empty (`[ -s … ]`), then feed it via
   stdin (preferred) or an inline `$(cat …)` from that guarded file.**

## The pattern

Use the **session scratchpad** for the prompt, the output, and the log. The
harness provides a stable per-session scratchpad path (`…/scratchpad`); anchor
everything there. Below, `SP` stands for that directory.

```bash
# 1. Write the prompt to a stable file (scratchpad, NOT $TMPDIR).
#    Assemble it however you like — here-doc, cat of rubric + diff, etc.
cat rubric.md diff.txt > "$SP/codex-prompt.txt"

# 2. Guard against an empty prompt BEFORE spending an agent run on it.
[ -s "$SP/codex-prompt.txt" ] || { echo "EMPTY PROMPT — aborting"; exit 1; }

# 3. Dispatch with machine-readable capture (-o file, or --json) and a log.
#    Pass the prompt from the guarded, stable file (step 2) — never off an
#    unstable temp path (see the note below on the positional-arg cat).
codex exec --sandbox read-only -o "$SP/codex-out.md" \
  "$(cat "$SP/codex-prompt.txt")" > "$SP/codex.log" 2>&1

# 4. Make an empty / failed / incomplete result detectable: capture exit code,
#    byte count, and the output's last line, and append them to the log so a
#    poller detects completion in one place.
exit_code=$?
bytes=$(wc -c < "$SP/codex-out.md" 2>/dev/null || echo 0)
last=$(tail -n 1 "$SP/codex-out.md" 2>/dev/null)
echo "exit=$exit_code bytes=$bytes last=$last" >> "$SP/codex.log"
```

Run step 3 with the Bash tool's **`run_in_background: true`** for any review that
could take more than a couple of minutes, then poll the log/output for
completion rather than blocking a foreground call to the 7-minute ceiling.

> Step 3 still shows `"$(cat …)"` because some codex versions take the prompt as
> a positional argument, not on stdin. The rule is narrower than "never use
> `cat`": **never inline a prompt straight off an unstable path.** The `[ -s … ]`
> guard in step 2 is what makes this safe — by the time the substitution runs,
> the file is confirmed non-empty and on a stable path. If your codex version
> reads the prompt on **stdin** (`cat "$SP/codex-prompt.txt" | codex exec …`),
> prefer that: it sidesteps the substitution entirely.

### Checklist

- [ ] **Background long runs.** `run_in_background: true` for anything that might
      exceed ~2 min; the Bash foreground ceiling is ~7 min and a hit there loses
      all output.
- [ ] **Scratchpad, never `$TMPDIR`.** Prompt, `-o` output, and log all live in
      the session scratchpad — a stable path that survives across turns.
- [ ] **Machine-readable capture.** Prefer `codex exec -o <file>` (or `--json`)
      over scraping stdout, so the result is a file you can re-read next turn.
- [ ] **Detect empty _and_ incomplete results.** Always echo the **exit code**
      and the output **byte count** — a 0-byte file or non-zero exit is how you
      catch a silent failure instead of trusting a blank review. Byte count
      alone does not prove the agent finished: an agent that exits 0 after a
      preamble or a tool result produces non-empty output that reads as a clean
      run. Where the prompt specifies a terminal verdict line (co-review's
      rubric requires `REVIEW_COMPLETE: PASS` / `REVIEW_COMPLETE: FINDINGS`),
      echo `tail -n 1` of the output too and check for it — missing means
      **incomplete, not PASS**.
- [ ] **Guard the prompt.** `[ -s "$SP/codex-prompt.txt" ]` before dispatch; never
      inline `$(cat "$TMPDIR/…")`.
- [ ] **Run unsandboxed — but only the _Bash_ sandbox.** `codex` (and `agy`) need
      **network**, so dispatch must run with the **Bash/tool** sandbox disabled.
      That is _not_ the same as the agent's own `--sandbox read-only` flag —
      **keep that on** so the external agent can't edit files. Request the sandbox
      escape on the **first** call; a network-blocked failure (`could not resolve
      host`, `connection refused`, socket `operation not permitted`) is the Bash
      sandbox, not a real error — don't retry inside it.

## Sandbox note

`codex exec` and `agy` reach a backend over the network, so their dispatch step
**cannot run inside a restrictive Bash sandbox** — run it with the **Bash/tool**
sandbox disabled. Disabling the Bash sandbox is orthogonal to the agent's own
read-only mode: keep `codex exec --sandbox read-only` (and `agy --sandbox`) so the
external agent stays a pure reviewer that can't write files. `agy` additionally
needs an Antigravity login. Treat a network-blocked failure as "run this with the
Bash sandbox off," not as a broken command. (This mirrors the "Sandbox" guidance
in the user's global `AGENTS.md`: `codex`/`agy` are network tools and belong on
the unsandboxed list.)

## Where this is already applied

The **co-review** skill (`skills/co-review/SKILL.md`) invokes `codex` and `agy`
as extra reviewers and already hardens the parts it needs: a single-shell
assemble-and-dispatch call, a fixed **absolute** `<INPUT>` path (never
`$TMPDIR`), truncate-with-`>` to kill leftover bytes, and a `[ -s "<INPUT>" ]`
byte guard before piping to `agy`. What it does **not** yet do is background a
long review or capture via `-o <file>` — it captures stdout from a foreground
call. If you extend co-review for longer reviews, apply the backgrounding +
`-o`/exit-code-echo pattern above (note that adding `-o <file>` changes the
command string, so its exact-match permission rule must be updated in lockstep).

## Open question: a `run-codex` wrapper?

The PRE-83 task asked whether this should become a reusable skill/wrapper that
standardizes the scratchpad paths, backgrounding, and timeout rather than living
as documentation. For now it's documentation — the pattern is short and the only
in-repo consumer (co-review) has bespoke, permission-sensitive invocation needs
that a generic wrapper would fight. Revisit if a second workflow starts calling
external agents and would benefit from a shared helper.
