#!/usr/bin/env python3
"""Hermetic tests for scripts/co-review-status.py.

Most cases replace the module's run_gh() seam with a recorder, so the API
path, the context and the state are asserted without a network. One case runs
the script as a subprocess with a stub `gh` first on PATH, so the real
subprocess call is exercised too.
"""

import contextlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "co-review-status.py"

_spec = importlib.util.spec_from_file_location("co_review_status", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
status_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(status_mod)

REPO = "owner/name"
SHA = "a" * 40
PR_URL = f"https://github.com/{REPO}/pull/42"


class FakeGh:
    """Records each `gh` call and answers like the statuses endpoint."""

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def run_gh(self, args, stdin=None):
        self.calls.append((args, json.loads(stdin) if stdin else None))
        if self.fail:
            return 1, "", "HTTP 422: Validation Failed\n"
        payload = json.loads(stdin)
        return (
            0,
            json.dumps(
                {
                    "url": f"https://api.github.com/repos/{REPO}/statuses/{SHA}",
                    "state": payload["state"],
                    "context": payload["context"],
                    "target_url": payload.get("target_url"),
                }
            ),
            "",
        )


class StatusTests(unittest.TestCase):
    def use(self, fake):
        orig = status_mod.run_gh
        status_mod.run_gh = fake.run_gh
        self.addCleanup(setattr, status_mod, "run_gh", orig)
        return fake

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = status_mod.main(argv)
        return code, out.getvalue(), err.getvalue()

    def argv(self, state, sha=SHA, **extra):
        args = ["--repo", REPO, "--sha", sha, "--state", state, "--description", "d"]
        for key, value in extra.items():
            args += [f"--{key.replace('_', '-')}", value]
        return args

    def test_each_state_posts_the_co_review_context_on_the_sha(self):
        for state in ("pending", "success", "failure"):
            with self.subTest(state=state):
                fake = self.use(FakeGh())
                code, out, _ = self.run_main(self.argv(state, target_url=PR_URL))
                self.assertEqual(code, 0)
                self.assertEqual(len(fake.calls), 1)
                args, payload = fake.calls[0]
                self.assertEqual(args[:3], ["api", "--method", "POST"])
                self.assertEqual(args[3], f"repos/{REPO}/statuses/{SHA}")
                self.assertEqual(payload["context"], "co-review")
                self.assertEqual(payload["state"], state)
                self.assertEqual(payload["target_url"], PR_URL)
                self.assertEqual(out, f"STATUS: {state} sha={SHA[:7]} url={PR_URL}\n")

    def test_without_a_target_url_the_line_names_the_status_api_url(self):
        fake = self.use(FakeGh())
        code, out, _ = self.run_main(self.argv("success"))
        self.assertEqual(code, 0)
        self.assertNotIn("target_url", fake.calls[0][1])
        self.assertTrue(
            out.endswith(f"url=https://api.github.com/repos/{REPO}/statuses/{SHA}\n"),
            out,
        )

    def test_description_is_one_line_and_within_the_api_limit(self):
        fake = self.use(FakeGh())
        self.run_main(
            [
                "--repo",
                REPO,
                "--sha",
                SHA,
                "--state",
                "success",
                "--description",
                "two\nlines " + "x" * 300,
            ]
        )
        description = fake.calls[0][1]["description"]
        self.assertNotIn("\n", description)
        self.assertLessEqual(len(description), 140)
        self.assertTrue(description.startswith("two lines "))

    def test_a_bad_sha_exits_2_without_calling_gh(self):
        fake = self.use(FakeGh())
        for bad in ("abc1234", "A" * 40, "g" * 40):
            with self.subTest(bad=bad), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    status_mod.main(self.argv("success", sha=bad))
                self.assertEqual(cm.exception.code, 2)
        self.assertEqual(fake.calls, [])

    def test_a_repo_without_a_slash_exits_2_without_calling_gh(self):
        fake = self.use(FakeGh())
        argv = [
            "--repo",
            "noslash",
            "--sha",
            SHA,
            "--state",
            "success",
            "--description",
            "d",
        ]
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                status_mod.main(argv)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(fake.calls, [])

    def test_a_blank_description_exits_2_without_calling_gh(self):
        fake = self.use(FakeGh())
        argv = [
            "--repo",
            REPO,
            "--sha",
            SHA,
            "--state",
            "success",
            "--description",
            "   ",
        ]
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                status_mod.main(argv)
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(fake.calls, [])

    def test_an_unknown_state_exits_2_without_calling_gh(self):
        fake = self.use(FakeGh())
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                status_mod.main(self.argv("error"))
        self.assertEqual(cm.exception.code, 2)
        self.assertEqual(fake.calls, [])

    def test_a_gh_failure_exits_nonzero_with_a_status_error_line(self):
        self.use(FakeGh(fail=True))
        code, out, _ = self.run_main(self.argv("success"))
        self.assertEqual(code, 1)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertTrue(out.startswith(f"STATUS: error sha={SHA[:7]} reason="), out)
        self.assertIn("Validation Failed", out)
        self.assertNotIn("Traceback", out)


class SubprocessTests(unittest.TestCase):
    """The real `gh` call, against a stub first on PATH."""

    STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$GH_STUB_LOG"
cat >> "$GH_STUB_LOG"
printf '\\n'  >> "$GH_STUB_LOG"
if [ -n "$GH_STUB_FAIL" ]; then echo "HTTP 500" >&2; exit 1; fi
echo '{"url": "https://api.github.com/x", "target_url": null}'
"""

    def run_script(self, *argv, fail=False):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "gh"
            stub.write_text(self.STUB)
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            log = Path(tmp) / "log"
            env = dict(
                os.environ,
                PATH=f"{tmp}{os.pathsep}{os.environ['PATH']}",
                GH_STUB_LOG=str(log),
            )
            if fail:
                env["GH_STUB_FAIL"] = "1"
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), *argv],
                capture_output=True,
                text=True,
                env=env,
                timeout=60,
            )
            return proc, log.read_text() if log.exists() else ""

    def test_posts_through_gh_on_path(self):
        proc, log = self.run_script(
            "--repo", REPO, "--sha", SHA, "--state", "pending", "--description", "d"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(f"api --method POST repos/{REPO}/statuses/{SHA} --input -", log)
        self.assertIn('"context": "co-review"', log)
        self.assertEqual(
            proc.stdout, f"STATUS: pending sha={SHA[:7]} url=https://api.github.com/x\n"
        )

    def test_bad_sha_never_reaches_gh(self):
        proc, log = self.run_script(
            "--repo", REPO, "--sha", "abc", "--state", "pending", "--description", "d"
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(log, "")

    def test_gh_failure_is_a_status_error_line(self):
        proc, _ = self.run_script(
            "--repo",
            REPO,
            "--sha",
            SHA,
            "--state",
            "failure",
            "--description",
            "d",
            fail=True,
        )
        self.assertEqual(proc.returncode, 1)
        self.assertTrue(proc.stdout.startswith("STATUS: error "), proc.stdout)


if __name__ == "__main__":
    unittest.main()
