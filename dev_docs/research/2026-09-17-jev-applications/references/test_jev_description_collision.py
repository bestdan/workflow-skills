#!/usr/bin/env python3
"""Hermetic tests for the jev-description-collision.py beside this file.

The pure half only: frontmatter parsing, manifest parsing, the ranking maths, the
collision threshold, and the key-resolution ladder. Nothing here touches the network
or reads a real key. There is deliberately no live counterpart: nothing in this repo
depends on the Jev API, so a standing test against it would be surface with no
dependency behind it. Check the request and response shapes by running the
instrument when that changes.

Each case reproduces something that actually bit during the section-1 measurement,
rather than restating the implementation.

No gate runs this: it is a frozen artifact of the record in the parent directory, and
leaving the repo's `scripts/test-*.sh` glob is the cost of that. Run it by path —
`python3 dev_docs/research/2026-09-17-jev-applications/references/test_jev_description_collision.py`
— when re-running the measurement. Dependencies: the standard library only.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
# The bundle sits four levels below the repository root:
# dev_docs/research/<record>/references/<this file>.
ROOT = Path(__file__).resolve().parents[4]
SPEC = importlib.util.spec_from_file_location(
    "jev_collision", HERE / "jev-description-collision.py"
)
assert SPEC and SPEC.loader
jev = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jev)


class RankTests(unittest.TestCase):
    def test_margin_is_winner_minus_runner_up(self):
        w, pw, r, pr, margin = jev.rank({"a": 0.63, "b": 0.37, "c": 0.0})
        self.assertEqual((w, r), ("a", "b"))
        self.assertAlmostEqual(pw, 0.63)
        self.assertAlmostEqual(pr, 0.37)
        self.assertAlmostEqual(margin, 0.26)

    def test_ordering_does_not_depend_on_dict_order(self):
        """The real response arrives in arbitrary order; the winner is by mass."""
        _, _, _, _, a = jev.rank({"z": 0.1, "m": 0.9})
        _, _, _, _, b = jev.rank({"m": 0.9, "z": 0.1})
        self.assertEqual(a, b)

    def test_single_option_has_no_runner_up(self):
        w, pw, r, pr, margin = jev.rank({"only": 1.0})
        self.assertEqual((w, r, pr), ("only", "", 0.0))
        self.assertAlmostEqual(margin, pw)

    def test_empty_is_an_error_not_a_silent_zero(self):
        with self.assertRaises(ValueError):
            jev.rank({})


class CollisionTests(unittest.TestCase):
    def test_below_threshold_is_a_collision(self):
        self.assertTrue(jev.is_collision(0.09, 0.10))

    def test_threshold_itself_is_clean(self):
        """A boundary that lands exactly on the threshold is a separation."""
        self.assertFalse(jev.is_collision(0.10, 0.10))

    def test_the_tightest_measured_margin_is_not_a_collision_at_default(self):
        """0.16 is the lowest margin seen in four runs (local-review vs co-review).

        The whole "no collisions" result rests on that minimum clearing the default
        threshold. If a future run drops below it the record's claim needs revisiting,
        so the number is pinned here rather than left in prose.
        """
        self.assertFalse(jev.is_collision(0.16))


class NeedsSkillThresholdTests(unittest.TestCase):
    def test_the_threshold_itself_is_a_yes(self):
        """Same boundary rule as is_collision, so the two questions round alike.

        Jev returns exactly 0.50 often enough for the difference to decide rows.
        """
        self.assertTrue(jev.says_needs_skill(0.50, 0.50))

    def test_below_the_threshold_is_a_no(self):
        self.assertFalse(jev.says_needs_skill(0.49, 0.50))


class NoulInstructionsTests(unittest.TestCase):
    def test_every_description_appears_verbatim(self):
        """A roster that drops a skill asks about a roster this repo does not ship.

        The Choice would still be scored over every description via `criteria`, so
        the two questions would silently be answering about different option sets.
        """
        crit = {"a": "Use when alpha.", "b": "Use when beta."}
        text = jev.noul_instructions(crit)
        for name, desc in crit.items():
            self.assertIn(name, text)
            self.assertIn(desc, text)

    def test_the_roster_is_name_ordered_not_dict_ordered(self):
        """Two runs must send byte-identical instructions, or they are not comparable."""
        a = jev.noul_instructions({"z": "Zed.", "m": "Em."})
        b = jev.noul_instructions({"m": "Em.", "z": "Zed."})
        self.assertEqual(a, b)
        self.assertLess(a.index("m: Em."), a.index("z: Zed."))

    def test_the_real_roster_covers_every_skill(self):
        files = {
            str(p): p.read_text() for p in sorted((ROOT / "skills").glob("*/SKILL.md"))
        }
        crit = jev.parse_descriptions(files)
        text = jev.noul_instructions(crit)
        for name in crit:
            self.assertIn(f"- {name}: ", text)


class ChoiceRequestIsUnchangedTests(unittest.TestCase):
    """The Noul must not disturb the Choice, or section 1 stops being reproducible.

    The roster lives in the Noul's own `instructions` for exactly this reason. Putting
    it in `state` — the obvious place, since state is shared — would change the Choice's
    input, and the record's measured margins were taken with the bare prompt as state.
    """

    def _payload(self):
        captured = {}

        def fake(key, payload):
            captured["payload"] = payload
            return {"answers": {}}

        with unittest.mock.patch.object(jev, "ask_payload", fake):
            jev.ask("k", "a prompt", {"a": "Use when alpha."})
        return captured["payload"]

    def test_state_is_still_the_bare_prompt(self):
        self.assertEqual(self._payload()["state"], "a prompt")

    def test_the_choice_criteria_are_still_the_descriptions_alone(self):
        choice = self._payload()["questions"]["skill"]
        self.assertEqual(choice["criteria"], {"a": "Use when alpha."})
        self.assertEqual(choice["type"], "choice")

    def test_the_roster_is_not_in_the_shared_state(self):
        self.assertNotIn("Use when alpha.", self._payload()["state"])

    def test_the_noul_rides_the_same_request(self):
        """One request, not two — the parallel-and-isolated property is the point."""
        questions = self._payload()["questions"]
        self.assertEqual(set(questions), {"skill", "needs_skill"})
        self.assertEqual(questions["needs_skill"]["type"], "noul")


class NegativeProbeTests(unittest.TestCase):
    def test_labels_are_unique(self):
        """main keys the tier map by label, so a duplicate silently loses a tier."""
        labels = [label for _, label, _ in jev.NEGATIVE_PROBES]
        self.assertEqual(len(labels), len(set(labels)))

    def test_every_probe_carries_a_known_tier(self):
        """The two tiers are reported separately; a typo would drop a row from both."""
        for tier, label, _ in jev.NEGATIVE_PROBES:
            self.assertIn(tier, ("plain", "near"), label)

    def test_both_tiers_are_populated(self):
        tiers = {tier for tier, _, _ in jev.NEGATIVE_PROBES}
        self.assertEqual(tiers, {"plain", "near"})

    def test_no_negative_prompt_names_a_skill(self):
        """A prompt naming its own skill measures nothing.

        `evals/manifest.tsv`'s header states that rule for the positive cases and
        nothing enforces it there; the negative set gets the enforcement.
        """
        files = {
            str(p): p.read_text() for p in sorted((ROOT / "skills").glob("*/SKILL.md"))
        }
        names = set(jev.parse_descriptions(files))
        for _, label, prompt in jev.NEGATIVE_PROBES:
            for name in names:
                self.assertNotIn(name, prompt.lower(), label)


class DescriptionTests(unittest.TestCase):
    def test_folded_scalar_is_joined_into_one_line(self):
        text = (
            "---\nname: co-review\ndescription: >\n  Use when the user wants a\n"
            "  collaborative review of a PR.\n---\n\n# body\n"
        )
        got = jev.parse_descriptions({"a": text})
        self.assertEqual(
            got,
            {"co-review": "Use when the user wants a collaborative review of a PR."},
        )

    def test_literal_scalar_is_also_handled(self):
        text = "---\nname: x\ndescription: |\n  One line.\n  Two line.\n---\n\n#\n"
        self.assertEqual(
            jev.parse_descriptions({"a": text}), {"x": "One line. Two line."}
        )

    def test_plain_one_line_description(self):
        text = "---\nname: y\ndescription: Just this.\n---\n\n#\n"
        self.assertEqual(jev.parse_descriptions({"a": text}), {"y": "Just this."})

    def test_a_file_without_frontmatter_is_skipped_not_fatal(self):
        """One malformed skill must not take the whole run down."""
        good = "---\nname: ok\ndescription: Fine.\n---\n\n#\n"
        self.assertEqual(
            jev.parse_descriptions({"bad": "# no frontmatter\n", "good": good}),
            {"ok": "Fine."},
        )

    def test_every_real_skill_yields_a_description(self):
        """The option set IS the interface; a skill missing from it can never win."""
        files = {
            str(p): p.read_text() for p in sorted((ROOT / "skills").glob("*/SKILL.md"))
        }
        got = jev.parse_descriptions(files)
        self.assertEqual(len(got), len(files))
        self.assertTrue(all(v for v in got.values()))


class ManifestTests(unittest.TestCase):
    def test_comments_and_blanks_are_skipped(self):
        text = "# header\n\nco-review\tprompts/co-review.txt\t6\n# trailing\n"
        self.assertEqual(
            jev.parse_manifest(text), [("co-review", "prompts/co-review.txt")]
        )

    def test_a_skill_may_appear_twice(self):
        """`task` has two cases; deduping rows would silently drop one."""
        text = "task\tprompts/task.txt\t6\ntask\tprompts/task-named-issue.txt\t6\n"
        self.assertEqual(len(jev.parse_manifest(text)), 2)

    def test_real_manifest_rows_all_name_an_existing_prompt(self):
        rows = jev.parse_manifest((ROOT / "evals" / "manifest.tsv").read_text())
        self.assertTrue(rows)
        for _, rel in rows:
            self.assertTrue((ROOT / "evals" / rel).is_file(), rel)


class KeyLadderTests(unittest.TestCase):
    def test_raw_beats_ref(self):
        """Rung 0 of auth_key_access.md: a raw secret wins over a pointer."""
        text = 'typesafe:\n  api_key_ref: "op://v/i/f"\n  api_key: "sk-real"\n'
        self.assertEqual(jev.extract_key(text), ("raw", "sk-real"))

    def test_ref_alone_resolves_to_a_pointer(self):
        self.assertEqual(
            jev.extract_key('typesafe:\n  api_key_ref: "op://v/i/f"\n'),
            ("ref", "op://v/i/f"),
        )

    def test_a_pointer_in_the_raw_field_is_never_returned_as_a_secret(self):
        """The whole point: an op:// string must not reach an Authorization header.

        `api_key` and `api_key_ref` differ by six characters and the config looks
        fine either way, so this recovers rather than refusing — but it must never
        come back tagged 'raw', which is what sends it to the API verbatim.
        """
        with contextlib.redirect_stderr(io.StringIO()):
            got = jev.extract_key('typesafe:\n  api_key: "op://v/i/f"\n')
        self.assertEqual(got, ("ref", "op://v/i/f"))

    def test_the_recovered_pointer_is_redacted_in_the_warning(self):
        """auth_key_access.md: never print a full reference, even in a warning.

        The item and field names are the half that advertises which vault entry
        holds a full-account token, so they are what must not survive.
        """
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            jev.extract_key('typesafe:\n  api_key: "op://vlt/ITEMNAME/FIELDNAME"\n')
        self.assertIn("op://vlt/…", err.getvalue())
        self.assertNotIn("ITEMNAME", err.getvalue())
        self.assertNotIn("FIELDNAME", err.getvalue())

    def test_a_real_key_is_still_raw(self):
        """The recovery must not swallow the ordinary case."""
        self.assertEqual(
            jev.extract_key('typesafe:\n  api_key: "sk-real"\n'), ("raw", "sk-real")
        )

    def test_a_configured_ref_beats_a_pointer_misplaced_in_the_raw_field(self):
        """The recovery is a fallback, not a winner.

        The two fields can name different items, and the misplaced one is the typo.
        Resolving it would send another service's full-account token to this API in
        an Authorization header — the harm typesafe_block guards against, one level
        down. The canonical `api_key_ref` has to win.
        """
        with contextlib.redirect_stderr(io.StringIO()):
            got = jev.extract_key(
                "typesafe:\n"
                '  api_key: "op://Private/Linear/token"\n'
                '  api_key_ref: "op://Private/TypeSafe/key"\n'
            )
        self.assertEqual(got, ("ref", "op://Private/TypeSafe/key"))

    def test_a_malformed_pointer_in_the_raw_field_does_not_mask_a_valid_ref(self):
        """The degenerate case of the same bug.

        A junk `op://` value is still classified as a pointer, so returning it early
        meant `op read` failed and resolve_key exited without ever reaching the valid
        `api_key_ref` below it.
        """
        with contextlib.redirect_stderr(io.StringIO()):
            got = jev.extract_key(
                'typesafe:\n  api_key: "op://"\n  api_key_ref: "op://v/i/f"\n'
            )
        self.assertEqual(got, ("ref", "op://v/i/f"))

    def test_the_ignored_misplaced_pointer_is_redacted_too(self):
        """The shadowed-pointer warning prints a ref, so it gets the same rule."""
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            jev.extract_key(
                "typesafe:\n"
                '  api_key: "op://vlt/ITEMNAME/FIELDNAME"\n'
                '  api_key_ref: "op://v/i/f"\n'
            )
        self.assertNotIn("ITEMNAME", err.getvalue())
        self.assertNotIn("FIELDNAME", err.getvalue())

    def test_unfilled_placeholder_is_treated_as_absent(self):
        """The template ships REPLACE_ME; sending it to the API would be worse."""
        self.assertIsNone(
            jev.extract_key(f'typesafe:\n  api_key: "{jev.PLACEHOLDER}"\n')
        )

    def test_placeholder_falls_through_to_the_ref(self):
        text = (
            f'typesafe:\n  api_key: "{jev.PLACEHOLDER}"\n  api_key_ref: "op://v/i/f"\n'
        )
        self.assertEqual(jev.extract_key(text), ("ref", "op://v/i/f"))

    def test_commented_out_key_is_not_read(self):
        self.assertIsNone(jev.extract_key('typesafe:\n  # api_key: "sk-nope"\n'))

    def test_empty_config_yields_nothing(self):
        self.assertIsNone(jev.extract_key("typesafe:\n"))

    def test_another_services_key_is_never_returned(self):
        """The real config holds linear.api_key too; returning it would send a
        full-account Linear token to TypeSafe in an Authorization header."""
        text = (
            'linear:\n  api_key: "lin_api_SECRET"\n'
            'typesafe:\n  api_key: "sk-typesafe"\n'
        )
        self.assertEqual(jev.extract_key(text), ("raw", "sk-typesafe"))

    def test_no_typesafe_block_means_no_key_at_all(self):
        """Not 'fall back to whatever key is in the file'."""
        self.assertIsNone(jev.extract_key('linear:\n  api_key: "lin_api_SECRET"\n'))

    def test_a_nested_typesafe_key_does_not_count_as_the_block(self):
        """Only a top-level `typesafe:` mapping is the TypeSafe config."""
        self.assertIsNone(
            jev.extract_key('other:\n  typesafe:\n    api_key: "sk-nested"\n')
        )


class OperatorKeyFileTests(unittest.TestCase):
    """The rung that lets a human run this without typing a prefix.

    `resolve_key` reaches the filesystem and the environment, so each test points
    the operator key file at a temp dir, confines the rung-0 scan to that same
    dir, and clears the variable. Without all three the suite reads whatever the
    developer's own machine has configured and passes or fails accordingly.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "dev_docs" / "tasks").mkdir(parents=True)
        self.keyfile = self.root / "operator_key"

        patched = unittest.mock.patch.object(jev, "OPERATOR_KEY_FILE", self.keyfile)
        patched.start()
        self.addCleanup(patched.stop)
        # Confine the rung-0 scan to the fixture. The real local_config_paths adds
        # the main checkout's config, found via `git rev-parse --git-common-dir`
        # from the *test process's* cwd rather than from the root it is handed —
        # so without this the developer's own key would be scanned, shadow the
        # fixture, and make the result machine-dependent. Patched here rather than
        # narrowed in the module: that fallback is deliberate, and it is what
        # Execution step 3 of the decision record tells an operator to clean up.
        paths = unittest.mock.patch.object(
            jev,
            "local_config_paths",
            lambda root: [p for p in [root / jev.LOCAL_CONFIG] if p.exists()],
        )
        paths.start()
        self.addCleanup(paths.stop)
        env = unittest.mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("TYPESAFE_API_KEY", None)

    def test_the_file_is_read_when_nothing_else_is_configured(self):
        """The whole point of the rung: no prefix, no key in the repo tree."""
        self.keyfile.write_text("sk-from-file\n")
        self.assertEqual(jev.resolve_key(self.root), "sk-from-file")

    def test_an_exported_variable_still_wins(self):
        """The file sits after the environment, so a one-off prefix overrides it."""
        self.keyfile.write_text("sk-from-file\n")
        os.environ["TYPESAFE_API_KEY"] = "sk-from-env"
        self.assertEqual(jev.resolve_key(self.root), "sk-from-env")

    def test_an_empty_file_falls_through_rather_than_returning_nothing(self):
        """A touched-but-unfilled file must not resolve to the empty string.

        That would sail into an Authorization header as a blank bearer token and
        fail at the API rather than here.
        """
        self.keyfile.write_text("\n")
        with self.assertRaises(SystemExit) as caught:
            jev.resolve_key(self.root)
        self.assertIn("No TypeSafe key", str(caught.exception))

    def test_a_raw_config_value_still_beats_the_file(self):
        """Rung 0 is unchanged: an in-tree raw value still shadows this rung.

        This is the silent-shadow case the contract warns about, asserted rather
        than assumed — adopting the file does not retire the config line for you.
        """
        (self.root / "dev_docs" / "tasks" / ".task-config.local.yml").write_text(
            'typesafe:\n  api_key: "sk-in-tree"\n'
        )
        self.keyfile.write_text("sk-from-file\n")
        self.assertEqual(jev.resolve_key(self.root), "sk-in-tree")


