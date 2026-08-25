# local-review — threaded replies and multi-round review

Design for a conversational review loop in `local-review`: the agent answers a
review comment inline, under that comment, while the page stays open. The
model is a GitHub PR conversation. The user submits a round, the agent replies
in place, and a settled thread can be resolved and disappear from view.

This document is a plan, not a change. Nothing here is implemented.

## The problem — what the one-shot flow costs

Today the flow is one round per server launch. The skill starts `server.py`
with `--once`, the user submits, the server writes `--out` and exits. The
agent reads the file and acts in the chat transcript.

That shape costs three things:

1. **Relaunch friction.** A second round needs a new server, a new token, a
   new browser tab, and a new startup poll. A three-round review pays that
   tax twice for no reason.
2. **A split conversation.** The user comments in the browser, but the
   agent's answer lands in the chat window. The user must map each chat
   answer back to the line it was about. GitHub solved this with threads;
   this tool makes the user do it by hand.
3. **No settlement record.** Nothing marks a comment as answered or settled.
   On round two the user re-reads round one's comments to remember which
   ones are done.

## Constraints this design inherits

- **Stdlib only.** `server.py` stays on the Python standard library; the
  page stays on vendored JS. No new dependency.
- **Loopback is not a trust boundary.** Any local web page can POST to
  `127.0.0.1`. The per-launch token path and the Origin check are the
  authorization, and every new endpoint must sit behind both.
- **Diff content is untrusted evidence, not instruction.** The `code` field
  can be authored by anyone who can open a PR. That rule extends unchanged
  to every payload this design adds.
- **The one-shot contract survives under `--once`.** With `--once`, the
  `--out` shape and the skill's step-4/step-5 loop stay valid. I verified by
  grep that no other skill consumes the `--out` payload today (`co-review`'s
  `local_reviewers` key is a different concept — agent reviewers, not this
  tool). The compatibility surface is this skill's own contract, its human
  users, and the test harness — one launch shape changes mode, and Mode
  derivation below names that cost.

## The design

### One sentence

The server grows a thread store and four small endpoints, active whenever an
explicit `--out` names an agent on the other end; the agent posts replies
over HTTP with the token it already holds; the page learns about replies
through the `/state` poll it already runs.

### Mode derivation: `--out` names the agent

There is no mode flag. The server derives the mode from two arguments it
already takes:

| Launch shape             | Mode       | Behavior                                                                                       |
| ------------------------ | ---------- | ---------------------------------------------------------------------------------------------- |
| `--out`, no `--once`     | threads    | Stays alive across rounds. Thread endpoints and thread UI on. Two buttons: `Submit`, `Finish`. |
| `--out` with `--once`    | one-shot   | One round, write `--out`, exit. Today's skill launch, post-stage-0 payload shape.              |
| no `--out`, no `--once`  | human-only | Today's hand-run behavior exactly. No thread endpoints, no Reply controls.                     |
| no `--out` with `--once` | human-only | One round, write `pr_comments.json`, exit. Today's behavior for that launch.                   |

`--once` always means "exit after one successful submit", in every mode. Only
an explicit `--out` with no `--once` keeps the server alive.

The derivation works because `--out` already encodes the one fact that
matters: an agent is watching the file. The skill's step 2 always passes
`--out`; the README's hand-run recipe (`local-review --git main`) never
does. `--once` stays as the opt-out: one round, then exit.

Derivation cannot be set wrong, and a flag can. A flag given without an
agent renders Reply controls that post into silence — a dead affordance
that implies a listener. A flag omitted on an agent launch leaves the agent
polling rounds with no way to answer them. Deriving from `--out` ties the
affordance to the channel: thread UI appears exactly when a reader for the
rounds exists, and an agent launch always carries its reply channel.

One implementation subtlety: `--out` today has an argparse **default**
(`pr_comments.json`), so "given" must mean "given explicitly". The default
moves to `None`; human-only mode still writes `pr_comments.json` on submit,
so the hand-run flow is unchanged in every observable way.

Two meanings shift, and both are named here rather than hidden:

- **`--once` narrows from "shut down after a successful submit" to "single
  round".** The observable behavior for every launch that passes `--once`
  today is identical: one submit, one file, exit 0. What changes is what
  the absence of `--once` means when `--out` is present — that absence now
  selects threads mode.
