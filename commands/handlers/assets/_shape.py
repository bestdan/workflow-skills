"""Read a field out of a decoded JSON payload, or fail with a message.

Every asset here talks to an API and then indexes the result. Done bare —
`payload["data"]["repository"]["issues"]` — a malformed or unexpected response
surfaces as a `KeyError` or `TypeError` traceback, which tells a user running a
handler nothing about what went wrong and tells the calling agent even less.

`expect()` turns that into one sentence naming the field, the type wanted, and
the type that arrived.

It also does something no `isinstance` chain does for free: because it returns
`T`, every type checker narrows the result at the call site with no `TypeGuard`,
no `typing_extensions`, and no annotation on the caller. That is the whole
reason this is a function returning a value rather than a `check_shape()`
that returns None.

Stdlib only and 3.9-clean on purpose — these assets are executed as bare
`python3` on consumers' machines, and `scripts/typecheck.sh` pins the asset tier
to `--python-version 3.9` to keep it that way.

Usage:
    data = expect(payload, "data", dict, "response")
    nodes = expect(conn, "nodes", list, "response.data.issues")
    total = expect(sub, "totalCount", int, f"issue #{number}.subIssues")
"""

from typing import Any, Type, TypeVar

T = TypeVar("T")


class ShapeError(Exception):
    """A payload did not have the shape the caller requires.

    Callers convert this into whatever their own failure channel is — a
    `sys.exit` message for the linear assets, a `LookupFailed` carrying
    `ROLLUP_REASON` for gh-issue-rollups.
    """


def expect(obj: Any, key: str, typ: Type[T], where: str) -> T:
    """Return `obj[key]` when it is a `typ`, else raise ShapeError.

    `where` names the container in the message, so a failure three levels deep
    says which level it was.

    A `bool` is rejected where an `int` is wanted. Python makes `bool` a
    subclass of `int`, so a plain isinstance check accepts `True` as a count —
    the same class of confusion that let a *string* `totalCount` pass a `> 0`
    comparison in the shell block this directory's rollup helper replaced.
    """
    if not isinstance(obj, dict):
        raise ShapeError(f"{where}: expected an object, got {type(obj).__name__}")
    if key not in obj:
        raise ShapeError(f"{where}.{key}: missing")
    val = obj[key]
    if typ is int and isinstance(val, bool):
        raise ShapeError(f"{where}.{key}: expected int, got bool")
    if not isinstance(val, typ):
        raise ShapeError(
            f"{where}.{key}: expected {typ.__name__}, got {type(val).__name__}"
        )
    return val
