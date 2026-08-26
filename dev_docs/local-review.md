# local-review — design and contracts

The `local-review` skill is the plugin's human-in-the-loop review surface: a
local, stdlib-only web server renders a GitHub-style split diff, the user
leaves inline line comments in their browser, and the submitted
round lands in a JSON file the agent acts on. `/co-review` is agents reading a
diff; `local-review` is the human reading it. This doc is the durable record of
the port (PRs #369–#376, 2026-08-18); the plan scaffolding that produced it has
been deleted per the repo's plan lifecycle.

## Provenance — why the code looks the way it does

`scripts/local-review/server.py` vendors **CaseyDiff** by Casey Rogers
(<https://github.com/caseycrogers>; no upstream repository exists — attribution
lives in `scripts/local-review/NOTICE`, and the vendored highlight.js carries
its full BSD-3-Clause text in `scripts/local-review/vendor/LICENSE`).

The port deliberately landed the file **verbatim** and hardened it in small,
reviewable deltas, instead of rewriting it into this repo's idiom. That is why
it sits on the **default** mypy tier (unannotated) and why
`scripts/local-review/**` is dprint-excluded. Before "cleaning it up," know
what that spends: the diffability against Casey's original, and the review
history that anchored every hardening change to a small diff.

## Architecture — five stages, one file

1. **Acquire** — `get_diff()`: `--diff-file`, `--git <spec>` (via
   `_git_diff_args()`, one helper mapping `uncommitted` → `git diff HEAD`, a
   single ref → `git diff <ref>...HEAD`, and an explicit `A...B` range
   straight through), or `gh pr diff`. `main()` pins the repo once at startup
   with `git rev-parse --show-toplevel`, and every later call re-runs `git -C
   <dir> diff ...` against that pinned dir instead of caching the result —
   but only `uncommitted` is a live *worktree* source: `/state` and
   `/refresh` see worktree edits under that spec because `git diff HEAD`
   re-reads the working tree every time. A ref or an `A...B` range diffs
   commit-to-commit, so re-running it only surfaces something new when the
   refs themselves move (a new commit, a rebase) — a worktree edit alone
   doesn't. `sh()` converts every subprocess failure (nonzero _or_ missing
   binary) into `RuntimeError`; `main()` reports one line on stderr and exits
   1 — this covers a non-repo cwd or bad `--git` spec the same way it covers
   a gh failure. `get_meta()`/`resolve_gh()` propagate gh failures too — PR
   mode fails loud, never degrades silently. Exactly one of PR number,
   `--diff-file`, `--git` must be given; `main()` validates this and exits 2
   otherwise.
2. **Parse** — `parse_diff()`: unified diff → `files → hunks → rows`, pairing
   pending `-`/`+` runs into left/right cells. Also accepts **headerless**
   `---`/`+++` patches (hunk extents tracked from the `@@` counts, so a deleted
   line starting `--` is not a file boundary) and **git-quoted** headers
   (full C escape set, octal only when all three digits are octal).
   `GENERATED_PATTERNS` marks lockfiles, codegen, minified bundles, snapshots,
   and vendored trees for auto-collapse.
3. **Render** — `build_page()` substitutes the parsed JSON into the `PAGE`
   template. Escaping is load-bearing here: `esc_py` covers quotes, and
   `_json_for_script()` escapes `<` and U+2028/9 so a fork-PR diff line
   containing `</script>` cannot execute in the page.
4. **Serve** — `ThreadingHTTPServer` on `127.0.0.1`, state on `Handler` class
   attributes. Routes live under a per-launch token (below).