- **`--out` without `--once` is a live combination, and its mode changes.**
  `scripts/test-local-review.sh` uses it deliberately in four cases: the
  submission-slot race (line 1028, "stay-alive: no --once"), the security
  gates run (line 1079, "stay-alive: several requests in sequence"), the
  unwritable `--out` preflight (line 959), and `--out` as a directory
  (line 1310). Under this design those launches become threads-mode
  servers. Each case keeps its assertions: sequential second submits stay
  200 (now by design, not by slot release), the `Submit review` header
  button text the security case greps for stays, and the `--out` preflight
  path is mode-independent. What changes is the payload those cases
  observe — it gains `round`, `id`, and `threads` keys — so their labels
  and any payload-shape assertions move with stage 1. A future test that
  wants today's payload shape must pass `--once`; a stay-alive one-shot
  mode no longer exists.

In threads mode:

- The server stays alive across submits. Each submit is a **round**,
  numbered from 1 by the server.
- **Submit** sends the round to the agent and keeps the server alive. The
  agent answers each comment with a reply in the page. The Submit button
  re-arms for the next round.
- **Finish** closes out the review. The server writes the final round and
  shuts down, exactly as one-shot mode does after its single submit.

No mode carries a verdict. There is no `approved` field and no `Approve`
button anywhere; stage 0 removes both from the current server before the
thread work starts. The two threads-mode buttons state what happens next to
the session: `Submit` asks the agent for replies, `Finish` ends the review.
The `APPROVED: ensure the PR is pushed to GitHub and marked ready for
review` stdout line goes with the verdict. Pushing a PR is not part of a
local review.

### Comment identity

**The server mints thread ids at submit time.** When a round arrives at
`/submit`, the server assigns each new comment an id of the form `t<n>`
(`t1`, `t2`, …, a per-launch counter). The id is returned in the submit
response, written into `--out`, and used by every later reply and resolve
call.

Why the server and not the browser: before submit, a comment is a draft that
lives only in the browser and can be edited or deleted freely — it needs no
identity. After submit, the server owns the thread store, so the server is
the natural authority for the keys into it. Browser-minted UUIDs were
rejected (see Alternatives).

**The anchor is a snapshot, not a live reference.** Each thread stores the
`{file, side, line, code}` it was created with, plus the `diff_sig` of the
round that created it. When the diff changes under `/refresh`, the page
re-places each thread by this rule, in order:

1. If the file still has a row at `{side, line}` whose text equals `code`,
   render the thread there.
2. Else, if exactly one row in that file's rendered rows has text equal to
   `code`, render the thread at that row and mark it "moved".
3. Else, render the thread in an **Outdated** strip at the top of that
   file's card, showing the original anchor. The thread is never dropped:
   a comment the user wrote must stay visible until the user resolves it.

Re-placement is a pure function (`placeThreads(files, threads)` → placement
list) that lives inside the page's `PURE-PREVIEW` region, so
`test-local-review.sh` can drive it under bare node with a fixture corpus.

### State ownership

| State                          | Lives in       | Survives page reload | Survives server restart |
| ------------------------------ | -------------- | -------------------- | ----------------------- |
| Draft comments (pre-submit)    | Browser memory | No (as today)        | No                      |
| Threads, replies, resolved bit | Server memory  | Yes (`GET /threads`) | No — deliberate         |
| Round payloads                 | `--out` file   | Yes                  | Yes (last round only)   |
| View-mode overrides            | sessionStorage | Yes (as today)       | Yes (as today)          |

Server memory means `Handler` class attributes, matching how every other
piece of server state is held. Thread state dies with the server on purpose:
the conversation has the same lifetime as the token, and the durable record
the agent acts on is the transcript plus the round payloads it already read.
Persisting threads to disk would create a second copy of state the chat
transcript already owns, which this repo's rules forbid.

A page reload is cheap: the page fetches `GET /threads` at startup and
re-renders every thread against the current diff. Only unsubmitted drafts
are lost, which is today's behavior too.

### The turn-taking protocol

