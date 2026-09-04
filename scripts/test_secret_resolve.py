#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/_secret_resolve.py.

Stubs the resolver binary via PATH (a small shell script standing in for
`op`/`opx`) so nothing here ever shells out to the real 1Password CLI, needs
a network, or needs a live `op` session. Covers: allow-list enforcement, an
unknown resolver identifier, a valid ref containing spaces, the malformed
`opx op://…` value, no-fallthrough on a failed resolve, timeout, empty
stdout, that the full ref never appears in an error message, and the
`--probe` CLI contract (stdout stays empty; only a category on stderr).
"""

import contextlib
import importlib.util
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ASSET = (
    Path(__file__).resolve().parents[1]
    / "commands"
    / "handlers"
    / "assets"
    / "_secret_resolve.py"
)

_spec = importlib.util.spec_from_file_location("_secret_resolve", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
secret_resolve = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secret_resolve)


def write_stub(bin_dir, name, script):
    path = Path(bin_dir) / name
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


@contextlib.contextmanager
def stubbed_path(bin_dir):
    # Replace, don't prepend: PATH must contain nothing but our stubs, so the
    # no-binary case can't accidentally find a real `op`/`opx` on the host.
    old_environ = dict(os.environ)
    os.environ["PATH"] = str(bin_dir)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(old_environ)


class ResolveKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin_dir = self.tmp.name
        self._orig_timeout = secret_resolve.TIMEOUT
        self.addCleanup(self._restore_timeout)
        for key in ("SECRET", "SECRET_REF", "SECRET_RESOLVER"):
            os.environ.pop(key, None)

    def _restore_timeout(self):
        secret_resolve.TIMEOUT = self._orig_timeout

    def test_raw_secret_wins_no_resolver_runs(self):
        with stubbed_path(self.bin_dir):
            os.environ["SECRET"] = "raw-value"
            os.environ["SECRET_REF"] = "op://Private/whatever"
            self.assertEqual(secret_resolve.resolve_key("SECRET"), "raw-value")

    def test_allow_listed_resolver_op(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\necho the-secret\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/PreThink Linear/dan_local_key"
            self.assertEqual(secret_resolve.resolve_key("SECRET"), "the-secret")

    def test_allow_listed_resolver_opx(self):
        write_stub(self.bin_dir, "opx", "#!/bin/sh\necho the-opx-secret\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            os.environ["SECRET_RESOLVER"] = "opx"
            self.assertEqual(secret_resolve.resolve_key("SECRET"), "the-opx-secret")

    def test_ref_with_spaces_is_valid(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\necho spaced-secret\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/PreThink Linear/dan_local_key"
            self.assertEqual(secret_resolve.resolve_key("SECRET"), "spaced-secret")

    def test_unknown_resolver_identifier(self):
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            os.environ["SECRET_RESOLVER"] = "pass"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "unknown-resolver")

    def test_malformed_ref_opx_prefix(self):
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "opx op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "malformed-ref")

    def test_malformed_ref_leading_whitespace(self):
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = " op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "malformed-ref")

    def test_no_fallthrough_on_failed_resolve(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\necho 'access denied' 1>&2; exit 1\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            # a failed resolve must raise the specific failure, not silently
            # report unavailability as if nothing were configured
            self.assertNotEqual(ctx.exception.category, "not-found")
            self.assertEqual(ctx.exception.category, "denied")

    def test_no_session_classification(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\necho 'not signed in' 1>&2; exit 1\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "no-session")

    def test_not_found_classification(self):
        write_stub(
            self.bin_dir,
            "op",
            '#!/bin/sh\necho "isn\'t an item in this vault" 1>&2; exit 1\n',
        )
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "not-found")

    def test_timeout(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\n/bin/sleep 5\n")
        secret_resolve.TIMEOUT = 0.2
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "timeout")

    def test_empty_stdout(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\nexit 0\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "empty")

    def test_no_binary(self):
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "op://Private/x/y"
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "no-binary")

    def test_no_ref_or_secret(self):
        # `unconfigured`, not `not-found`: a keyless host is silent, while a ref
        # naming something that does not exist gets reported.
        with stubbed_path(self.bin_dir):
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            self.assertEqual(ctx.exception.category, "unconfigured")

    def test_full_ref_never_in_error_message(self):
        secret = "dan_local_key"
        ref = f"op://Private/PreThink Linear/{secret}"
        cases = []

        write_stub(self.bin_dir, "op", "#!/bin/sh\necho 'access denied' 1>&2; exit 1\n")
        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = ref
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            cases.append(str(ctx.exception))

        with stubbed_path(self.bin_dir):
            os.environ["SECRET_REF"] = "opx " + ref
            with self.assertRaises(secret_resolve.SecretUnavailable) as ctx:
                secret_resolve.resolve_key("SECRET")
            cases.append(str(ctx.exception))

        for message in cases:
            self.assertNotIn(secret, message)
            self.assertNotIn(ref, message)


class ProbeTests(unittest.TestCase):
    """`--probe` is what /doctor calls, so it is exercised as a subprocess.

    The property worth a test is that stdout stays empty even on success —
    that is the guard against a probe ever printing the secret it resolved.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.bin_dir = self.tmp.name

    def _probe(self, env):
        return subprocess.run(
            [sys.executable, str(ASSET), "--probe", "SECRET"],
            capture_output=True,
            text=True,
            env={"PATH": self.bin_dir, **env},
        )

    def test_success_is_silent_on_stdout(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\necho the-secret\n")
        out = self._probe({"SECRET_REF": "op://Private/x/y"})
        self.assertEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        self.assertNotIn("the-secret", out.stderr)

    def test_failure_reports_only_the_category(self):
        write_stub(self.bin_dir, "op", "#!/bin/sh\necho 'not signed in' 1>&2; exit 1\n")
        out = self._probe({"SECRET_REF": "op://Private/PreThink Linear/dan_local_key"})
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        self.assertEqual(out.stderr.strip(), "no-session")

    def test_unconfigured_host_is_its_own_category(self):
        out = self._probe({})
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(out.stdout, "")
        self.assertEqual(out.stderr.strip(), "unconfigured")


class RedactTests(unittest.TestCase):
    def test_op_ref_reduces_to_vault(self):
        self.assertEqual(
            secret_resolve.redact("op://Private/PreThink Linear/dan_local_key"),
            "op://Private/…",
        )

    def test_other_scheme_reduces_to_generic_phrase(self):
        self.assertEqual(
            secret_resolve.redact("vault://secret/data/x"),
            "configured secret reference",
        )

    def test_unparseable_value_reduces_to_generic_phrase(self):
        self.assertEqual(
            secret_resolve.redact("opx op://Private/x/y"), "configured secret reference"
        )


if __name__ == "__main__":
    unittest.main()