class RedactionTests(unittest.TestCase):
    def test_a_pointer_is_reduced_to_its_vault(self):
        """auth_key_access.md: "Never print a full reference. Reduce it."""
        self.assertEqual(jev.redact_ref("op://Private/TypeSafe/key"), "op://Private/…")

    def test_something_that_is_not_a_full_pointer_is_left_alone(self):
        self.assertEqual(jev.redact_ref("op://Private"), "op://Private")


def _noul_record(truth, *, noul, tier=None, fp=False, fn=False, label="row"):
    """Only the fields report_noul reads. A real record carries a dozen more."""
    return {
        "label": label,
        "tier": tier,
        "winner": "some-skill",
        "needs_skill": noul,
        "needs_skill_truth": truth,
        "false_positive": fp,
        "false_negative": fn,
    }


class RateLineTests(unittest.TestCase):
    def test_the_per_run_spread_is_printed_not_averaged(self):
        """Section 1's finding was that the spread is the story, so it is printed."""
        self.assertEqual(
            jev.rate_line("false positives:", [1, 0, 2], [3, 3, 3]),
            "false positives: 3/9 = 33.3%  (per run: 1, 0, 2)",
        )

    def test_a_single_run_omits_the_spread(self):
        """One run has no spread; "(per run: 1)" would imply it does."""
        self.assertEqual(
            jev.rate_line("false positives:", [1], [4]),
            "false positives: 1/4 = 25.0%",
        )

    def test_an_empty_denominator_is_n_a_not_a_zero_division(self):
        """`--suite negative` alone leaves the positive half with no rows at all."""
        self.assertEqual(
            jev.rate_line("false negatives:", [0], [0]),
            "false negatives: 0/0 = n/a",
        )