5. **Hand off** — `/submit` writes `--out` atomically; the agent polls that
   file and posts any GitHub-flagged comments itself. The server holds no
   GitHub write capability (#381).

## View modes

Each file renders in one of three per-file modes — `split`, `single`,
`preview` — picked by heuristic and overridable from a segmented control in the
file header. `parse_diff()` supplies the fact the heuristic reads:
`file["single"]` is `"r"` when the diff deletes nothing, `"l"` when it adds
nothing, and `None` when both sides are live. Defaults: one-sided → `single`,
everything else → `split`; wholly-added markdown → `preview`.

`preview` renders a wholly-added markdown file as a document. It uses
`marked`'s **lexer** only and builds DOM nodes from the token stream, so no
attacker-derived string ever reaches an HTML parser and no sanitizer exists to
be bypassed — `marked` deliberately does not sanitize, which is why the
originally-planned "disable raw HTML and allowlist the output" was not
buildable. `describe()` is a pure token-tree → description-tree function with
no DOM, which is what makes the security property testable under bare `node`;
`materialize()` adds no logic. Comments anchor to leaf blocks — a list item, a
table row — and carry `kind: "block"` with `endLine`. A **labelled** fence is
highlighted through the same structural token-tree path the diff view uses
(#385); an unlabelled or unknown-labelled one renders plain rather than reach
`highlightAuto`, which would run all 36 vendored grammars over a body a fork
PR chooses — 2.7s of synchronous work for 512 KiB, against 248ms for the same
body labelled. A per-fence size cap would not fix that, because the cost is
additive across fences. The server sends
`Referrer-Policy: no-referrer` because the token is in the URL (see #386), and
the whole design including eight corrections to its first draft is in
[`designs/local-review-markdown-preview.md`](designs/local-review-markdown-preview.md).

`single` emits a genuine two-column grid rather than hiding two of four, so
`insertAfterRow()` reads the column count from `grid.dataset.cols` instead of
assuming 4. It is offered **only** when one side is empty; a mixed
modification never gets it. Overrides live in `viewModes` and survive
`/refresh` (comments do not — refresh discards those on purpose); an override
that stops being legal falls back to the default. Collapse and "Viewed" live in
`fileUi` for the same reason: a mode switch re-renders one file via
`renderFile()`, which also re-materialises saved comment chips from the
`comments` store so the submit counter never disagrees with the page.

The reasoning, and the two constraints that look arbitrary from outside, are in
[`decisions/local_review_view_modes.md`](decisions/local_review_view_modes.md).

## The agent contract

### Modes

There is no mode flag; the server derives the mode from two arguments it
already takes. `--out`'s argparse default moved from `pr_comments.json` to
`None`, so "given" means "given explicitly":

| Launch shape             | Mode       | Behavior                                                                         |
| ------------------------ | ---------- | -------------------------------------------------------------------------------- |
| `--out`, no `--once`     | threads    | stays alive across rounds; thread endpoints and thread UI on                     |
| `--out` with `--once`    | one-shot   | one round, write `--out`, exit — today's skill launch, pre-stage-1 payload shape |
| no `--out`, no `--once`  | human-only | hand-run behavior; no thread endpoints, no Reply/Resolve controls                |
| no `--out` with `--once` | human-only | one round, write `pr_comments.json`, exit                                        |

`--once` always means "exit after one successful submit," in every mode.
Only an explicit `--out` with no `--once` keeps the server alive.

- **Startup:** the server prints `LOCAL_REVIEW_URL=http://127.0.0.1:<port>/<token>/`
  on its own line — the machine contract, unchanged. Port defaults to the
  stable `8765`; if that's busy, `bind_server()` falls back to autoselect
  (bind 0) and logs the fallback to stderr. An explicit `--port` binds exactly
  as asked (its `OSError` propagates rather than falling back). The token is
  now four hyphenated words drawn from a 1024-word list (40 bits of entropy) —
  a deliberate reduction from the prior 128-bit `secrets.token_urlsafe(16)`,
  traded for a URL a human can read and retype; both forms are `secrets`-based
  and the token remains the authorization. A second, human-facing line —
  `Review UI: http://review.localhost:<port>/<token>/ …` — shows the same
  token under the vanity `review.localhost` host, which `*.localhost` browsers
  resolve straight to loopback (RFC 6761); `LOCAL_REVIEW_URL` stays the
  machine-parsed line either way. Use the URL verbatim — never rebuild it from
  the port; the token is the authorization. Poll the log for the
  `LOCAL_REVIEW_URL=` line with a bound (the skill shows the loop); a dead
  process means startup failed and the log has the details — gh failures, a
  bad `--git` spec, and a non-repo cwd are all normalized to a one-line
  `error:` (via `sh()`'s `RuntimeError`); other failures (an unreadable
  `--diff-file`, an occupied explicit `--port`) surface as a traceback.
- **`--once`:** the server exits after a successful submit. When passed with
  `--out`, it selects one-shot mode (see Modes above); the skill no longer launches with it by
  default — an explicit `--out` with no `--once` is what the skill passes,
  selecting threads mode instead. `--once` remains available as an explicit
  one-round opt-out, keeping the pre-stage-1 payload shape. The recorded PID
  is cleanup only for an abandoned session (one the user never finishes).
- **`--out`:** written via temp file + `os.replace` — a poller checking
  `[ -s "$OUT" ]` never sees a torn payload. It is now the **last** thing
  `/submit` does, and nothing outward happens after it: the server no longer posts to
  GitHub, so the old ordering constraint (post first, because OUT's appearance
  is the caller's kill signal) and its accepted disk-exhaustion residual are
  both gone.
- **`meta.sha`:** in PR mode, the head SHA `resolve_gh()` pinned at launch —
  the commit this page's diff was rendered from. The agent posts against it;
  re-resolving the head at posting time would anchor comments to whatever the
  PR advanced to while the page was open.
- **Payload:** `{meta, summary, comments:[{file, side, line, code,
  text}]}`. A `kind: "dismiss-comments"` entry adds `endLine` (delete lines
  `line`–`endLine` on `side`); `kind: "block"` adds `endLine` too (a preview
  comment about that whole span). `github: true` marks a comment the **user
  wants on the PR** — the agent posts it; the server never does.
  `code` is the anchor if line numbers shifted. `github_posted` /
  `github_failed` no longer exist. This is the one-shot and human-only
  shape; threads mode adds `round`, per-comment `id`, the full-state
  `threads` array, and `finished` — see Round protocol below.
- **Submission slot:** concurrent `/submit`s get 409; the slot releases before
  the response on a stay-alive server (CI caught the after-response release as
  a spurious sequential 409 — PR #374), and a completed `--once` submission
  keeps it.

### Round protocol (threads mode)

Each submit is a **round**, numbered from 1 by the server. The server mints
each new comment's id (`t1`, `t2`, … a per-launch counter) at submit time,
not the browser — before submit a comment is a browser-only draft with no
identity, and after submit the server owns the thread store, so it is the
natural authority for the keys into it. `--out`'s `comments` array keeps its
one-shot meaning (the entries new in this round) plus the minted `id`;
`threads` carries the complete state, resolved threads included, so the
previous round's file can be overwritten with nothing lost. `finished: true`
marks the round the server writes just before Finish shuts it down; no
reply channel exists once that round is read, since the server has already
exited.

New endpoints, active only in threads mode (`--out` without `--once`; they
404 under one-shot and human-only): `GET /threads` (full thread state, for
render and reload), `POST /reply` (agent and browser append a reply to a
thread; `author` is `"agent"` or `"user"`, a label rather than an
authenticated identity, since both callers share one bearer token), and
`/resolve` (browser only — sets or clears a thread's `resolved` bit;
resolve is the user's click, and the agent never calls it). `GET /state`
gains a `threads_rev` integer that increments on every store mutation; the
page diffs it against the value it last rendered and fetches `/threads`
only on change. A submit's response and stdout block also carry the count
of replies posted since the previous round, so a reply-only round does not
read as "Submitted 0 comment(s)". Full detail — schemas, anchor
re-placement across `/refresh`, resolved-thread semantics — is in
`skills/local-review/references/threads.md`.

## Threat model — loopback is not a trust boundary

Binding `127.0.0.1` does not protect against the user's own browser: any web
page can POST to localhost, and `/submit` writes the file the agent acts on.

**What `/submit` can no longer do is post to GitHub.** It used to, through the
user's authenticated `gh`, which made every capability of this handler reachable
by any page the user had open — and a `gh` post is not undoable, unlike the file
write. The **Comment on GitHub** button now only records `github: true`; the
agent reads that and posts, where the write appears in the transcript and can be
interrupted. `scripts/test-local-review.sh` asserts the absence rather than
trusting it: a `gh` stand-in logs every invocation during a real PR-mode submit
of a flagged comment, and the log must contain no write. Issue #381.

The remaining defences, for the file write and for reads: every route mounts
under a path segment of four `secrets.choice()`-drawn words
from the 1024-word `WORDLIST` (bare 404 otherwise, slashless alias 301s),
POSTs reject any foreign `Origin` — the allowlist is `127.0.0.1`, `localhost`,
and the port-scoped vanity `http://review.localhost:<port>` only, so an
arbitrary `*.localhost` origin (e.g. `evil.localhost`) is still rejected — and
`Sec-Fetch-Site: cross-site`, and the vendor route's
`[\w.\-]+\.js` fullmatch blocks traversal. The token is per-launch; showing it
to the user is fine — it dies with the server.

**The token leaves the URL after the first hit (#386).** A `GET /<token>/`
302s to `/`, setting a `HttpOnly`, `SameSite=Strict` session cookie named
`local_review_<port>=<token>` and stripping the token from `Location`. The
browser then rests on the bare `/`, so history and `Referer` no longer
disclose the token; `_route_path()` accepts either the token path prefix
or the matching cookie, so the token URL itself stays valid as a bearer
credential. Every route under it — `GET /<token>/state`,
`POST /<token>/submit`, `/<token>/vendor/*` — and the machine
`LOCAL_REVIEW_URL` contract are unchanged. The page root is the one
exception: a client that fetches `/<token>/` gets the 302, so it must follow
redirects _and_ retain the cookie. `curl` without `-L` stops at the 302;
`curl -L` or `urllib.request.urlopen()` without a cookie jar lands on a 404.
That is what `open_token_url()` in `scripts/test-local-review.sh` exists for.
The redirect must be 302, not 301 — a browser caches a 301
forever, which would break a later review bound to the same port. The
cookie name embeds the port because cookies are not port-scoped (RFC 6265):
`127.0.0.1:8765` and `:8766` share one jar for host `127.0.0.1`, so naming
the cookie after the port is what keeps two concurrent reviews from
clobbering each other's session. The name fixes the collision, not the
exposure: the cookie is host-scoped, so the browser now sends the token to
every other service it talks to on `127.0.0.1`, where before the token only
ever appeared in requests to this server's own tokenized paths. Accepted —
those services are the user's own, and the token dies with the server. There
is no fix to reach for: cookies have no port scope, and a `__Host-` prefix
would require `Secure`, hence HTTPS.

**The page no longer parses attacker-derived HTML anywhere (#385).** It used to
in one place: highlight.js was asked for an HTML string and the result was
assigned to a diff cell's `innerHTML`. hljs escapes its input, so this was
believed safe — but the belief rested on a vendored escaper being correct over
content a fork PR chooses, not on a structural property. The same
`hljs.highlight()` call also builds a **token tree** (`_emitter.rootNode`), and
that is what the page reads now: `hlNodes()` turns it into the same
`{tag, text, attrs, kids}` descriptors preview already used, `materialize()`
creates `<span>`s and assigns source text with `textContent`, and the diff view
and preview share that one path — which is why fenced code in preview is
highlighted again rather than deliberately plain — for a **labelled** fence;
auto-detection stays refused there on cost grounds, per **View modes** above.
Whole-line comment detection
(the ⊘ affordance) walks the same tree instead of re-parsing hljs's output.
With the last interpolation gone the page carries **no HTML-escaping helper at
all**; every diff-derived value — file paths, `@@` headers, section labels —
and the reviewer's own comment text now land through `textContent`.
`scripts/test-local-review.sh` asserts both halves: hostile diff lines
(`<img onerror=…>`, `</script>`, encoded variants) must round-trip byte for
byte through `hlNodes()` as spans of text, which a parse could not do, and a
source scan fails the build if any `innerHTML` assignment interpolates a
diff-derived name or the escaper returns. What remains on `innerHTML` is
developer-authored markup with no interpolation of untrusted content.

## Testing

`scripts/test-local-review.sh` (wired into `scripts/check.sh`) runs ~270
checks from **one** python3 harness invocation (interpreter startup dominates
suite time at this repo's scale) — the harness itself spawns real server
subprocesses for the live cases: parse_diff fixtures, server
cases (`--once` round trip, port autoselect, gh-failure propagation, security
gates, disconnect, submission races), and `--git` cases against throwaway git
fixture repos (`GIT_CONFIG_GLOBAL`/`GIT_CONFIG_SYSTEM` pinned to `/dev/null`
so a machine's global git config can't leak in): uncommitted-mode startup,
live refresh via `/refresh`, branch-vs-ref mode, a non-repo cwd, and an
invalid spec — all normalized to the same one-line `error:` contract. Two
Bash 3.2 traps it documents: a quoted heredoc containing an apostrophe breaks
inside `$(...)`, and `TextIOWrapper` buffering defeats `select()` on the
stream (read the raw fd).

## Deferred on purpose

- **`/co-review` / `/deliver-task` integration** — wiring the human round into
  the review pipeline was scoped out until the standalone skill had real use.
- ~~**`langForFile`'s Dockerfile special case**~~ Fixed: the filename special
  cases now route through the same `hljs.getLanguage` guard as the extension
  map, so a Dockerfile falls back to `highlightAuto` instead of the caught
  error rendering plain text. (`makefile` still highlights natively — that
  grammar is in the bundle.)
