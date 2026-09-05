# co-review reviewer — `crush` (Charm Crush CLI)

`crush` is a built-in default reviewer. It is a **provider-agnostic** terminal agent: the binary is Charm's, the model is whatever you point it at. This invocation pins **`hyper/kimi-k2.7-code`** — Kimi K2.7 served by [Charm Hyper](https://hyper.charm.land/) — so its voice is an **open-weight** one, distinct from Claude (the main agent), OpenAI (`codex`), and GitHub-routed (`copilot`). That is the whole reason it earns a slot: it is the only reviewer in the pool whose model shares no training lineage with another.

Read the shared dispatch contract in [`../SKILL.md`](../SKILL.md) — single-shell-call, the `<INPUT>` / `<REQUESTS>` / `<POINTER>` placeholders, per-agent paths — before using the invocation below.

If `crush` errors or isn't runnable, note it and skip — a missing reviewer is never fatal.

## The read-only posture is a config file, not a flag — and it is the whole safety story

Every other reviewer pins read-only inside the approved command string (`codex --sandbox read-only`, `agy --sandbox`, `devin --permission-mode auto`, `copilot --no-ask-user`). **`crush run` has no such flag.** It has `--yolo` and nothing on the other side. Two findings follow, both verified against **crush v0.91.0**, and together they decide the shape of the invocation.

**1. `permissions.allowed_tools` does not constrain `crush run`.** That key means "tools that don't require permission prompts" — and headless mode has no prompts to skip, so it accepts every tool call. Verified: with a global config allowing only `view`, `ls`, `grep`, `glob`, and `sourcegraph`, a `crush run` asked to create a file **created it**. Read a config with a tight `allowed_tools` as no protection at all here.

The mechanism that does hold is **`options.disabled_tools`**, which removes a tool from the agent's roster entirely. Verified with the same prompt against [`assets/crush-readonly.json`](assets/crush-readonly.json): no file appeared. Note what the model said while failing — `DONE`. It reported success it had not achieved, which is the reason the denylist is the control and the rubric's "do not modify files" line is not.

**2. The cwd's `crush.json` wins over every other config.** Crush loads, last-wins: `/etc/crush/crush.json`, `~/.config/crush/crush.json`, `~/.config/crush/crushrc`, `~/.local/share/crush/crush.json`, then **`<cwd>/crush.json`**. Verified by planting invalid JSON in a working directory and reading the loader's own error, which names all five paths in that order. Since co-review runs in repos you don't control, a repo could ship a `crush.json` that re-enables `bash` and `write` — and under `--post` that repo belongs to someone else.

So the dispatch pins **`--cwd "<NEUTRAL>"`**, a dedicated empty directory holding our own config, and copies [`assets/crush-readonly.json`](assets/crush-readonly.json) into it as `crush.json` on every run. This is devin's neutral-cwd move (see [`devin.md`](devin.md)) doing double duty: it takes the reviewed repo's config off the discovery path **and** its `CRUSH.md`/context files with it. Copying on every dispatch rather than once at setup makes the posture self-healing — an edited or deleted config is restored before the model starts. `<NEUTRAL>` must be **crush's own** directory — never the repo, `$HOME`, the `<INPUT>` directory (which holds other reviewers' assembled diffs), **or devin's neutral cwd**. devin requires an _empty_ one and crush writes a `crush.json` into its own, so the two cannot share a path; they share a rule _shape_, not a directory. The placeholder keeps the `<NEUTRAL>` spelling because that is the token [`../../../scripts/coreview-rule-drift.py`](../../../scripts/coreview-rule-drift.py) recognises and exempts from its off-machine check — the checker reads each reviewer's rules separately, so two reviewers may substitute different literal paths into the same placeholder.

> **`disabled_tools` is a denylist, so a crush upgrade opens it.** A release that adds a tool ships it **enabled**, because the asset cannot name it. Re-check the roster after upgrading: ask a throwaway `crush run` to list its tools, or read the tool names in `https://charm.land/crush.json`. The list the asset was built against (26 tools, v0.91.0) is `agent`, `agentic_fetch`, `bash`, `crush_info`, `crush_logs`, `download`, `edit`, `fetch`, `glob`, `grep`, `job_kill`, `job_output`, `ls`, `lsp_*` (8), `multiedit`, `sourcegraph`, `todos`, `view`, `write`. The asset disables all of them — the review arrives on stdin, so the reviewer needs no tool at all.

## Driving rules