class ReportNoulTests(unittest.TestCase):
    """The counting that produces the published rates.

    The fixture is deliberately asymmetric — six negatives against four positives, four
    `plain` against two `near`, one false positive against two false negatives — so a
    swapped denominator or an inverted `needs_skill_truth` partition has to change a
    printed number rather than landing on the right one by luck.
    """

    def _passes(self):
        return [
            {
                "records": [
                    _noul_record(False, noul=0.80, tier="plain", fp=True, label="p-a"),
                    _noul_record(False, noul=0.10, tier="plain", label="p-b"),
                    _noul_record(False, noul=0.20, tier="near", label="n-c"),
                    _noul_record(True, noul=0.30, fn=True, label="pos-1"),
                    _noul_record(True, noul=0.90, label="pos-2"),
                ]
            },
            {
                "records": [
                    _noul_record(False, noul=0.20, tier="plain", label="p-a"),
                    _noul_record(False, noul=0.10, tier="plain", label="p-b"),
                    _noul_record(False, noul=0.30, tier="near", label="n-c"),
                    _noul_record(True, noul=0.40, fn=True, label="pos-1"),
                    _noul_record(True, noul=0.90, label="pos-2"),
                ]
            },
        ]

    def _report(self, passes):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            jev.report_noul(passes, 0.5)
        return buf.getvalue()

    def test_the_two_rates_use_their_own_denominators(self):
        out = self._report(self._passes())
        self.assertIn(
            "false positives: 1/6 = 16.7%  (per run: 1, 0) negative prompts", out
        )
        self.assertIn(
            "false negatives: 2/4 = 50.0%  (per run: 1, 1) positive prompts", out
        )

    def test_each_tier_is_counted_against_its_own_rows(self):
        out = self._report(self._passes())
        self.assertIn("plain: 1/4 = 25.0%  (per run: 1, 0)", out)
        self.assertIn("near: 0/2 = 0.0%  (per run: 0, 0)", out)

    def test_nothing_is_printed_when_neither_half_has_rows(self):
        """A Choice-only run must not emit an empty no-skill block."""
        self.assertEqual(self._report([{"records": []}]), "")


