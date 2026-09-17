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

### Raw HTTP — uncredentialed for writes, and **credentialed for reads**

> **Amended 2026-09-16, and the heading changed with it.** A routine read a GitHub
> dependency edge over plain `curl` — `HTTP 200`, correct data — so "uncredentialed" is
> wrong as a blanket description of this channel. **The write findings below are
> untouched and still govern `claim-lock.md`.** Run
> `cse_01Dg1yyyDLSTKykKZKSxpQ7v`; full measurement in
> [`2026-09-05-cloud-session-plugin-and-proxy.md`](2026-09-05-cloud-session-plugin-and-proxy.md)
> → "2026-09-16: the same read, from a routine".
>
> **Amended again 2026-09-17: the write block is PATH-SCOPED, so "for writes" is too
> broad as well.** The same routine `PATCH`ed an issue's labels and got `HTTP 200`. The
> ref refusal below is real and specific to **that path** — it is not a statement about
> writes in general, and the refusal's own wording ("this GitHub API path") says so. Run
> `cse_012mi73fBwpCdckjPF3KKhts`; full measurement in the companion record →
> "2026-09-17: the proxy's write block is PATH-SCOPED". **Read every claim in this
> section as scoped to the endpoint it was measured on.**

- `gh` is **not installed**. Absent from `PATH`, and absent from
  `find / -maxdepth 4 -name gh -type f`. **Still true 2026-09-16**, including in a run
  that sourced `bestdan/dotfiles` — see the 09-16 section for why that is unexplained.
- ~~`curl` to `api.github.com` carries **no token**.~~ **Superseded 2026-09-16.**
  `$GH_TOKEN` holds the literal placeholder `proxy-injected` (len 14) — so "no token" is
  literally right and was the wrong thing to measure. **The egress proxy substitutes a
  real credential**, and the read succeeds carrying the placeholder. Read the token
  variable as saying nothing about what the channel can do.
- `POST` and `DELETE` on `/git/refs` return:

  ```
  403 Write access to this GitHub API path is not permitted through this proxy.
  ```

  > **Re-measured 2026-09-16 and unchanged — word for word, three weeks later.** Runs
  > `cse_01R2HLXrknbeDW8NuK8bBjYP` (DELETE) and `cse_01EvGKZQfMFPGrfSSucrNkC3` (POST),
  > against a disposable ref in `bestdan/workflow-skills`, both returning the body above
  > with `documentation_url: …/claude-code/github-actions`.
  >
  > **The DELETE was run against a ref confirmed to exist**, by a `GET` returning 200
  > immediately before, so the refusal is not an artefact of a missing target. The ref
  > was still present afterwards and was removed by hand.
  >
  > This was worth re-running rather than inheriting: the **read** half of this same
  > record was overturned the same day, and "the other half is probably still fine" is
  > the reasoning that produced the errors these records exist to correct. It is now
  > measured, not assumed. **This is the finding `claim-lock.md` rests on, and it
  > stands.**
  >
  > A first POST attempt returned `415 Request bodies must declare Content-Type:
  > application/json` — a malformed request of the prober's own making, testing nothing.
  > Recorded because a 415 in a transcript reads like a refusal and is not one.

- Read behaviour on that path was **inconsistent between runs**, so it is not
  dependable for reads either. **That is about `/git/refs` specifically**; the 09-16
  read was against `/issues/{n}/dependencies/blocked_by` and was clean.

Consequence: the `gh api` acquire form in `claim-lock.md` is **local-only** — still
true, and now for a sharper reason: `gh` is absent rather than merely unusable.

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
