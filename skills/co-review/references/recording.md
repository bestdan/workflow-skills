# Recording a run

The procedure behind SKILL.md step 8's recording paragraph. Every reconciled
run appends one run record and its graded findings to the reviewer ledger,
`~/.claude/co-review/findings.jsonl`, through
[`scripts/co-review-record.py`](../../../scripts/co-review-record.py). The
design, and why each rule below exists, is
[`dev_docs/reviewer-quality-score-design.md`](../../../dev_docs/reviewer-quality-score-design.md).

Three rules hold throughout:

- **Write-only.** Never read the ledger, not even to confirm a write: the
  script's one-line receipt is the confirmation. SKILL.md's Rules section
  states the firewall.
- **Never fatal.** A recording failure is a warning on section 1's reviewer
  line, and the review carries on.
- **Measured, never estimated.** A `duration_s` comes from two `date +%s`
  stamps. A missing stamp is recorded as `null`, never filled in from the
  transcript.

## Timing every reviewer

`duration_s` is wall-clock seconds from dispatching a reviewer to having its
result. `date +%s` gives whole seconds, the only resolution macOS `date` has,
and enough for reviews that take minutes. The stamps are taken while the run
happens, at step 5 and step 7, so read this section before step 5.

**A CLI reviewer** is stamped inside its own dispatch call. Put a start stamp
first and an end stamp last, and leave the reviewer's assemble-and-dispatch
line between them exactly as its file under [`../reviewers/`](../reviewers/)
gives it:

```bash
date +%s > "<INPUT>.<run_id>.time"; <the reviewer's line, byte-for-byte>; date +%s >> "<INPUT>.<run_id>.time"
```