class RunSuiteScoreToLabelTests(unittest.TestCase):
    """The step that turns a score into a label: which noul meets which threshold, and
    which way the `not` points.

    The aggregation tests above inject `false_positive`/`false_negative` as fixture
    booleans, so they cannot see an inversion in the expressions that compute them —
    and until these cases, nothing executed `run_suite` at all. Patching `ask` rather
    than `ask_payload` is the point: it replaces the network and the response while
    leaving `rank`, `says_needs_skill` and the record assembly running for real, so
    what is under test is the composition rather than the parts.
    """

    def _row(self, noul, truth, *, threshold=0.5, tiers=None):
        def fake_ask(key, state, criteria, model=jev.MODEL):
            return {
                "answers": {
                    "skill": {
                        "probabilities": {"alpha": 0.7, "beta": 0.3},
                        "confidence": 0.8,
                    },
                    "needs_skill": {"noul": noul},
                },
                "usage": {"input_tokens": 11},
            }

        # run_suite narrates to stderr; the verdict under test is the returned record.
        with unittest.mock.patch.object(jev, "ask", fake_ask):
            with contextlib.redirect_stderr(io.StringIO()):
                out = jev.run_suite(
                    "k",
                    {"alpha": "Use when alpha."},
                    [("alpha", "a prompt")],
                    jev.DEFAULT_MARGIN,
                    True,
                    needs_skill_truth=truth,
                    noul_threshold=threshold,
                    tiers=tiers,
                )
        return out["results"][0]

    def test_a_high_noul_on_a_no_skill_row_is_a_false_positive(self):
        row = self._row(0.90, False)
        self.assertTrue(row["false_positive"])
        self.assertFalse(row["false_negative"])

    def test_a_low_noul_on_a_needs_skill_row_is_a_false_negative(self):
        row = self._row(0.10, True)
        self.assertTrue(row["false_negative"])
        self.assertFalse(row["false_positive"])

    def test_neither_flag_fires_when_the_noul_agrees_with_the_truth(self):
        """The common case. An inverted `not` in either expression breaks it."""
        self.assertFalse(self._row(0.90, True)["false_negative"])
        self.assertFalse(self._row(0.10, False)["false_positive"])

    def test_a_noul_exactly_at_the_threshold_counts_as_fired(self):
        """`says_needs_skill` is at-or-above. Its own unit test pins the predicate;
        this pins that the record built from it rounds the same way."""
        self.assertTrue(self._row(0.50, False, threshold=0.50)["false_positive"])
        self.assertFalse(self._row(0.50, True, threshold=0.50)["false_negative"])

    def test_the_threshold_that_was_passed_is_the_one_applied(self):
        """A dropped `noul_threshold` would fall back to the 0.5 default in silence,
        which is what makes `--noul-threshold` either load-bearing or a no-op."""
        self.assertFalse(self._row(0.60, False, threshold=0.70)["false_positive"])
        self.assertTrue(self._row(0.60, False, threshold=0.50)["false_positive"])

    def test_the_tier_is_carried_onto_the_record(self):
        """report_noul's per-tier split reads this field, and an unmapped label drops
        the row out of both tiers rather than erroring."""
        self.assertEqual(
            self._row(0.10, False, tiers={"alpha": "plain"})["tier"], "plain"
        )
        self.assertIsNone(self._row(0.10, False)["tier"])