```
user            browser              server                 agent
 |  submit round   |                    |                      |
 |---------------->| POST /submit ----->| write --out          |
 |                 |                    |    (atomic replace)  |
 |                 |                    |<---- poll --out -----|
 |                 |                    |     read, rm, echo   |
 |                 |                    |<-- POST /reply ------|  (per thread)
 |                 |<-- /state poll --- |                      |
 |                 |  threads_rev bump  |                      |
 |                 |  GET /threads      |                      |
 |  reads replies  |  render inline     |                      |
 |  resolve / answer / new comments / next submit …            |
```

The agent's write channel is HTTP against the running server. The agent
already holds the token: it parsed `LOCAL_REVIEW_URL=` from the launch log.
A reply is one `curl` against the tokenized path. No watched files, no
reply directory — the server is already the rendezvous point, and a second
channel would need its own liveness and ordering rules.

The browser's read channel is the existing `/state` poll (6-second interval,
plus focus and visibility triggers). `/state` gains one integer field. No
SSE, no long-poll (see Alternatives).

### Endpoint table

All routes mount under `/<token>/` exactly as today; cookie fallback applies
to the browser, the token path serves the agent. POSTs pass the existing
`_origin_ok()` gate. The new endpoints exist only in threads mode (explicit
`--out`, no `--once`); in one-shot and human-only mode they 404.

| Method | Route      | Caller          | Purpose                                       |
| ------ | ---------- | --------------- | --------------------------------------------- |
| GET    | `/state`   | browser         | today's staleness + new `threads_rev` integer |
| GET    | `/threads` | browser         | full thread state, for render and reload      |
| POST   | `/submit`  | browser         | today's round write + id minting + round no.  |
| POST   | `/reply`   | agent & browser | append a reply to one thread                  |
| POST   | `/resolve` | browser         | set or clear one thread's `resolved` bit      |
| POST   | `/refresh` | browser         | unchanged; threads survive it (drafts do not) |

`threads_rev` starts at 0 and increments on every mutation of the thread
store (submit, reply, resolve). The page compares it to the value it last
rendered and fetches `/threads` on change. A mutex (`threading.Lock`) guards
the store; the existing `_submit_lock` keeps its current job.

### Payload schemas

`POST /reply` request, and the stored reply object:

```json
{
  "thread_id": "t3",
  "author": "agent",
  "text": "Fixed in the same file; the guard now checks both sides."
}
```

`author` is `"agent"` or `"user"`. The two callers share one bearer token,
so the field is a label, not an authenticated identity — acceptable in a
single-user loopback tool, and stated in the threat model below. The server
stamps `ts` (epoch seconds) on each reply. Response: `{"ok": true,
"threads_rev": 7}`. Unknown `thread_id` → 404 JSON. Empty `text` → 400.

`POST /resolve`:

```json
{ "thread_id": "t3", "resolved": true }
```

`GET /threads` response:

```json
{
  "threads_rev": 7,
  "round": 2,
  "threads": [
    {
      "id": "t3",
      "round": 1,
      "file": "scripts/foo.py",
      "side": "R",
      "line": 41,
      "code": "    return cached",
      "endLine": null,
      "kind": null,
      "github": false,
      "text": "This caches before validation — is that safe?",
      "resolved": false,
      "replies": [
        {
          "author": "agent",
          "ts": 1787392201,
          "text": "No — moved the check above the cache write."
        },
        { "author": "user", "ts": 1787392360, "text": "Good, resolving." }
      ]
    }
  ]
}
```

`--out` round payload (written on every submit, atomic replace as today):

```json
{
  "meta": { "...": "unchanged" },
  "round": 2,
  "summary": "",
  "finished": false,
  "comments": [
    {
      "id": "t7",
      "file": "scripts/foo.py",
      "side": "R",
      "line": 88,
      "code": "...",
      "text": "..."
    }
  ],
  "threads": [
    { "...": "the full GET /threads array, resolved threads included" }
  ]
}
```

`comments` keeps today's shape and meaning — the entries new in this round —
plus the minted `id`. `threads` is the complete state, so overwriting the
previous round's file loses nothing: every earlier comment, reply, and
resolution rides along in full. That property is what makes a single `--out`
path safe across rounds; the agent removes the file after reading and
resumes polling. In one-shot mode (`--once`) the payload matches the
post-stage-0 one-shot shape — today's payload minus the `approved` key stage 0
removes. It carries none of the keys this design adds: no `round`, no `id`, no
`threads`, and no `finished`. Human-only mode writes the same one-shot shape to
the default `pr_comments.json`.

