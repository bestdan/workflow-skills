#!/usr/bin/env python3
"""Installation-token minter — the MAINTAINER-side broker stand-in (§2.1).

Runs as the maintainer (the only identity that can read the App private key),
App-authenticates with a short-lived JWT, and mints a scoped installation token.
In production this is the fixed-config broker; here it is a disposable spike
stand-in (rule 4 — never promoted by renaming).

The private key is read but NEVER printed or persisted. `token` prints ONLY the
installation token to stdout so it can be piped straight into the agent-side
driver's stdin (never argv, never env, never a file).

Run via uv so PyJWT/cryptography need not be installed globally:

    APP_ID=... KEY_PATH=... uv run --with 'pyjwt[crypto]' mint.py list-installations
    APP_ID=... KEY_PATH=... INSTALLATION_ID=... uv run --with 'pyjwt[crypto]' mint.py token
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

import jwt  # provided by `uv run --with 'pyjwt[crypto]'`

API = "https://api.github.com"


def make_jwt(app_id, key_path):
    with open(key_path) as f:
        key = f.read()
    now = int(time.time())
    # iat backdated 60s for clock skew; exp 9 min (< GitHub's 10 min ceiling).
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": str(app_id)},
                      key, algorithm="RS256")


def api(method, path, bearer, data=None):
    req = urllib.request.Request(API + path, method=method)
    req.add_header("Authorization", f"Bearer {bearer}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.data = json.dumps(data).encode()
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"HTTP {e.code} {path}: {e.read().decode()}\n")
        raise


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "jwt"
    app_id = os.environ["APP_ID"]
    key_path = os.environ["KEY_PATH"]
    j = make_jwt(app_id, key_path)

    if cmd == "jwt":
        print(j)
    elif cmd == "list-installations":
        insts = api("GET", "/app/installations", j)
        # non-secret metadata only
        print(json.dumps([{"id": i["id"], "account": i["account"]["login"],
                           "repo_selection": i["repository_selection"]}
                          for i in insts], indent=2))
    elif cmd == "installation-repos":
        inst = os.environ["INSTALLATION_ID"]
        tok = api("POST", f"/app/installations/{inst}/access_tokens", j)["token"]
        repos = api("GET", "/installation/repositories", tok)
        print(json.dumps({"repo_count": repos["total_count"],
                          "repos": [r["full_name"] for r in repos["repositories"]]},
                         indent=2))
    elif cmd == "token":
        inst = os.environ["INSTALLATION_ID"]
        tok = api("POST", f"/app/installations/{inst}/access_tokens", j)
        sys.stdout.write(tok["token"])  # ONLY the token, no newline noise
    else:
        sys.exit(f"unknown command: {cmd}")


if __name__ == "__main__":
    main()
