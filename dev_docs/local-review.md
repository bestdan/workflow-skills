# local-review — design and contracts

The `local-review` skill is the plugin's human-in-the-loop review surface: a
local, stdlib-only web server renders a GitHub-style split diff, the user
leaves inline line comments and a verdict in their browser, and the submitted
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
5. **Hand off** — `/submit` posts GitHub-flagged comments first, then writes
   `--out` atomically; the agent polls that file.

## The agent contract

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
- **`--once`:** the server exits after a successful submit. The skill launches
  with it; the recorded PID is only cleanup for an abandoned round.
- **`--out`:** written via temp file + `os.replace` — a poller checking
  `[ -s "$OUT" ]` never sees a torn payload. The write happens **after** the
  GitHub-posting loop, so OUT's appearance means posting finished, and the
  payload carries `github_posted`/`github_failed`. Accepted residual: disk
  exhaustion between the preflight and the write can still fail after posting
  (the posting outcome is part of the payload, so it cannot be written first).
- **Payload:** `{meta, summary, approved, comments:[{file, side, line, code,
  text}]}`, plus — in PR mode only — `github_posted` (list of comment URLs)
  and `github_failed` (list of `{file, line, error}`). A
  `kind: "dismiss-comments"` entry adds `endLine` (delete lines
  `line`–`endLine` on `side`); `github: true` marks a comment the server also
  posted to the PR. `code` is the anchor if line numbers shifted.
- **Submission slot:** concurrent `/submit`s get 409; the slot releases before
  the response on a stay-alive server (CI caught the after-response release as
  a spurious sequential 409 — PR #374), and a completed `--once` submission
  keeps it.

## Threat model — loopback is not a trust boundary

Binding `127.0.0.1` does not protect against the user's own browser: any web
page can POST to localhost, and `/submit` writes the file the agent acts on —
in PR mode it posts to GitHub through the user's authenticated `gh`. Hence:
every route mounts under a path segment of four `secrets.choice()`-drawn words
from the 1024-word `WORDLIST` (bare 404 otherwise, slashless alias 301s),
POSTs reject any foreign `Origin` — the allowlist is `127.0.0.1`, `localhost`,
and the port-scoped vanity `http://review.localhost:<port>` only, so an
arbitrary `*.localhost` origin (e.g. `evil.localhost`) is still rejected — and
`Sec-Fetch-Site: cross-site`, and the vendor route's
`[\w.\-]+\.js` fullmatch blocks traversal. The token is per-launch; showing it
to the user is fine — it dies with the server.

## Testing

`scripts/test-local-review.sh` (wired into `scripts/check.sh`) runs ~185
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
