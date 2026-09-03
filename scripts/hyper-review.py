#!/usr/bin/env python3
"""hyper-review.py — one-shot code review through the Charm Hyper gateway.

co-review's `hyper` reviewer. Reads the assembled review input (rubric,
optional requests, diff) on stdin, sends it as one chat completion to
https://hyper.charm.land/v1, and prints the model's reply verbatim on stdout.
Nothing else reaches stdout, so the caller can hand the bytes straight to the
reconciler. One `HYPER_REVIEW:` status line goes to stderr on every exit.

The script has no filesystem access beyond stdin, runs no commands, and keeps
no state, so it inherits none of the hazards the CLI reviewers carry (see
skills/co-review/reviewers/hyper.md).

Usage:
  cat "<INPUT>" | hyper-review.py [--model ID] [--effort LEVEL]
                                  [--max-tokens N] [--timeout SECONDS]

Credential: `HYPER_API_KEY` from the environment, else `HYPER_API_KEY_REF`
resolved through commands/handlers/assets/_secret_resolve.py (the shared
contract in dev_docs/auth_key_access.md). The key is never accepted in argv
and never printed.

Exit codes:
  0  a response arrived (any finish_reason — check the status line for
     `finish_reason=length`, which means the review was cut at --max-tokens)
  2  usage error, or no credential configured
  3  authentication rejected (HTTP 401/403), or the credential resolver failed
  4  rate limited (HTTP 429) after retries
  5  Hypercredits exhausted (HTTP 402)
  6  other HTTP or network failure
  7  no response within --timeout
  8  empty stdin

Verified against Hyper's published docs (/docs/llms-full.txt, 2026-09-02):
401 for a missing or bad key, 402 `billing_error` for exhausted credits, 429
`rate_limit_error`, 5xx `server_error`, and `usage.cost.hypercredits` /
`usage.remaining.hypercredits` on every completion. The effort field is sent
as OpenAI's `reasoning_effort`; Hyper documents that "all standard parameters
are accepted" but does not name this one, and it was not confirmed with an
authenticated call.
"""

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = "https://hyper.charm.land/v1"

# The shared <POINTER> from skills/co-review/SKILL.md — byte-identical to the
# one codex and copilot receive, so every stateless reviewer gets the same
# framing. It goes in the system message; the assembled input is the user turn.
POINTER = (
    "Review ONLY the rubric and diff on stdin. Do NOT explore the filesystem, "
    "run commands, or retrieve any prior conversation or memory. If stdin is "
    "empty, output exactly NO INPUT and stop. Output findings as file:line, "
    "the issue, and a suggested fix. Read only."
)

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_AUTH = 3
EXIT_RATE_LIMITED = 4
EXIT_CREDITS = 5
EXIT_HTTP = 6
EXIT_TIMEOUT = 7
EXIT_EMPTY = 8

RATE_LIMIT_RETRIES = 2
SERVER_ERROR_RETRIES = 1


def status(state, **fields):
    parts = [f"HYPER_REVIEW: {state}"]
    parts.extend(f"{k}={v}" for k, v in fields.items() if v is not None)
    print(" ".join(parts), file=sys.stderr, flush=True)


def load_key():
    """Resolve HYPER_API_KEY via the shared secret ladder.

    The resolver module lives beside the handler assets. A distribution that
    ships this script without it (a claude.ai zip) still works from a plain
    `$HYPER_API_KEY`.
    """
    key = os.environ.get("HYPER_API_KEY", "").strip()
    if key:
        return key
    assets = Path(__file__).resolve().parent.parent / "commands" / "handlers" / "assets"
    sys.path.insert(0, str(assets))
    try:
        from _secret_resolve import SecretUnavailable, resolve_key
    except ImportError:
        status("no-credential", reason="HYPER_API_KEY unset and no resolver module")
        sys.exit(EXIT_USAGE)
    try:
        return resolve_key("HYPER_API_KEY")
    except SecretUnavailable as exc:
        if exc.category == "unconfigured":
            status(
                "no-credential",
                reason="HYPER_API_KEY unset and HYPER_API_KEY_REF unset",
            )
            sys.exit(EXIT_USAGE)
        status("resolver-failed", category=exc.category)
        sys.exit(EXIT_AUTH)