class NoulThresholdRangeTests(unittest.TestCase):
    """`--noul-threshold` is a probability, and argparse's `type=float` is not.

    Out of range the run still completes and prints a rate, which is the failure mode
    worth a check: 0/64 reads as a clean result. `nan` is the same shape and also
    writes the bare token `NaN` into the `--json` records, which is not JSON a strict
    parser reads back.
    """

    def _rejects(self, value):
        """Rejection must happen before the paid path, and the test must not depend on
        the code under test to stay hermetic.

        `main` reaches `resolve_key` and then a live suite the moment the range check
        lets a value through, so stubbing it is what keeps this suite offline even
        against a regression that drops the guard. Without the stub, removing the
        check turns this test into 22 paid requests — measured, not hypothesised.
        """

        def unreachable(root):
            raise AssertionError("range check let the value through to the paid path")

        with unittest.mock.patch.object(jev, "resolve_key", unreachable):
            with contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    jev.main(["--noul-threshold", value])
        return caught.exception.code

    def test_above_one_is_rejected(self):
        self.assertEqual(self._rejects("5"), 2)

    def test_below_zero_is_rejected(self):
        self.assertEqual(self._rejects("-0.1"), 2)

    def test_nan_is_rejected(self):
        """Every comparison against nan is False, so the range check catches it."""
        self.assertEqual(self._rejects("nan"), 2)

    def test_infinity_is_rejected(self):
        self.assertEqual(self._rejects("inf"), 2)

    # The accepting side (0 and 1, the degenerate but meaningful bounds) has no test:
    # the check is inline in main(), so a value that passes it falls through to
    # resolve_key and a paid run. Asserting `0.0 <= 0.0 <= 1.0` instead would test
    # Python, not this file.


if __name__ == "__main__":
    unittest.main(verbosity=2)
