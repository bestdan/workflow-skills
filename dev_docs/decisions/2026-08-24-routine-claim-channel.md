# How a cloud routine reaches GitHub, and why it cannot hold the ref lock

**Measured 2026-08-24** against the live API from inside a Claude Code cloud routine.
This is a dated snapshot: it records what was true on that day and is allowed to go
stale. The rule it supports lives in
[`commands/handlers/claim-lock.md`](../../commands/handlers/claim-lock.md); this file is
the evidence behind it.

## Why this exists

`claim-lock.md` was wrong about routines **twice**, and both times the same way: the
claim was taken from documentation rather than from a measurement.

- The first version asserted that a cloud session "runs pinned to a fixed
  `claude/<session>` branch and cannot create `task/<KEY>`" — so the ref lock was
  described as unavailable to routines outright. Removed in `08a6642`.
- The second asserted that the proxy refused GitHub API writes outright. That is true
  of the raw HTTP path and false of the MCP connector, which is the credentialed one —
  so the conclusion drawn from it was wrong.

Neither survived contact with a probe.

So the operative instruction, which belongs with the rule and not only here, is: **do
not re-derive routine behaviour from documentation.** Probe it.

## The two channels

A routine reaches GitHub two ways, and they do not behave alike.

### Raw HTTP — uncredentialed

- `gh` is **not installed**. Absent from `PATH`, and absent from
  `find / -maxdepth 4 -name gh -type f`.
- `curl` to `api.github.com` carries **no token**.
- `POST` and `DELETE` on `/git/refs` return:

  ```
  403 Write access to this GitHub API path is not permitted through this proxy.
  ```

- Read behaviour on that path was **inconsistent between runs**, so it is not
  dependable for reads either.

Consequence: the `gh api` acquire form in `claim-lock.md` is **local-only**.

### The GitHub MCP connector — authenticated

This is the routine's real channel. Its tool surface was **enumerated in full** on
2026-08-24 — 58 tools, not sampled.

## `create_branch` works as an acquire primitive

```
create_branch(owner, repo, branch="zz/probe-b-20260824")
  → {"ref":"refs/heads/zz/probe-b-20260824","object":{"sha":"4b6379aa…"}}

same call again
  → failed to create branch: Reference already exists
```

Create-only, duplicate rejected — the same election semantics as `POST /git/refs`. The
primitive is sound.

One caveat for whenever the default flips: `create_branch` takes `from_branch`, **not a
sha**, so it resolves the source tip at call time and cannot pin an exact base. The
election is unaffected (the lock is the _name_), but the branch may not sit at the sha
the session read earlier.

## But a routine cannot release

Both release paths are closed:

- The connector's 58 tools contain **no delete-branch and no delete-ref tool**.
- `git push --delete` fails with a **403 RPC error**, even though `git push` creating a
  ref succeeds.

There is also **no delete-comment tool**, so a routine cannot retract a claim comment
either — but a leftover comment costs hygiene where a leftover ref costs the issue.
That asymmetry is the whole decision, and it is stated inline in `claim-lock.md`.

## What else the connector cannot do

Measured in the same 2026-08-24 probe session, and recorded here because each one
shaped a task in the GitHub-Issues migration. Unlike the facts above, these were not
carried over from `claim-lock.md` — nothing else in the repo corroborates them:

- **No dependency-edge tool** — `blocked_by` / `blocking` are unreachable unattended.
- **No milestone create** (a milestone can be _set_, not created).
- **No issue transfer.**

A routine **can** create issues and write issue fields, so `/add-task` works unattended.
`issue_write` **replaces** the label set and **auto-creates** unknown names, matching the
`gh api` path — which is why validate-then-replace is the rule on both channels.

## The decision

Routines stay on the **comment-token election**, not the ref lock. Flipping that default
is a one-paragraph change in `claim-lock.md` — once a stale-ref sweep exists. None does
today: `scripts/claim-scan.sh` and `/doctor` both operate on `repo-pr` claim PRs, not
refs.
