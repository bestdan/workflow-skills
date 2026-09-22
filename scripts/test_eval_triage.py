#!/usr/bin/env python3
"""Hermetic tests for scripts/eval-triage.py.

Loads the script by file path (hyphenated name, not an importable module) and
drives `triage()` over synthetic stream-json logs. No subprocess, no network,
no `claude` CLI — the point is that every cause the harness can report is
exercised without spending a run.

The log fixtures mirror the real event shapes observed from
`claude -p --output-format stream-json --verbose`: a `system`/`init` event
carrying `skills`, `slash_commands` and `plugins`, `assistant` events whose
`message.content` holds `tool_use` blocks, and a terminal `result` event.
"""

import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "eval-triage.py"

_spec = importlib.util.spec_from_file_location("eval_triage", SCRIPT)
assert _spec is not None and _spec.loader is not None, f"cannot load {SCRIPT}"
eval_triage = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_triage)


def init(skills=("workflow-skills:co-review",), commands=(), plugins=()):
    return {
        "type": "system",
        "subtype": "init",
        "skills": list(skills),
        "slash_commands": list(commands),
        "plugins": list(plugins),
    }


def tool_use(name, inputs):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": inputs}]},
    }


def result(subtype="success", **kw):
    event = {
        "type": "result",
        "subtype": subtype,
        "is_error": subtype != "success",
        "num_turns": 2,
        "api_error_status": None,
        "stop_reason": "end_turn",
        "terminal_reason": "completed",
        "permission_denials": [],
        "result": "done",
    }
    event.update(kw)
    return event


def log(*events):
    return [json.dumps(event) for event in events]