def error_message(body, key):
    """The error text from a Hyper error body, whichever shape it takes.

    Docs show `{"error": {"message": ..., "type": ...}}`; the live 401 on
    2026-09-02 returned `{"error": "missing authorization"}`. Accept both, and
    never let the key itself leak through a quoted request.
    """
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = body[:200] if isinstance(body, str) else ""
    else:
        err = data.get("error", data) if isinstance(data, dict) else data
        if isinstance(err, dict):
            text = f"{err.get('type', '')}: {err.get('message', '')}".strip(": ")
        else:
            text = str(err)
    return text.replace(key, "<redacted>").replace("\n", " ")[:200]


def post(payload, key, timeout):
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8", errors="replace")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", default="deepseek-v4-pro")
    ap.add_argument(
        "--effort",
        default=None,
        help="reasoning effort level; omit for models without one",
    )
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument(
        "--timeout", type=float, default=600.0, help="overall deadline in seconds"
    )
    args = ap.parse_args()

    body = sys.stdin.read()
    if not body.strip():
        status("empty-input", model=args.model)
        return EXIT_EMPTY

    key = load_key()

    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": POINTER},
            {"role": "user", "content": body},
        ],
        "max_tokens": args.max_tokens,
        "stream": False,
    }
    if args.effort:
        payload["reasoning_effort"] = args.effort

    deadline = time.monotonic() + args.timeout
    rate_retries = RATE_LIMIT_RETRIES
    server_retries = SERVER_ERROR_RETRIES
    backoff = 2.0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            status("timeout", model=args.model, timeout=args.timeout)
            return EXIT_TIMEOUT
        try:
            _, text = post(payload, key, remaining)
            break
        except urllib.error.HTTPError as exc:
            code = exc.code
            msg = error_message(exc.read().decode("utf-8", errors="replace"), key)
            if code in (401, 403):
                status("auth-rejected", model=args.model, http=code, error=repr(msg))
                return EXIT_AUTH
            if code == 402:
                status(
                    "credits-exhausted", model=args.model, http=code, error=repr(msg)
                )
                return EXIT_CREDITS
            if code == 429 and rate_retries > 0:
                rate_retries -= 1
            elif code >= 500 and server_retries > 0:
                server_retries -= 1
            elif code == 429:
                status("rate-limited", model=args.model, http=code, error=repr(msg))
                return EXIT_RATE_LIMITED
            else:
                status("http-error", model=args.model, http=code, error=repr(msg))
                return EXIT_HTTP
            time.sleep(min(backoff, max(0.0, deadline - time.monotonic())))
            backoff *= 2
        except (socket.timeout, TimeoutError):
            status("timeout", model=args.model, timeout=args.timeout)
            return EXIT_TIMEOUT
        except urllib.error.URLError as exc:
            reason = str(exc.reason).replace(key, "<redacted>")
            if "timed out" in reason:
                status("timeout", model=args.model, timeout=args.timeout)
                return EXIT_TIMEOUT
            status("network-error", model=args.model, error=repr(reason[:200]))
            return EXIT_HTTP

    try:
        data = json.loads(text)
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
        finish = choice.get("finish_reason")
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        status(
            "bad-response",
            model=args.model,
            error=repr(text[:200].replace(key, "<redacted>")),
        )
        return EXIT_HTTP

    usage = data.get("usage") or {}
    sys.stdout.write(content)
    if content and not content.endswith("\n"):
        sys.stdout.write("\n")
    sys.stdout.flush()
    status(
        "ok",
        model=args.model,
        finish_reason=finish,
        **{"in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens")},
        cost=(usage.get("cost") or {}).get("hypercredits"),
        remaining=(usage.get("remaining") or {}).get("hypercredits"),
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
