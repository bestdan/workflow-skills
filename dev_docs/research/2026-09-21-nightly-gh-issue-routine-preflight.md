---
created: 2026-09-21
---

# `gh` in a cloud routine: the setup script installs it, and repo-scoped REST serves

**Measured 2026-09-21** across three runs of one probe on environment
`env_01KURKZo3LcfRKBaEZWcbsrk`, varying one thing at a time. A dated snapshot: it
records what was true that day and is allowed to go stale.

Two findings, both of which settle questions older records left open:

1. **`gh` is installed by the environment's setup script, not by attaching
   `bestdan/dotfiles` as a source.** Attaching dotfiles does **not** fire its
   `.claude/hooks/session-start.sh`.
2. **Repo-scoped `gh` REST serves a repo attached as a source** — `HTTP 200`,
   correct data. This is the question
   [`2026-09-07-cloud-routine-plugins-and-gh.md`](2026-09-07-cloud-routine-plugins-and-gh.md)
   named as "still the unanswered one", blocked three times.

## The three runs

Each probe was read-only: no `--apply`, no label write, no issue or PR touched, and
the only push was `--dry-run`.

| Run                 | Sources                                   | Setup script      | `gh`       | `just` / `shellcheck` |
| ------------------- | ----------------------------------------- | ----------------- | ---------- | --------------------- |
| `cse_011b38Rxrvfm…` | workflow-skills                           | pre-change        | **absent** | absent                |
| `cse_01FRwcpxdtcQ…` | workflow-skills, dotfiles, agent-guidance | pre-change        | **absent** | absent                |
| `cse_015pk8QjYd6L…` | workflow-skills, dotfiles, agent-guidance | **installs `gh`** | **2.45.0** | absent                |

Run 2 is the discriminating one. The 09-07 record observed that every `gh`-present
run had sourced `dotfiles`, whose `session-start.sh` apt-installs `shellcheck`, `zsh`
and `gh` under `CLAUDE_CODE_REMOTE=true`, and reasoned from that to the source repo
as the mechanism. Run 2 attaches dotfiles and `gh` is **still** absent — and so are
`just` and `shellcheck`, the rest of that hook's payload. All three missing together
means the hook never ran at all. **Attaching a repo as a source does not run its
`SessionStart` hook**; the correlation was real and the causal reading was not.

Run 3 changes only the setup script, and `just`/`shellcheck` stay absent — so `gh`'s
arrival is attributable to that block and nothing else.

## What run 3 measured

| # | Command                                                | Exit  | Verbatim result                                                                                      |
| - | ------------------------------------------------------ | ----- | ---------------------------------------------------------------------------------------------------- |
| 1 | `cat /usr/local/share/gh-setup-receipt.txt`            | 0     | `written_at=2026-09-21T12:56:05Z whoami=root uid=0 gh_path=/usr/bin/gh gh_version=gh version 2.45.0` |
| 2 | `command -v gh` / `gh --version`                       | 0     | `/usr/bin/gh` — `gh version 2.45.0 (2025-07-18 Ubuntu 2.45.0-1ubuntu0.3)`                            |
| 3 | `command -v just` / `command -v shellcheck`            | 1/1   | both empty                                                                                           |
| 4 | `gh api repos/bestdan/workflow-skills --jq .full_name` | 0     | `bestdan/workflow-skills`                                                                            |
| 5 | `gh api repos/bestdan/workflow-skills/labels`          | 0     | `auto-eligible` `auto:eligible` `auto:human-review-needed` `bug` `dependencies`                      |
| 6 | `gh auth status`                                       | **0** | `X Failed to log in to github.com using token (GH_TOKEN)` / `The token in GH_TOKEN is invalid.`      |
| 7 | `gh-issue-state.py --help`                             | 0     | argparse usage                                                                                       |
| — | `push --dry-run` (run 2)                               | 0     | `* [new branch] HEAD -> zz-probe-never-created`                                                      |

## The receipt is the instrument, not decoration

`gh` had been attempted in the setup script before, and the attempt "didn't work" with
no way to tell why. A bare `command -v gh` cannot distinguish **the setup script never
ran** from **it ran and its installs don't reach the session** — and those have
opposite fixes. The receipt splits them: its presence proves the script ran and that a
file it wrote survived into the session, so a receipt beside a missing `gh` would
indict apt, while no receipt at all would indict the script. Run 3 produced both, which
is why one run settled it.

The receipt also records `whoami=root uid=0`, which is why the `$SUDO` fallback in the
block is untested here rather than confirmed unnecessary.

## `gh auth status` is worse than worthless — measured, not inherited

It reported the token **invalid** and **exited 0**, while checks 4 and 5 succeeded
seconds later. The proxy replaces the credential. The 09-07 record says never to gate
on `gh`'s exit code; run 3 shows the body lies too, in the direction of a false
negative. **Probe the call you actually need.** A preflight gating on `gh auth status`
would have declared this environment broken while it was working.

