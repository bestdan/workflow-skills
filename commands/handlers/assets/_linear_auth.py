"""Frame a Linear credential the way Linear expects for the kind of key it is.

A personal API key (`lin_api_…`) goes in the Authorization header BARE — most
linear assets here hardcode that, and adding a scheme breaks it. An OAuth access
token does not: it is a Bearer token, and Linear rejects it unframed.

That distinction stopped being academic once a `client_credentials` grant became
the supported way to authenticate automation with no browser, because the same
`$LINEAR_API_KEY` slot can now hold either kind. `linear-export.py` was the first
asset that could be handed either and grew the rule; this is that one rule with
one home, so the second asset to need it cannot drift from the first.

Getting it wrong surfaces as an authentication error, which reads like a bad key
rather than a correctly-valued key in the wrong envelope — so the framing is
decided from the key's own shape, never from a flag the caller has to know to
pass.

Stdlib only and 3.9-clean on purpose — these assets are executed as bare
`python3` on consumers' machines, and `scripts/typecheck.sh` pins the asset tier
to `--python-version 3.9` to keep it that way.

Usage:
    headers = {"Authorization": auth_header(key)}
"""


def auth_header(key: str) -> str:
    """The Authorization header value for `key`, framed by its own shape."""
    if key.startswith("Bearer ") or key.startswith("lin_api_"):
        return key
    return "Bearer " + key
