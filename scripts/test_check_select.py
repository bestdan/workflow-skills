#!/usr/bin/env python3
"""Tests for scripts/check-select.py's selection rules.

select() is pure over a {path: text} map, so these cases build the map by hand
rather than a repository; one case at the end runs main() in a fixture repo to
cover the git plumbing.
"""

import contextlib
import importlib.util
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-select.py"

_spec = importlib.util.spec_from_file_location("check_select", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
cs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cs)

TREE = {
    "scripts/widget.sh": "echo widget",
    "scripts/test-widget.sh": "run scripts/widget.sh",
    "test/widget.bats": "load test_helper\nrun scripts/widget.sh",
    "scripts/gadget-tool.py": "print('gadget')",
    "scripts/test_gadget_tool.py": "spec_from_file_location('gadget-tool.py')",
    "scripts/test-gadget-tool.sh": "exec python3 scripts/test_gadget_tool.py",
    "scripts/dashpkg/settings.py": "X = 1",
    "scripts/dashpkg/server.py": "import settings",
    "scripts/test-dashpkg-server.sh": "cd scripts/dashpkg/ && python3 -m unittest",
    "scripts/test-validate.sh": "uv run scripts/validate.py",
    "scripts/test-spawn-orchestrator.sh": "for part in scripts/test-spawn-orchestrator-*.sh",
    "scripts/test-spawn-orchestrator-alarm.sh": "echo alarm",
    "scripts/test-other.sh": "echo unrelated",
    "scripts/orphan.sh": "echo nobody tests me",
    "skills/demo/SKILL.md": "# demo",
    "test/test_helper.bash": "helper",
}


def pick(*changed: str) -> set:
    return cs.select(list(changed), TREE)


class SelectTest(unittest.TestCase):
    def test_a_test_naming_the_file_is_selected(self) -> None:
        self.assertEqual(
            pick("scripts/widget.sh"), {"scripts/test-widget.sh", "test/widget.bats"}
        )

    def test_a_changed_test_selects_itself(self) -> None:
        self.assertEqual(pick("scripts/test-other.sh"), {"scripts/test-other.sh"})

    def test_a_wrapper_is_reached_through_the_unittest_file_it_runs(self) -> None:
        self.assertIn("scripts/test-gadget-tool.sh", pick("scripts/gadget-tool.py"))

    def test_a_package_module_selects_tests_naming_the_package(self) -> None:
        self.assertEqual(
            pick("scripts/dashpkg/settings.py"), {"scripts/test-dashpkg-server.sh"}
        )

    def test_a_skill_edit_runs_the_real_tree_validator(self) -> None:
        self.assertEqual(pick("skills/demo/SKILL.md"), {"scripts/test-validate.sh"})

    def test_an_orchestrator_part_runs_the_orchestrator(self) -> None:
        self.assertEqual(
            pick("scripts/test-spawn-orchestrator-alarm.sh"),
            {"scripts/test-spawn-orchestrator.sh"},
        )

    def test_unnamed_prose_selects_nothing(self) -> None:
        self.assertEqual(pick("docs/notes.txt"), set())

    def test_unnamed_code_needs_the_full_gate(self) -> None:
        with self.assertRaisesRegex(cs.FullGate, "no test names scripts/orphan.sh"):
            pick("scripts/orphan.sh")

    def test_gate_infrastructure_needs_the_full_gate(self) -> None:
        for path in (
            "scripts/check.sh",
            "test/test_helper.bash",
            "scripts/lib/x.sh",
            "mise.toml",
        ):
            with self.subTest(path=path):
                with self.assertRaisesRegex(cs.FullGate, "gate infrastructure"):
                    pick(path)

    def test_selections_union_across_files(self) -> None:
        self.assertEqual(
            pick("scripts/widget.sh", "scripts/test-other.sh"),
            {"scripts/test-widget.sh", "test/widget.bats", "scripts/test-other.sh"},
        )

    def test_short_names_do_not_match_everything(self) -> None:
        tree = dict(
            TREE,
            **{"scripts/a.sh": "", "scripts/test-other.sh": "a.sh is in this text"},
        )
        with self.assertRaises(cs.FullGate):
            cs.select(["scripts/a.sh"], tree)


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


class MainTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        self._env = dict(os.environ)
        os.environ["GIT_CONFIG_GLOBAL"] = "/dev/null"
        os.environ.pop("CHECK_CHANGED_BASE", None)
        repo = Path(self._tmp.name)
        os.chdir(repo)
        git("init", "-q", "-b", "main")
        git("config", "user.email", "t@example.com")
        git("config", "user.name", "t")
        for path, text in TREE.items():
            (repo / path).parent.mkdir(parents=True, exist_ok=True)
            (repo / path).write_text(text)
        git("add", "-A")
        git("commit", "-qm", "init")
        git("switch", "-qc", "topic")
        self.repo = repo

    def tearDown(self) -> None:
        os.chdir(self._cwd)
        os.environ.clear()
        os.environ.update(self._env)
        self._tmp.cleanup()

    def run_main(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cs.main([]), 0)
        return out.getvalue()

    def test_committed_uncommitted_and_untracked_changes_all_count(self) -> None:
        (self.repo / "scripts/widget.sh").write_text("echo changed")
        git("commit", "-qam", "edit widget")
        (self.repo / "scripts/test-other.sh").write_text("echo dirty")
        (self.repo / "scripts/test-new.sh").write_text("echo new")
        self.assertEqual(
            self.run_main().splitlines(),
            [
                "subset",
                "unit scripts/test-new.sh",
                "unit scripts/test-other.sh",
                "unit scripts/test-widget.sh",
                "unit test/widget.bats",
            ],
        )

    def test_an_unchanged_branch_selects_nothing(self) -> None:
        self.assertEqual(self.run_main(), "subset\n")

    def test_no_default_branch_needs_the_full_gate(self) -> None:
        git("branch", "-qm", "main", "trunk")
        self.assertEqual(self.run_main(), "full no merge-base with the base branch\n")

    def test_the_base_can_be_overridden_for_a_stacked_branch(self) -> None:
        (self.repo / "scripts/widget.sh").write_text("echo parent")
        git("commit", "-qam", "parent work")
        git("switch", "-qc", "child")
        (self.repo / "scripts/test-other.sh").write_text("echo child")
        os.environ["CHECK_CHANGED_BASE"] = "topic"
        self.assertEqual(
            self.run_main().splitlines(), ["subset", "unit scripts/test-other.sh"]
        )

    def test_an_unknown_base_override_needs_the_full_gate(self) -> None:
        os.environ["CHECK_CHANGED_BASE"] = "no-such-branch"
        self.assertEqual(self.run_main(), "full no merge-base with the base branch\n")


if __name__ == "__main__":
    unittest.main()