## What this does not say

- It does not say the dotfiles `SessionStart` hook never works anywhere — only that
  attaching dotfiles as a source did not fire it in these runs.
- It does not say **writes** work. Every call above is a read. The `/git/refs` 403 in
  [`2026-08-24-routine-claim-channel.md`](2026-08-24-routine-claim-channel.md) is
  untouched, so the claim lock remains the comment-token election
  ([`commands/handlers/claim-lock.md`](../../commands/handlers/claim-lock.md)).
- It does not say GraphQL works. Checks 4 and 5 are REST by construction; `gh pr list`
  and `gh pr view` were refused as GraphQL on 09-07 and were not re-tried here.
- `gh` 2.45.0 is Ubuntu's package, not the latest release. Nothing here measures a
  subcommand that needs a newer one.

## The channel probe: REST serves everything, porcelain serves nothing

A fourth run (`cse_011qxdBkk3APPDg8yicfVg7a`, 13:20Z) put twelve read-only calls through
both channels. The split is total — no partial support, no per-subcommand exceptions.

| Call                                             | Exit | Result                                                 |
| ------------------------------------------------ | ---- | ------------------------------------------------------ |
| `gh api '…/labels?per_page=3'`                   | 0    | `auto-eligible auto:eligible auto:human-review-needed` |
| `gh api '…/issues?state=open&per_page=3'`        | 0    | `820 819 818`                                          |
| `gh api '…/pulls?state=open&per_page=3'`         | 0    | `819 817 801`                                          |
| `gh api …/pulls/819 --jq .state`                 | 0    | `open`                                                 |
| `gh issue list`                                  | 1    | **HTTP 403 GraphQL**                                   |
| `gh issue list --json number,title`              | 1    | **HTTP 403 GraphQL**                                   |
| `gh issue view 723 --json number,labels`         | 1    | **HTTP 403 GraphQL**                                   |
| `gh repo view`                                   | 1    | **HTTP 403 GraphQL**                                   |
| `gh pr list`                                     | 1    | **HTTP 403 GraphQL**                                   |
| `gh pr view 819 --json number,state`             | 1    | **HTTP 403 GraphQL**                                   |
| `gh api graphql -f query='query{viewer{login}}'` | 1    | **HTTP 403 GraphQL**                                   |
| `gh pr create --help`                            | 0    | help text (says nothing about creating)                |

**The refusal body names replacement routes**, which the 09-07 record did not capture:

> `GitHub GraphQL is not available from Claude Code sessions; use the REST API (gh api
> repos/{owner}/{repo}/...). For review threads, auto-merge, and draft/ready-for-review
> use the CCR routes on api.github.com: GET /repos/{owner}/{repo}/pulls/{n}/ccr/review_threads,
> POST /repos/{owner}/{repo}/pulls/{n}/ccr/comments/{comment_id}/resolve (or /unresolve),
> PUT or DELETE /repos/{owner}/…`

So draft/ready-for-review and auto-merge — both GraphQL-only on the public API — have
documented CCR equivalents here. That is a lead, not a measurement: none was exercised.

### What this means for the handler, and it is not small

Every refused call above appears in **shipped executable assets**, not only in prose:
`gh pr list` (12), `gh issue list` (12), `gh pr edit` (10), `gh pr view` (8),
`gh repo view` (6), `gh pr ready` (2). Only `gh-issue-state.py` is on `gh api`.

**The gh-issue handler cannot run in a cloud routine as it stands.** Candidate-finding,
PR creation, PR editing and the draft→ready transition are all on the refused channel,
so the failure is not confined to one step that could be skipped. This is a channel
problem, not a `gh` version or presence problem — no version of `gh` changes it, which
is why a minimum-version guard would be answering the wrong question.

Two honest limits on that conclusion. The twelve calls above were run directly, not
through the verbs, so this says the verbs' **ingredients** fail, not that a particular
verb was observed failing. And nothing here was a write — the refusals are all on reads,
which merely makes the write case no better.

## Incidental: the shell differs between runs

Call 1 failed as `(eval):1: no matches found: repos/…/labels?per_page=3` — **zsh**
glob-expanding the `?`. Earlier runs the same day reported `/bin/bash: line N:`. The
interactive shell is not stable across runs, so any `gh api` URL carrying `?` or `&`
must be quoted; unquoted, it works under bash and dies under zsh.

## Incidental: a label outside the vocabulary

Check 5 returned both `auto-eligible` and `auto:eligible`. Only the colon form is in
`commands/handlers/assets/labels.yml`; the hyphenated one is the Linear-side spelling
from finplan's nightly job. It is inert — the write helper owns only its four
namespaces and carries everything else forward — but it reads as a rung to a human
scanning the board, and `carried_rungs()` will not count it.