- **Pre-flight probe — a version gate, not an auth one.** Run bare `crush --version` and compare its output to the pinned **`crush version v0.91.0`** byte-for-byte. On a mismatch, **skip crush** (noted in the run summary, never fatal) with the reason: `crush <version> is not validated — re-check the tool roster against assets/crush-readonly.json, then bump the pin in reviewers/crush.md`. This exists because `disabled_tools` is a denylist (see the blockquote above): every other reviewer pins its read-only posture inside the approved command string, while crush's depends on an asset that names one release's tools, so an upgrade is the expected way the posture fails open — and it fails open silently, in an unsandboxed process, under `--non-interactive` where nobody reads a prose warning. Failing closed costs one skipped reviewer, which this system already treats as routine. The probe is ~1ms and exits 0; it is not an auth check, and crush needs none — a misconfigured model or a missing credential errors in ~0.8s with an `ERROR` block and a non-zero exit, so that class of failure gets the `copilot` treatment and is caught from the dispatch output.
- **Runs unsandboxed**, for two reasons rather than one. It needs network for the Hyper API, and it needs to write `~/.local/share/crush/` — a sandboxed run dies on `open …/crush.json.lock: operation not permitted` before reaching the model.
- **Stateless per dispatch.** `crush run` opens a fresh session unless given `-s`/`--session` or `-C`/`--continue`. **Never** add either — every review is a fresh session, and a resumed one would review a stale prior conversation.
- **The model pin is `-m "hyper/kimi-k2.7-code"`** — `$1.03/$4.36` per 1M tokens, 262K context, and it accepts the `provider/model` form to disambiguate. Hyper gives every account 100 hypercredits (5¢ each) a month, so routine reviews run inside the free tier: a measured 8.4KB rubric-plus-diff returned findings in **5.3s**. Cheaper Hyper models exist (`qwen3.8-flash` at `$0.15/$0.47`, `deepseek-v4-flash` at `$0.20/$0.40`); a different provider works too, if you have its key configured. Changing the pin changes the command string, so update the exact-match rule below in lockstep.
- **An account with no Hyper credentials skips, it doesn't fail the run.** The dispatch errors, the run summary notes it, co-review continues — same as any missing reviewer.

## Invocation (assemble + dispatch in one shell call)

- **GitHub mode, with requests** → `mkdir -p "<NEUTRAL>" && cat "<this skill dir>/reviewers/assets/crush-readonly.json" > "<NEUTRAL>/crush.json" && cat "<this skill dir>/review_prompt.md" "<REQUESTS>" > "<INPUT>"; gh pr diff <n> --repo <owner>/<name> >> "<INPUT>"; cat "<INPUT>" | crush run --cwd "<NEUTRAL>" -q -m "hyper/kimi-k2.7-code" "<POINTER>" 2>&1`
- **GitHub mode, no requests** → drop the `"<REQUESTS>"` argument: `mkdir -p "<NEUTRAL>" && cat "<this skill dir>/reviewers/assets/crush-readonly.json" > "<NEUTRAL>/crush.json" && cat "<this skill dir>/review_prompt.md" > "<INPUT>"; gh pr diff <n> --repo <owner>/<name> >> "<INPUT>"; cat "<INPUT>" | crush run --cwd "<NEUTRAL>" -q -m "hyper/kimi-k2.7-code" "<POINTER>" 2>&1`
- **`--local` mode** → swap the `gh pr diff …` segment for `git diff <base>` and append any untracked files you read, per the shared `--local` rule in SKILL.md.

`<owner>/<name>` is the repo resolved in SKILL.md step 2 — never `cwd`'s by default, since a `--post` review commonly targets a PR that isn't checked out here.

`crush` reads `<INPUT>` from the `cat "<INPUT>" |` pipe, so the path stays out of the command string. `-q` hides the spinner, which otherwise writes progress frames into the captured stdout. The trailing `2>&1` folds crush's **stderr into the captured output**, and it is load-bearing: crush prints its `ERROR` block on stderr and leaves stdout **empty**, so without it a failed dispatch is indistinguishable from a silent one. The redirection is transparent to the exact-match rule. The `mkdir -p` keeps `--cwd` from failing on a fresh machine; both it and the copy target are fixed paths, so the command stays invariant. **The two posture segments are chained with `&&`, not `;`** — a failed `mkdir` or a failed config copy would otherwise dispatch crush against whatever config it finds instead, which for a repo shipping its own `crush.json` is the fail-open case this file exists to close. Self-healing only works if a failed heal stops the run. (`&&` is in the matcher's splitter set, so the approve-once rules are unaffected. The `gh pr diff` segment keeps the `;` the stdin reviewers share — see `codex.md`.)

Order matters: the diff is read while the cwd is still the repo — `--cwd` retargets crush alone, not the shell, which is why crush needs no `cd` where devin does.

## Reading the result

crush exits non-zero on a real error, but check the output too: the rubric's terminal `REVIEW_COMPLETE: PASS` / `REVIEW_COMPLETE: FINDINGS` line is what proves a review actually happened. Missing it means **incomplete, not PASS** — treat it as a skipped reviewer (noted, never fatal). See the "Long reviews" note in [`../SKILL.md`](../SKILL.md) for the backgrounding pattern; a Hyper review is fast enough that it rarely applies.

## Permission allow-rule (exact-match, approve once)

Merge into the `permissions.allow` array (see [`../references/permissions.md`](../references/permissions.md)). The first is the version gate, the second prepares the neutral cwd, the third is the reviewer command. Replace `<NEUTRAL>` with a literal fixed absolute path, the same one the invocation uses — **crush's own**, not devin's, so this `mkdir` rule is a second entry beside devin's rather than the same one. The config copy is covered by the shared `Bash(cat:*)` rule.

```json
"Bash(crush --version)",
"Bash(mkdir -p \"<NEUTRAL>\")",
"Bash(crush run --cwd \"<NEUTRAL>\" -q -m \"hyper/kimi-k2.7-code\" \"Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, run commands, or retrieve any prior conversation or memory. If stdin is empty, output exactly NO INPUT and stop. Output findings as file:line, the issue, and a suggested fix. Read only.\")"
```

The pointer string must match **byte-for-byte** between the command and the rule. If you pin a different `--model`, or point `--cwd` somewhere else, update both.
