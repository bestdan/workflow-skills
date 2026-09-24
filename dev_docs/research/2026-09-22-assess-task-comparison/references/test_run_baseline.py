#!/usr/bin/env python3
"""Hermetic checks on the incumbent baseline runner.

    python3 test_run_baseline.py

No network and no real `claude`: every test points `--claude`/`args.claude` at a
small fake CLI script this file writes to a temp directory, which reads the
prompt from stdin and answers a canned `claude -p --output-format json` reply (or
fails, on request, via an env-var-seeded counter file so failures persist across
the separate processes `run-baseline.py` spawns).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("run_baseline", HERE / "run-baseline.py")
rb = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rb)

_scorer_spec = importlib.util.spec_from_file_location(
    "compare_assess_task", HERE / "compare-assess-task.py"
)
scorer = importlib.util.module_from_spec(_scorer_spec)
_scorer_spec.loader.exec_module(scorer)

CASES = rb.build_cases()
CORPUS_IDS = [c["id"] for c in CASES]

# Token counts and total_cost_usd match run-baseline.py's own smoke-call comment:
# 0.0475822 == (2*4 + 5421*8 + 531*0.2 + 205*20) / 1e6 at PRICING_USD_PER_MTOK.
CANNED_USAGE = {
    "input_tokens": 2,
    "cache_read_input_tokens": 531,
    "cache_creation_input_tokens": 5421,
    "output_tokens": 205,
    "cache_creation": {
        "ephemeral_5m_input_tokens": 5421,
        "ephemeral_1h_input_tokens": 0,
    },
}

FAKE_CLAUDE = (
    """#!/usr/bin/env python3
import json
import os
import sys

if "--version" in sys.argv:
    print("2.5.0 (Claude Code)")
    sys.exit(0)

prompt = sys.stdin.read()

stdin_capture = os.environ.get("FAKE_CLAUDE_STDIN_CAPTURE")
if stdin_capture:
    with open(stdin_capture, "w") as f:
        f.write(prompt)

cwd_capture = os.environ.get("FAKE_CLAUDE_CWD_CAPTURE")
if cwd_capture:
    with open(cwd_capture, "w") as f:
        f.write(os.getcwd())

state_path = os.environ.get("FAKE_CLAUDE_STATE")
if state_path:
    if os.path.exists(state_path):
        fail_left = int(open(state_path).read().strip() or "0")
    else:
        fail_left = int(os.environ.get("FAKE_CLAUDE_FAIL_COUNT", "0"))
    if fail_left > 0:
        with open(state_path, "w") as f:
            f.write(str(fail_left - 1))
        print("boom", file=sys.stderr)
        sys.exit(1)
    with open(state_path, "w") as f:
        f.write("0")

result = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "task_profile:\\n  complexity: standard\\n",
    "duration_api_ms": 4321,
    "total_cost_usd": 0.0475822,
    "usage": """
    + json.dumps(CANNED_USAGE)
    + """,
    "modelUsage": {os.environ.get("FAKE_CLAUDE_SERVED_MODEL", "claude-opus-5-5"): {}},
}
print(json.dumps(result))
"""
)


def write_fake_claude(bin_dir: Path) -> Path:
    path = bin_dir / "fake-claude"
    path.write_text(FAKE_CLAUDE)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return path


class FakeClaudeHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.tmp_path = Path(self.tmp.name)
        self.fake_claude = write_fake_claude(self.tmp_path)
        self._env = dict(os.environ)
        self.addCleanup(self._restore_env)
        for key in list(os.environ):
            if key.startswith("FAKE_CLAUDE_"):
                del os.environ[key]

    def _restore_env(self):
        os.environ.clear()
        os.environ.update(self._env)

    def run_quiet(self, argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = rb.main(argv)
        return code, err.getvalue()


class UsageMapping(FakeClaudeHarness):
    def test_ids_restriction_and_the_written_file(self):
        out = self.tmp_path / "agent-1.json"
        code, _ = self.run_quiet(
            [
                "--agent",
                "1",
                "--claude",
                str(self.fake_claude),
                "--ids",
                "issue-277,issue-348",
                "--out",
                str(out),
            ]
        )
        self.assertEqual(code, 0)
        data = json.loads(out.read_text())
        self.assertEqual(set(data["cards"]), {"issue-277", "issue-348"})
        self.assertEqual(data["failed"], {})
        self.assertEqual(data["agent"], 1)
        self.assertEqual(data["model"], rb.MODEL)
        self.assertEqual(data["pricing_usd_per_mtok"], rb.PRICING_USD_PER_MTOK)

        entry = data["cards"]["issue-277"]
        self.assertEqual(
            entry["tokens"],
            {"uncached": 2, "cache_read": 531, "cache_write": 5421, "output": 205},
        )
        self.assertEqual(entry["raw"], "task_profile:\n  complexity: standard\n")
        # 531 read against 5423 fresh: only the CLI's own prompt was cached.
        self.assertFalse(entry["cache_warm"])
        self.assertAlmostEqual(entry["cost_usd"], 0.0475822)
        self.assertEqual(entry["duration_api_ms"], 4321)
        self.assertEqual(entry["served_models"], ["claude-opus-5-5"])
        self.assertEqual(
            entry["cache_write_ttl"], {"ephemeral_5m": 5421, "ephemeral_1h": 0}
        )
        self.assertGreaterEqual(entry["latency_s"], 0)

    def test_the_pricing_matches_the_smoke_call(self):
        tok = {"uncached": 2, "cache_read": 531, "cache_write": 5421, "output": 205}
        dollars = scorer.call_dollars(tok, rb.PRICING_USD_PER_MTOK)
        self.assertAlmostEqual(dollars, 0.0475822)

    def test_a_served_model_other_than_the_pinned_one_warns(self):
        os.environ["FAKE_CLAUDE_SERVED_MODEL"] = "claude-opus-9-9"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            entry, error = rb.ask_card(
                rb.cli_invocation(str(self.fake_claude)), CASES[0]
            )
        self.assertIsNone(error)
        self.assertEqual(entry["served_models"], ["claude-opus-9-9"])
        self.assertIn("warning:", err.getvalue())
        self.assertIn("claude-opus-9-9", err.getvalue())


class CacheWarm(unittest.TestCase):
    def test_cold_when_only_the_cli_prompt_is_read(self):
        usage = {
            "input_tokens": 2,
            "cache_read_input_tokens": 531,
            "cache_creation_input_tokens": 5421,
        }
        self.assertFalse(rb.is_cache_warm(usage))

    def test_warm_when_most_input_is_read_from_cache(self):
        usage = {
            "input_tokens": 2,
            "cache_read_input_tokens": 5900,
            "cache_creation_input_tokens": 60,
        }
        self.assertTrue(rb.is_cache_warm(usage))


class PromptAndCwd(FakeClaudeHarness):
    def test_stdin_matches_build_corpus_prompt(self):
        capture = self.tmp_path / "stdin.txt"
        os.environ["FAKE_CLAUDE_STDIN_CAPTURE"] = str(capture)
        case = next(c for c in CASES if c["id"] == "issue-277")
        rb.ask_card(rb.cli_invocation(str(self.fake_claude)), case)
        sent = capture.read_text()
        expected = rb.render_prompt(case)
        self.assertEqual(sent, expected)
        self.assertNotIn(rb.MODEL, expected)  # sanity: the prompt is just the card

    def test_cwd_is_a_fresh_directory_not_the_repo(self):
        capture = self.tmp_path / "cwd.txt"
        os.environ["FAKE_CLAUDE_CWD_CAPTURE"] = str(capture)
        rb.ask_card(rb.cli_invocation(str(self.fake_claude)), CASES[0])
        seen_cwd = Path(capture.read_text())
        repo_root = Path(__file__).resolve().parents[4]
        self.assertNotEqual(seen_cwd, repo_root)
        self.assertNotEqual(seen_cwd, HERE)
        self.assertFalse(seen_cwd.exists())  # TemporaryDirectory cleaned it up
        self.assertTrue(str(seen_cwd).startswith(tempfile.gettempdir()))


class Retries(FakeClaudeHarness):
    def test_retry_then_succeed(self):
        state = self.tmp_path / "state"
        os.environ["FAKE_CLAUDE_STATE"] = str(state)
        os.environ["FAKE_CLAUDE_FAIL_COUNT"] = "1"  # fails once, then succeeds
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            entry, error = rb.ask_card(
                rb.cli_invocation(str(self.fake_claude)), CASES[0]
            )
        self.assertIsNone(error)
        self.assertIsNotNone(entry)
        self.assertIn("attempt 1 failed", err.getvalue())
        self.assertEqual(state.read_text(), "0")

    def test_permanent_failure_leaves_the_card_out(self):
        state = self.tmp_path / "state"
        os.environ["FAKE_CLAUDE_STATE"] = str(state)
        os.environ["FAKE_CLAUDE_FAIL_COUNT"] = "5"  # never recovers within 3 tries
        out = self.tmp_path / "agent-2.json"
        code, err = self.run_quiet(
            [
                "--agent",
                "2",
                "--claude",
                str(self.fake_claude),
                "--ids",
                CASES[0]["id"],
                "--out",
                str(out),
            ]
        )
        self.assertEqual(code, 1)
        data = json.loads(out.read_text())
        self.assertEqual(data["cards"], {})
        self.assertIn(CASES[0]["id"], data["failed"])
        self.assertIn("boom", data["failed"][CASES[0]["id"]])
        self.assertEqual(state.read_text(), "2")  # 3 attempts consumed of 5


class WrittenFileLoadsThroughTheScorer(FakeClaudeHarness):
    def test_three_agent_files_pass_check_baselines(self):
        files = []
        for agent in (1, 2, 3):
            out = self.tmp_path / f"agent-{agent}.json"
            code, _ = self.run_quiet(
                [
                    "--agent",
                    str(agent),
                    "--claude",
                    str(self.fake_claude),
                    "--ids",
                    "issue-277,issue-348",
                    "--out",
                    str(out),
                ]
            )
            self.assertEqual(code, 0)
            files.append(json.loads(out.read_text()))
        # The scorer's own loader/validator: no exception means the shape is right,
        # extra keys (cost_usd, duration_api_ms, served_models, cache_write_ttl,
        # claude_cli_version, invocation, failed) are tolerated as documented.
        scorer.check_baselines(files, ["issue-277", "issue-348"])


class Cli(FakeClaudeHarness):
    def test_default_out_path(self):
        # No --out: the file lands under measurement/baseline/agent-N.json,
        # relative to this script's own directory, not the caller's cwd.
        self.assertEqual(
            rb.OUT_DIR / "agent-1.json",
            HERE / "measurement" / "baseline" / "agent-1.json",
        )

    def test_agent_must_be_one_two_or_three(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as cm:
                rb.main(["--agent", "4", "--claude", str(self.fake_claude)])
        self.assertEqual(cm.exception.code, 2)

    def test_claude_version_is_recorded(self):
        out = self.tmp_path / "agent-1.json"
        self.run_quiet(
            [
                "--agent",
                "1",
                "--claude",
                str(self.fake_claude),
                "--ids",
                "issue-277",
                "--out",
                str(out),
            ]
        )
        data = json.loads(out.read_text())
        self.assertIn("2.5.0", data["claude_cli_version"])
        self.assertEqual(data["invocation"][0], str(self.fake_claude))
        self.assertNotIn("issue-277", json.dumps(data["invocation"]))


if __name__ == "__main__":
    unittest.main()
