#!/usr/bin/env python3
"""Every linear asset's gql() must fail with a message, not a traceback.

All five `linear-*.py` assets share one seam: `gql()` posts a query and unwraps
the reply. Until recently each did a bare `return payload["data"]`, so a
malformed or unexpected response surfaced as a `KeyError` — and then as a chain
of further `KeyError`s at every caller that indexed the result. A handler run by
an agent got a traceback instead of a reason.

This tests that seam across ALL FIVE at once, which matters because
`linear-false-closures.py`, `linear-relations.py` and `linear-scan.py` have no
test file of their own. The parametrization is the point: a sixth linear asset
added tomorrow is covered by the glob without anyone remembering to add it.

Hermetic — `urllib.request.urlopen` is stubbed, so nothing reaches the network
and no API key is needed.
"""

import importlib.util
import io
import json
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "commands" / "handlers" / "assets"
LINEAR_ASSETS = sorted(ASSET_DIR.glob("linear-*.py"))


def load(path):
    spec = importlib.util.spec_from_file_location(path.stem.replace("-", "_"), path)
    assert spec is not None and spec.loader is not None, f"cannot load {path}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResponse(io.BytesIO):
    """Enough of an http response for `with urlopen(req) as r: r.read()`."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def serving(payload):
    """Stub urlopen to return `payload` as the decoded JSON body."""
    return mock.patch.object(
        urllib.request,
        "urlopen",
        lambda *a, **k: FakeResponse(json.dumps(payload).encode()),
    )


class TestEveryLinearAssetUnwrapsSafely(unittest.TestCase):
    def test_the_glob_found_the_assets(self):
        """A rename that empties the glob would make every case below vacuous."""
        self.assertGreaterEqual(len(LINEAR_ASSETS), 5, LINEAR_ASSETS)
        for path in LINEAR_ASSETS:
            self.assertTrue((ASSET_DIR / path.name).exists())

    def test_a_good_payload_returns_the_data_object(self):
        for path in LINEAR_ASSETS:
            with self.subTest(asset=path.name):
                mod = load(path)
                with serving({"data": {"ok": True}}):
                    self.assertEqual(mod.gql("key", "query {}"), {"ok": True})

    def test_a_payload_with_no_data_key_exits_with_a_message(self):
        for path in LINEAR_ASSETS:
            with self.subTest(asset=path.name):
                mod = load(path)
                with serving({"nothing": "here"}):
                    with self.assertRaises(SystemExit) as ctx:
                        mod.gql("key", "query {}")
                # SystemExit carrying a string is a message; carrying an int
                # or None would be a bare exit with nothing to read. A plain
                # assert, not assertIsInstance, because this has to narrow the
                # `str | int | None` for the assertIn below.
                code = ctx.exception.code
                assert isinstance(code, str), f"bare exit, no message: {code!r}"
                self.assertIn("GraphQL response.data", code)

    def test_a_non_object_data_exits_with_a_message(self):
        for path in LINEAR_ASSETS:
            with self.subTest(asset=path.name):
                mod = load(path)
                with serving({"data": "unexpected"}):
                    with self.assertRaises(SystemExit) as ctx:
                        mod.gql("key", "query {}")
                code = ctx.exception.code
                assert isinstance(code, str), f"bare exit, no message: {code!r}"
                self.assertIn("expected dict, got str", code)

    def test_a_graphql_errors_envelope_still_exits(self):
        """Pre-existing behaviour that must survive the unwrap change: an
        `errors` envelope arrives with HTTP 200 and has to stop the run."""
        for path in LINEAR_ASSETS:
            with self.subTest(asset=path.name):
                mod = load(path)
                with serving({"errors": [{"message": "rate limited"}]}):
                    with self.assertRaises(SystemExit) as ctx:
                        mod.gql("key", "query {}")
                code = ctx.exception.code
                assert isinstance(code, str), f"bare exit, no message: {code!r}"
                self.assertIn("rate limited", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