### Thread lifecycle

A thread is born at submit, from one saved comment. It accumulates replies
from either side. The user (or a reply-driven agreement) resolves it; a
resolved thread and all its replies leave the diff view. Resolution is a
hidden bit, not a deletion: the thread stays in the store, appears in every
later round's `threads` array marked `resolved: true`, and a per-file
"N resolved" toggle in the file header shows resolved threads again on
demand — with a **Reopen** control that clears the bit. Nothing the user
wrote is ever destroyed while the server lives.

Resolution authority is the user's, in v1. The agent proposes resolution in
reply text ("resolving unless you object" is prose, not a state change); the
Resolve button lives only in the page. This matches the tool's premise —
the human is the reviewer — and it keeps the agent-facing surface to one
write verb. Extending `/resolve` to the agent later is additive. The user
settled this — see Decisions the user made.

### UI changes, precisely

- A submitted comment's chip stays in the grid after submit (today it stays
  too, but the page is about to die; now it persists meaningfully). Under
  the chip, replies render as indented rows inside the same `.cmt-row`:
  author label, timestamp, text via `textContent`. Reply text is never
  parsed as HTML and never rendered as markdown in v1.
- Each unresolved thread chip carries a **Reply** affordance (textarea, as
  the composer today) posting `POST /reply` with `author: "user"`, and a
  **Resolve** button posting `/resolve`.
- A resolved thread disappears from the grid. The file header gains a small
  "3 resolved" counter; clicking it toggles a strip listing resolved
  threads, each with Reopen.
- After a `Submit` round, the Submit button re-arms as `Submit review (0)`
  instead of freezing at `Submitted ✓`.
- The finish dialog holds two buttons in threads mode: **Submit** ("send
  this round to Claude") and **Finish** ("end the review and close this
  page"). Neither states a verdict. One-shot and human-only mode hold one
  button, `Submit review` — stage 0 already removed `Approve` there.
- Threads whose anchor fails re-placement rule 1 and 2 render in the
  per-file **Outdated** strip described under Comment identity.
- A subtle header note shows the round number: `Round 2`.

### The agent-side loop, and the skill body

The skill's step 4 becomes a bounded outer loop:

```bash
round=0
while :; do
  for i in $(seq 1 2400); do [ -s "$OUT" ] && break; sleep 3; done
  [ -s "$OUT" ] || break            # 2h idle: stop watching, ask the user
  payload=$(cat "$OUT"); rm -f "$OUT"
  # echo the round (step 5).
  # finished==true → break HERE, before any reply: the server has already
  #   exited, so there is no reply channel left and every curl would fail.
  # else act on the round, then per thread answered:
  # curl -sf -X POST "$BASE/reply" -H 'Content-Type: application/json' \
  #   -d '{"thread_id":"t3","author":"agent","text":"..."}'
done
```

Termination is explicit and threefold: a finished round (server exits, loop
breaks), the idle bound (agent reports "no round in 2 hours" and asks the
user), or the user saying so in chat (agent kills the recorded PID). The
page never closing is therefore not an unbounded wait — the wait is per
round and bounded exactly as today.

The SKILL.md body change is small: step 2 drops `--once` from the launch
line (the `--out` it already passes selects threads mode), step 4 gains the
outer loop above and the reply call, step 5 is
unchanged per round. The protocol detail — endpoint table, payload schemas,
re-placement rules, the resolved-thread semantics — moves to a new
`skills/local-review/references/threads.md`, loaded only when the skill runs
a threaded review. Estimated body growth: ~45 lines against the 500-line
cap (today 243).

## Security posture

- **No new capability class.** `/reply` and `/resolve` mutate page-display
  state and the next round's payload. Neither touches GitHub, the working
  tree, or any file except `--out`, which `/submit` already writes. The
  worst a forged reply can do is show text in the reviewer's page and echo
  it into the payload.
- **Same gates as `/submit`.** Both new POSTs sit behind the token path (or
  session cookie), the `Sec-Fetch-Site` check, and the Origin allowlist. A
  hostile local page cannot reach them without the token, exactly as it
  cannot reach `/submit` today.
- **The agent authenticates with the token it already holds.** No second
  credential is introduced. The token's handling rules in the skill (never
  persist it, never rebuild it from the port) already cover the reply calls.