- `<run_id>` is the run's id from [The three input files](#the-three-input-files).
  Mint it **before the first dispatch**, not at step 8, and reuse it there. The
  stamp file must be per-run, not only per-agent — see
  [Why the stamp file carries the run id](#why-the-stamp-file-carries-the-run-id).
- `<INPUT>.<run_id>.time` sits beside that reviewer's own `<INPUT>`, so its
  directory already exists. **`grok` is the one exception to the position:** its
  `<INPUT>` lives inside `<NEUTRAL>`, which its leading `mkdir -p "<NEUTRAL>"`
  creates, so its start stamp goes right after that segment and ahead of the
  input lock, which keeps lock-wait time inside the measured interval as for
  every other reviewer: `mkdir -p "<NEUTRAL>" && date +%s >
  "<INPUT>.<run_id>.time" && <the rest of its chain, from the lock>; date +%s >>
  "<INPUT>.<run_id>.time"`.
- The end stamp is joined with `;`, so it lands whether the dispatch succeeded,
  failed, or was skipped by an `&&` gate. A backgrounded dispatch stamps itself
  when the reviewer exits, however late you notice.
- The reviewer's command is untouched, so its exact-match rule still matches:
  each `;`-separated segment is matched on its own, and redirection is
  transparent to matching. The stamps need `Bash(date:*)`. SKILL.md's
  `allowed-tools` grants it when `/co-review` opens the turn; otherwise it is a
  shared settings rule in [`permissions.md`](permissions.md). It is not
  optional: a call is approved only when every segment is, so a denied stamp
  takes the reviewer's dispatch down with it.
- The trailing stamp makes the call's exit status the stamp's, not the
  reviewer's. Judge the reviewer from its output and its `REVIEW_COMPLETE` line,
  as SKILL.md step 5 and each reviewer file already require.
- Read `<INPUT>.<run_id>.time` (`cat "<INPUT>.<run_id>.time"`) once the
  dispatch has finished. `duration_s` is the last line minus the first. A
  killed reviewer can leave a third line (below), and the last one is still the
  moment the wait ended.

**A timed-out reviewer** (the `--non-interactive` 15-minute bound) never
reaches its end stamp. When you stop waiting and kill it, append the stamp
yourself, `date +%s >> "<INPUT>.<run_id>.time"`, so its duration is how long it was
waited on — a lower bound on how long it would have taken. Killing only the
reviewer process lets the dispatch's own end stamp run too; the extra line is
harmless, since the duration reads first to last.

**Your own review** (step 7) runs in this session, so stamp it with two bare
`date +%s` calls: one immediately before you start forming the review, one
immediately after the findings list is complete. Both numbers are in the
transcript; subtract.

**`null`, never a guess:**

- a reviewer that was never dispatched: `skipped`, whatever the reason;
- a GitHub bot: co-review never dispatches one, so there is no
  dispatch-to-result interval to measure;
- a stamp file with fewer than two lines, or a stamp call that was denied.
  Record `null` and add `duration unmeasured` to that reviewer on the reviewer
  line.

### Why the stamp file carries the run id

A path named only for the agent is the same file for **every** co-review run
on the machine, in any repo and any session. That is why `<INPUT>` carries the
run id or is held under a lock (SKILL.md's Built-in invocations,
[#1071](https://github.com/bestdan/workflow-skills/issues/1071)). A stamp file
named only for the agent would be shared the same way: a second run's start
stamp truncates it and its end stamp appends to it, while the first run is
still waiting. The
first run then sees two lines that are not its own. If it waits on the line
count, or reads the file as "the dispatch has returned", it reads its output
file while its reviewer is still writing, and it records a live reviewer as
`failed`.

That happened to PR #1050's run on 2026-10-18 ([#1051](https://github.com/bestdan/workflow-skills/issues/1051)).
A `bestdan/dotfiles` session reviewing PR #902 dispatched its own codex while
#1050's codex was running. #1050's session read `1792332438` and `1792332447`
as its stamps, neither of which its dispatch wrote, and recorded codex as
failed after 9 s. Its real end stamp, `1792332471`, matched the second the
output file stopped changing, and that file held a complete review ending
`REVIEW_COMPLETE: FINDINGS`. A solo re-run of the same dispatch showed codex
and the shell behaving correctly: the call returned when codex exited, and the
output did not change afterwards.

Two rules follow, and the second holds whatever the file is named:

- Name the stamp file for the run (`<INPUT>.<run_id>.time`), so no other run
  can write to it.
- Judge a reviewer only after **its own** dispatch has finished: the Bash call
  returned, or for a backgrounded dispatch, its background task completed.
  Never infer that from a file another process can write. Output that is still
  missing its `REVIEW_COMPLETE` line while the dispatch is running is a
  reviewer still at work, not a failed one.

## The three input files

Write them with the Write tool under
`$HOME/.claude/co-review-input/record/`, named for the run:
`<run_id>.run.json`, `<run_id>.findings.json`, `<run_id>.mapping.json`. The
Write tool takes the expanded absolute path; the Bash call below spells it
`$HOME/…`. Never `$TMPDIR`, which the sandbox remaps, and never under
`~/.claude/co-review/`, which is the ledger's directory. The run id in the name
keeps concurrent runs (auto-pilot runs several) from overwriting each other's
files between the write and the call.

**`<run_id>.mapping.json`** is the label → identity mapping you kept at step
8, one entry per neutral label:

```json
{
  "Reviewer A": { "name": "claude", "kind": "agent" },
  "Reviewer B": { "name": "devin", "kind": "agent" },
  "Reviewer C": {
    "name": "bot:copilot-pull-request-reviewer",
    "kind": "agent"
  },
  "Reviewer D": { "kind": "human" }
}
```

- Your own review is `claude`.
- A local CLI reviewer is its config name (`codex`, `agy`, `devin`, `copilot`,
  `crush`, `grok`, or a custom agent's `name:`).
- A GitHub bot is `bot:<login>`, with any `[bot]` suffix dropped, so the review
  read and the inline-comment read give one name. A bot is a login ending
  `[bot]`, or `user.type` `Bot` in the API. The GitHub Copilot bot and the local
  `copilot` CLI are different reviewers and must never share a name.
- Every other GitHub author is a human: `{"kind": "human"}`, with no name. The
  script records only that a human corroborated a finding, and drops a finding
  only humans raised.

**`<run_id>.findings.json`** is the reconciler's JSON array exactly as it
returned it. Don't edit a verdict or a `sources` list to get it past the
script: a rejected run stays unrecorded, and the warning says why.

**`<run_id>.run.json`** is the run metadata:

```json
{
  "run_id": "2026-07-14T15-02-11Z-a3f9",
  "ts": "2026-07-14T15:02:11Z",
  "repo": "bestdan/workflow-skills",
  "pr": 231,
  "base_sha": "5ceb4a9",
  "head_sha": "3d1a42b",
  "mode": { "local": false, "post": false, "non_interactive": false },
  "diff_lines": 412,
  "co_review_version": "2.89.0",
  "reconciler_model": "claude-opus-4-8",
  "reviewers": [
    {
      "name": "claude",
      "disposition": "ran",
      "model": "claude-opus-4-8",
      "duration_s": 94
    },
    {
      "name": "devin",
      "disposition": "ran",
      "model": "swe-2-high",
      "command": "<its dispatch tail>",
      "duration_s": 212
    },
    {
      "name": "agy",
      "disposition": "skipped",
      "reason": "auth probe failed",
      "duration_s": null
    }
  ]
}
```

- `ts` is `date -u +%Y-%m-%dT%H:%M:%SZ`. `run_id` is that timestamp with `-`
  for each `:`, then `-` and four random hex characters, so two runs started in
  the same second on one host still differ.
- `repo` is the `<owner>/<name>` resolved in step 2, or `origin`'s under
  `--local`. `pr` is the PR number, `null` under `--local`.
- `head_sha` is `headRefOid`, or `git rev-parse HEAD` under `--local`.
  `base_sha` is `baseRefOid`, both from step 3's metadata read, or the merge-base or `<base>` tip the diff was
  taken against under `--local`.
- `diff_lines` is insertions plus deletions: `additions` plus `deletions` from
  step 3's metadata read in the PR modes, `git diff --shortstat <base>` under
  `--local`.
- `co_review_version` is `version` in `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`.
  `reconciler_model` is the model the reconciler ran on; it inherits this
  session's.
- `reviewers` lists every reviewer this run resolved, plus each GitHub bot
  whose review reached the reconciler or was waited on, under the same names as
  the mapping. `disposition` is one of:
  - `ran`: it produced a review, including findings that lacked the
    `REVIEW_COMPLETE` line;
  - `skipped`: never dispatched (a failed probe or gate, an unconfirmed custom
    command, a retired entry, a reviewer `--reviewer-set` left out), with a
    `reason`;
  - `timed-out`: it hit its bound;
  - `failed`: dispatched, but nothing usable came back (an auth or entitlement
    error in its output, `NO INPUT`, no findings and no `REVIEW_COMPLETE`
    line), with a `reason`.

  `model` is the model the invocation pins, or `null` where it pins none.
  `command` is the reviewer's own dispatch tail as it ran, never the assembly
  segments. The script fills in `host` itself: the hostname, or
  `CO_REVIEW_HOST` when set, for machines that share a hostname.

Leave out anything about scores or past runs. The metadata describes this run
only.

## The call

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/co-review-record.py" --run "$HOME/.claude/co-review-input/record/<run_id>.run.json" --findings "$HOME/.claude/co-review-input/record/<run_id>.findings.json" --mapping "$HOME/.claude/co-review-input/record/<run_id>.mapping.json"
```

**Type the path as SKILL.md renders `${CLAUDE_PLUGIN_ROOT}`**, the absolute
plugin path (the `--plugin-root` value in its allow-rule pre-flight). The
variable is not expanded in this file or in the Bash tool, so a literal copy
runs `/scripts/co-review-record.py` and exits `2`. A relative `scripts/…` path
resolves only when the repo under review is this plugin. Either way, since a
failure is non-fatal, the ledger would silently stay empty. SKILL.md's
`allowed-tools` grants the call. It writes under `~/.claude/co-review/`, so
where a Bash sandbox denies that write, run it unsandboxed.

Read its outcome:

- **Exit 0** prints one receipt line, `RECORDED: <n> findings, <m> reviewers`
  or `RECORDED: duplicate, skipped`. Put it on section 1's reviewer line as
  `ledger ✅ <receipt>`.
- **Any other exit** is a warning, never a stop. Put `ledger ❌ not recorded —
  <the script's error line>` on the reviewer line and carry on to step 9. A
  mistake in your own files, the mapping or the metadata, may be corrected and
  the call rerun once; the script is idempotent on the run id, so a rerun can
  never count a run twice.

Copying rows to other machines is a separate flush, never part of the review:
[`scripts/co-review-ledger-sync.py`](../../../scripts/co-review-ledger-sync.py),
run at session end by the plugin's `SessionEnd` hook, by `/co-review-stats`,
and by hand. Don't run it from a review.

When aggregate reviewer scores were already in this session's context before
the run started, add `scores were in context, discount this run` to the ledger
entry on the reviewer line.
