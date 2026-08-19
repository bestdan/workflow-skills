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

1. **Acquire** — `get_diff()`: `--diff-file` or `gh pr diff`. `sh()` converts
   every gh failure (nonzero _or_ missing binary) into `RuntimeError`;
   `main()` reports one line on stderr and exits 1. `get_meta()`/`resolve_gh()`
   propagate gh failures too — PR mode fails loud, never degrades silently.
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
  on its own line. Port is autoselected (bind 0) unless `--port` is given. Use
  the URL verbatim — never rebuild it from the port; the token is the
  authorization. Poll the log for the line with a bound (the skill shows the
  loop); a dead process means startup failed and the log's `error:` line says
  why.
- **`--once`:** the server exits after a successful submit. The skill launches
  with it; the recorded PID is only cleanup for an abandoned round.
- **`--out`:** written via temp file + `os.replace` — a poller checking
  `[ -s "$OUT" ]` never sees a torn payload. The write happens **after** the
  GitHub-posting loop, so OUT's appearance means posting finished, and the
  payload carries `github_posted`/`github_failed`. Accepted residual: disk
  exhaustion between the preflight and the write can still fail after posting
  (the posting outcome is part of the payload, so it cannot be written first).
- **Payload:** `{meta, summary, approved, comments:[{file, side, line, code,
  text}]}`. A `kind: "dismiss-comments"` entry adds `endLine` (delete lines
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
every route mounts under a `secrets.token_urlsafe(16)` path segment (bare 404
otherwise, slashless alias 301s), POSTs reject foreign `Origin` and
`Sec-Fetch-Site: cross-site`, and the vendor route's `[\w.\-]+\.js` fullmatch
blocks traversal. The token is per-launch; showing it to the user is fine — it
dies with the server.

## Testing

`scripts/test-local-review.sh` (wired into `scripts/check.sh`) runs ~160
checks in **one** python3 process: parse_diff fixtures, real-subprocess server
cases (`--once` round trip, port autoselect, gh-failure propagation, security
gates, disconnect, submission races). Two Bash 3.2 traps it documents: a
quoted heredoc containing an apostrophe breaks inside `$(...)`, and
`TextIOWrapper` buffering defeats `select()` on the stream (read the raw fd).

## Deferred on purpose

- **`/co-review` / `/deliver-task` integration** — wiring the human round into
  the review pipeline was scoped out until the standalone skill had real use.
- **`langForFile`'s Dockerfile/makefile special case** names a grammar the
  vendored bundle lacks — harmless (the `hljs.getLanguage` guard falls back to
  auto-detection), noted here so nobody rediscovers it as a bug.