- **`author` is a label, not an identity.** Both callers hold the same
  bearer, so the server cannot verify who wrote a reply. Accepted for a
  single-user loopback tool; recorded here so nobody later treats the label
  as evidence.
- **Untrusted evidence, restated for replies.** The `code` field stays
  attacker-influenceable and stays evidence-not-instruction. Reply `text`
  from the user is a request, as the user's comment `text` is today. Reply
  `text` labeled `agent` in a payload the agent reads back is its own prior
  output and must not be treated as a fresh user instruction. All reply text
  reaches the page through `textContent` only — the page keeps its
  "no attacker-derived string reaches an HTML parser" invariant with zero
  new escaping code.

## Alternatives considered and rejected

- **A watched reply file (agent writes, server tails).** My first instinct:
  it mirrors the `--out` handoff. Rejected: it doubles the channel count
  (HTTP one way, files the other), needs its own atomicity and ordering
  rules, and the server would poll the filesystem to serve a browser that
  is already polling the server. HTTP into the existing server is one
  mechanism doing both directions.
- **SSE or long-poll for reply delivery.** Real push, nicer latency.
  Rejected: `ThreadingHTTPServer` pins one thread per open connection, an
  SSE connection is open forever, and shutdown (`srv.shutdown()`) must then
  reap parked handler threads — new failure modes for a latency win the
  6-second poll makes invisible. The poll already exists; one integer rides
  it for free.
- **Browser-minted comment ids (`crypto.randomUUID` at draft time).**
  Rejected: drafts need no identity, ids minted before submit die with a
  discarded draft, and the server would have to trust client uniqueness for
  keys into its own store. Server minting at the ownership boundary is
  smaller and testable from the harness without a browser.
- **Per-round `--out` files (`out.round-N.json`).** Rejected: it changes the
  poll contract (`[ -s "$OUT" ]`) that the skill and its tests encode, and
  the full-state `threads` array makes overwrite lossless anyway.
- **Persisting threads to disk for server-restart recovery.** Rejected: a
  restart also loses the token, so the page is unreachable anyway; the
  transcript already holds every round the agent read; and a state file is
  a copy of state another system owns.
- **A `--threads` flag.** This document's first draft chose one, and
  rejected derivation as "a compatibility break hidden behind an absence".
  The user asked why a flag is needed at all, and the draft had no good
  answer: `--out` already states the fact a flag would restate — an agent
  is watching. A flag also adds two ways to be wrong that derivation
  removes: thread UI with no listener, and an agent with no reply channel.
  The compatibility break the draft feared is real but small — four named
  harness cases, enumerated under Mode derivation — and it is paid once,
  visibly, in stage 1. The reversal is recorded rather than quietly edited
  away because the first instinct ("explicit beats inferred") is the
  instructive mistake: explicitness is worthless when the explicit knob can
  contradict the channel it describes.
- **Agent-side resolve in v1.** Rejected for now: it doubles the write
  surface before the loop has real use, and a wrong auto-resolve hides a
  comment the user still cares about. Reopen exists, but the failure is
  silent. Deferred, not refused.

## Decisions the user made

Every open question this document raised is settled. Nothing below awaits
an answer.

- **No mode flag: the mode derives from `--out`.** The first draft
  introduced `--threads`; the user asked why a flag is needed, and it is
  not. `--out` given without `--once` selects threads mode; `--once`
  selects one round then exit; no `--out` keeps today's hand-run behavior.
  The full derivation, and the harness cases whose mode changes, are under
  Mode derivation. This settlement also removes the flag-name question the
  draft left open.
- **Threads mode has no verdict, and two buttons.** `Submit` sends the
  round and calls the agent for replies. `Finish` closes out the local
  review. Neither judges the code, so the mode drops the `approved` field,
  the `Approve` button, and the `APPROVED: ensure the PR is pushed to
  GitHub and marked ready for review` instruction the server prints today.
  A local review is local: making a PR ready is a separate request the
  user makes in chat.