class TriageTest(unittest.TestCase):
    def test_pass_tolerates_the_plugin_prefix(self):
        verdict = eval_triage.triage(
            log(
                init(),
                tool_use("Skill", {"skill": "workflow-skills:co-review"}),
                result(),
            ),
            "co-review",
        )
        self.assertTrue(verdict["fired"])
        self.assertIsNone(verdict["cause"])
        # The final text is only interesting on a miss; a pass shouldn't carry it.
        self.assertIsNone(verdict["final_text"])

    def test_no_skill_chosen_when_surfaced_and_the_run_completed(self):
        verdict = eval_triage.triage(
            log(init(), result(result="Here's how I'd do it by hand.")),
            "co-review",
        )
        self.assertFalse(verdict["fired"])
        self.assertEqual(verdict["cause"], "no-skill-chosen")
        self.assertTrue(verdict["surfaced"])
        self.assertEqual(verdict["final_text"], "Here's how I'd do it by hand.")

    def test_not_surfaced_beats_the_routing_verdict(self):
        verdict = eval_triage.triage(
            log(init(skills=["superpowers:brainstorming"]), result()),
            "co-review",
        )
        self.assertEqual(verdict["cause"], "not-surfaced")
        self.assertFalse(verdict["surfaced"])

    def test_a_slash_command_counts_as_surfaced(self):
        verdict = eval_triage.triage(
            log(init(skills=[], commands=["/workflow-skills:co-review"]), result()),
            "co-review",
        )
        self.assertEqual(verdict["cause"], "no-skill-chosen")
        self.assertTrue(verdict["surfaced"])

    def test_wrong_skill_is_distinguished_from_none(self):
        verdict = eval_triage.triage(
            log(
                init(
                    skills=["workflow-skills:co-review", "workflow-skills:local-review"]
                ),
                tool_use("Skill", {"skill": "workflow-skills:local-review"}),
                result(),
            ),
            "co-review",
        )
        self.assertEqual(verdict["cause"], "wrong-skill")
        self.assertEqual(verdict["skills_invoked"], ["workflow-skills:local-review"])

    def test_max_turns(self):
        verdict = eval_triage.triage(
            log(
                init(),
                tool_use("Read", {"file_path": "x"}),
                result("error_max_turns", num_turns=6),
            ),
            "co-review",
        )
        self.assertEqual(verdict["cause"], "max-turns")
        self.assertEqual(verdict["num_turns"], 6)
        self.assertEqual(verdict["tools_used"], ["Read"])

    def test_api_error_is_named_even_when_the_subtype_says_success(self):
        verdict = eval_triage.triage(
            log(init(), result(api_error_status=529)),
            "co-review",
        )
        self.assertEqual(verdict["cause"], "api-error")

    def test_error_during_execution_is_an_api_error(self):
        verdict = eval_triage.triage(
            log(init(), result("error_during_execution")), "co-review"
        )
        self.assertEqual(verdict["cause"], "api-error")

    def test_a_killed_run_has_no_result_event(self):
        # `timeout` sends SIGTERM, so the log simply stops mid-stream.
        verdict = eval_triage.triage(
            log(init(), tool_use("Read", {"file_path": "x"})) + ['{"type":"assis'],
            "co-review",
            rc=124,
        )
        self.assertEqual(verdict["cause"], "no-result-event")
        self.assertEqual(verdict["rc"], 124)
        self.assertIsNone(verdict["result_subtype"])

    def test_an_empty_log_does_not_raise(self):
        verdict = eval_triage.triage([], "co-review")
        self.assertEqual(verdict["cause"], "no-result-event")
        # No init event at all is not the same claim as "the skill was absent".
        self.assertIsNone(verdict["surfaced"])

    def test_non_json_noise_is_skipped(self):
        noisy = ["note: ANTHROPIC_API_KEY not set", "[]", "null"] + log(
            init(), tool_use("Skill", {"skill": "co-review"}), result()
        )
        self.assertTrue(eval_triage.triage(noisy, "co-review")["fired"])

    def test_the_repo_plugin_loaded_twice_is_recorded(self):
        plugins = [
            {"name": "workflow-skills", "path": "/repo"},
            {
                "name": "workflow-skills",
                "path": "/Users/x/.claude/plugins/cache/ws/abc",
            },
            {"name": "superpowers", "path": "/elsewhere"},
        ]
        verdict = eval_triage.triage(
            log(init(plugins=plugins), result()), "co-review", plugin="workflow-skills"
        )
        self.assertEqual(
            verdict["plugin_dirs"], ["/repo", "/Users/x/.claude/plugins/cache/ws/abc"]
        )
        self.assertIn("plugin loaded twice", "\n".join(eval_triage.render(verdict)))

    def test_long_final_text_is_truncated(self):
        verdict = eval_triage.triage(log(init(), result(result="x" * 900)), "co-review")
        self.assertEqual(len(verdict["final_text"]), eval_triage.FINAL_TEXT_LIMIT + 1)
        self.assertTrue(verdict["final_text"].endswith("…"))

    def test_a_pass_on_a_killed_run_is_flagged_not_reported_clean(self):
        # The observed case: local-review fired its skill, then `timeout` killed
        # the run at the cap. Old harness and new both call it a pass; only this
        # says the pass was luck.
        verdict = eval_triage.triage(
            log(init(), tool_use("Skill", {"skill": "workflow-skills:co-review"})),
            "co-review",
            rc=124,
        )
        self.assertTrue(verdict["fired"])
        self.assertTrue(verdict["truncated"])
        self.assertIsNone(verdict["cause"])
        rendered = "\n".join(eval_triage.render(verdict))
        self.assertIn("⚠", rendered)
        self.assertIn("truncated", rendered)

    def test_a_clean_pass_carries_no_warning(self):
        verdict = eval_triage.triage(
            log(init(), tool_use("Skill", {"skill": "co-review"}), result()),
            "co-review",
        )
        self.assertFalse(verdict["truncated"])
        self.assertEqual(eval_triage.render(verdict), ["  ✅ PASS"])

    def test_an_alternatives_row_passes_on_either_name(self):
        # The observed case: prompts/task.txt legitimately routes to `task` in
        # one run and `add-task` in the next, and both are right.
        for fired in ("workflow-skills:task", "workflow-skills:add-task"):
            with self.subTest(fired=fired):
                verdict = eval_triage.triage(
                    log(
                        init(
                            skills=["workflow-skills:task", "workflow-skills:add-task"]
                        ),
                        tool_use("Skill", {"skill": fired}),
                        result(),
                    ),
                    "add-task|task",
                )
                self.assertTrue(verdict["fired"])

    def test_an_alternatives_row_still_fails_on_a_third_name(self):
        verdict = eval_triage.triage(
            log(
                init(skills=["workflow-skills:task", "workflow-skills:co-review"]),
                tool_use("Skill", {"skill": "workflow-skills:co-review"}),
                result(),
            ),
            "add-task|task",
        )
        self.assertFalse(verdict["fired"])
        self.assertEqual(verdict["cause"], "wrong-skill")

    def test_an_alternatives_row_is_surfaced_when_any_alternative_is_listed(self):
        verdict = eval_triage.triage(
            log(init(skills=["workflow-skills:add-task"]), result()), "add-task|task"
        )
        self.assertTrue(verdict["surfaced"])
        self.assertEqual(verdict["cause"], "no-skill-chosen")

    def test_render_reports_the_cause_and_the_evidence(self):
        verdict = eval_triage.triage(
            log(init(), result(result="I'll just answer.")), "co-review"
        )
        rendered = "\n".join(eval_triage.render(verdict))
        self.assertIn("cause: no-skill-chosen", rendered)
        self.assertIn("surfaced=True", rendered)
        self.assertIn("said instead: I'll just answer.", rendered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
