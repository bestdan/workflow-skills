#!/usr/bin/env python3
"""Hermetic tests for scripts/check-timings.py.

Each case writes a times file, and where it needs one a history file, to a temp
directory, runs main(), and asserts on what it printed and what it recorded.
"""

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-timings.py"

_spec = importlib.util.spec_from_file_location("check_timings", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
ct = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ct)


class CheckTimingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.times = self.dir / "times.tsv"
        self.history = self.dir / "history.tsv"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_main(self, times: dict, total: int, *extra: str) -> tuple[int, str]:
        self.times.write_text("".join(f"{s}\t{n}\n" for n, s in times.items()))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = ct.main(
                [
                    "--times",
                    str(self.times),
                    "--total",
                    str(total),
                    "--mode",
                    "full",
                    *extra,
                ]
            )
        return rc, out.getvalue()

    def seed(self, runs: int, times: dict, total: int, mode: str = "full") -> None:
        lines = []
        for i in range(runs):
            run = f"2026-01-0{i + 1}T00:00:00"
            lines += [f"{run}\t{mode}\t{n}\t{s}\n" for n, s in times.items()]
            lines.append(f"{run}\t{mode}\t{ct.TOTAL}\t{total}\n")
        self.history.write_text("".join(lines))

    def test_prints_slowest_checks_in_order(self) -> None:
        rc, out = self.run_main({"chk-x": 3, "chk-y": 40, "chk-z": 12}, 45)
        self.assertEqual(rc, 0)
        self.assertLess(out.index("chk-y"), out.index("chk-z"))
        self.assertLess(out.index("chk-z"), out.index("chk-x"))
        self.assertNotIn("⚠", out)

    def test_shows_at_most_five(self) -> None:
        _, out = self.run_main({f"c{i}": i for i in range(1, 9)}, 10)
        self.assertIn("c8", out)
        self.assertIn("c4", out)
        self.assertNotIn("c3", out)

    def test_over_budget_warns_and_still_exits_zero(self) -> None:
        rc, out = self.run_main({"a": 5}, 300, "--budget", "240")
        self.assertEqual(rc, 0)
        self.assertIn("⚠ slower: the full gate took 300s, over its 240s budget", out)

    def test_at_budget_is_quiet(self) -> None:
        _, out = self.run_main({"a": 5}, 240, "--budget", "240")
        self.assertNotIn("⚠", out)

    def test_github_flag_emits_annotation(self) -> None:
        _, out = self.run_main({"a": 5}, 300, "--budget", "240", "--github")
        self.assertIn("::warning title=check.sh timing::the full gate took 300s", out)

    def test_jump_names_the_check_that_moved(self) -> None:
        self.seed(3, {"slow": 20, "steady": 30}, 40)
        _, out = self.run_main(
            {"slow": 50, "steady": 31}, 55, "--history", str(self.history)
        )
        self.assertIn("⚠ slower: slow took 50s, 2.5x its median of 20s", out)
        self.assertNotIn("steady took", out)

    def test_small_absolute_jump_is_ignored(self) -> None:
        self.seed(3, {"tiny": 2}, 40)
        _, out = self.run_main({"tiny": 8}, 40, "--history", str(self.history))
        self.assertNotIn("⚠", out)

    def test_needs_enough_samples(self) -> None:
        self.seed(2, {"slow": 20}, 40)
        _, out = self.run_main({"slow": 90}, 95, "--history", str(self.history))
        self.assertNotIn("⚠", out)

    def test_other_modes_history_is_not_compared(self) -> None:
        self.seed(5, {"slow": 5}, 10, mode="fast")
        _, out = self.run_main({"slow": 90}, 95, "--history", str(self.history))
        self.assertNotIn("⚠", out)

    def test_record_appends_only_when_asked(self) -> None:
        self.run_main({"a": 5}, 9, "--history", str(self.history))
        self.assertFalse(self.history.exists())
        self.run_main({"a": 5}, 9, "--history", str(self.history), "--record")
        rows = ct.read_history(self.history)
        self.assertEqual(
            {(m, n, s) for _, m, n, s in rows},
            {("full", "a", 5), ("full", ct.TOTAL, 9)},
        )

    def test_history_keeps_only_recent_runs(self) -> None:
        rows = [(f"run{i:03d}", "full", "a", i) for i in range(ct.RUNS_KEPT + 5)]
        ct.write_history(self.history, rows)
        kept = ct.read_history(self.history)
        self.assertEqual(len(kept), ct.RUNS_KEPT)
        self.assertEqual(kept[0][0], "run005")

    def test_malformed_history_lines_are_skipped(self) -> None:
        self.history.write_text("garbage\nr\tfull\ta\tNaN\nr\tfull\ta\t7\n")
        self.assertEqual(ct.read_history(self.history), [("r", "full", "a", 7)])

    def test_unreadable_times_exits_two(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ct.main(
                ["--times", str(self.dir / "missing"), "--total", "1", "--mode", "full"]
            )
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