- **The verdict leaves one-shot mode too.** Strip `Approve`, the `approved`
  payload field, and the same `APPROVED:` stdout block from the current
  server as well. One review surface must not carry two models of what a
  submit means. This is a behavior change to a flow in use today, so it
  lands as its own PR — stage 0 of the plan.
- **Replies render as plain text.** Reply text reaches the page through
  `textContent` only. No markdown render path is added. A code span in a
  reply shows its backticks. This keeps the reply surface out of the
  security review that a second render path would need.
- **The agent must not resolve threads.** Resolve is user-only. The agent
  proposes in prose; the user clicks. This keeps resolution as the human's
  signal that a point is settled, and prevents the agent from hiding a
  comment it wrongly judged settled. Extend `/resolve` to the agent only if
  resolving by hand proves tedious in real use.

## Staged implementation plan

Each item is one PR. `just check` gates each.

0. **Remove the verdict from the current server.** Drop the `Approve`
   button, the `approved` field in the `--out` payload, the
   `REVIEW APPROVED` stdout header and its push-the-PR instruction, and the
   `Approved ✓` button state. The finish dialog keeps one button,
   `Submit review`. Touches `scripts/local-review/server.py`,
   `skills/local-review/SKILL.md` (the step-5 echo line drops its verdict
   field), `dev_docs/local-review.md`, `scripts/test-local-review.sh`.
   Tests: the payload carries no `approved` key; stdout prints
   `REVIEW SUBMITTED` and no `APPROVED:` line; the existing one-shot cases
   pass with the verdict assertions removed.
1. **Server protocol core.** Mode derivation (`--out` default moves to
   `None`; explicit `--out` without `--once` selects threads mode;
   human-only mode keeps writing `pr_comments.json`), thread store + lock,
   id minting and `round` in `/submit`, `GET /threads`, `POST /reply`,
   `POST /resolve`, `threads_rev` in `/state`, finish-shuts-down.
   Full-state `threads` in `--out`. Re-label and extend the four
   `--out`-without-`--once` harness cases named under Mode derivation:
   they now start threads-mode servers, and their payload assertions gain
   the `round`/`id`/`threads` keys. Touches
   `scripts/local-review/server.py`, `scripts/test-local-review.sh`. Tests:
   one-shot payload matches the post-stage-0 shape under `--once`; human-only
   submit still
   writes `pr_comments.json` with the one-shot shape; threaded round trip
   (submit → ids in OUT → reply → `/threads` shows it → `threads_rev`
   bumped); resolve round trip; 404 on `/reply` in `--once` mode, in
   human-only mode, and without the token; Origin rejection on `/reply`;
   second submit accepted after a `Submit` round; `Finish` exits the
   process.
2. **Thread UI.** Reply rendering under chips, Reply and Resolve controls,
   resolved-strip with Reopen, submit re-arm, round indicator, `/state`
   → `/threads` fetch on rev change. Touches `server.py` (PAGE template),
   `scripts/test-local-review.sh`. Tests: a live-server case drives the
   endpoints in the browser's order and asserts the `/threads` render input;
   a source-scan check that reply text lands via `textContent` only (extends
   the existing innerHTML scan).
3. **Anchor re-placement across `/refresh`.** Pure `placeThreads()` in the
   PURE region, Outdated strip, "moved" marker. Touches `server.py`,
   `scripts/test-local-review.sh`. Tests: node corpus for the three
   placement rules (exact, unique-code relocation, outdated); a git-fixture
   case that edits the file, refreshes, and asserts threads survive.
4. **Skill and docs.** SKILL.md step 2/4 changes, new
   `skills/local-review/references/threads.md`, `dev_docs/local-review.md`
   contract section update, changelog via PR title. Touches those three
   files. Tests: `scripts/validate.py` (body under cap, no stale paths);
   README counts unchanged — no new skill or command.

Ordering: 0 is independent, but landing it first keeps stage 1 from having
to describe two verdict models. 1 must land before 2; 2 before 3 (the strip
is UI). 4 depends on 1-3 in substance and lands last, so the skill never
documents behavior that is not yet merged. None of this blocks or is
blocked by work elsewhere in the repo.
