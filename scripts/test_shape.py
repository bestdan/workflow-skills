#!/usr/bin/env python3
"""Hermetic tests for commands/handlers/assets/_shape.py.

`expect()` is now the shape gate for six assets — the rollup helper and all five
linear ones — so a hole here is a hole in every one of them. Nothing else covers
it: three of those five assets have no test file at all.

Two properties carry the weight and get the most cases. A wrong-shaped payload
must produce a NAMED failure rather than a KeyError or an AttributeError, and a
`bool` must not pass where an `int` is wanted, because Python makes `bool` a
subclass of `int` and a count of `True` would otherwise satisfy `> 0`.
"""

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "commands" / "handlers" / "assets" / "_shape.py"

_spec = importlib.util.spec_from_file_location("_shape", ASSET)
assert _spec is not None and _spec.loader is not None, f"cannot load {ASSET}"
shape = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(shape)

expect = shape.expect
ShapeError = shape.ShapeError


class TestReturnsTheValue(unittest.TestCase):
    def test_returns_the_value_unchanged(self):
        inner = {"a": 1}
        self.assertIs(expect({"data": inner}, "data", dict, "response"), inner)

    def test_accepts_each_type_it_is_asked_for(self):
        payload = {"n": 3, "s": "x", "b": True, "l": [1], "d": {"k": 1}}
        self.assertEqual(expect(payload, "n", int, "p"), 3)
        self.assertEqual(expect(payload, "s", str, "p"), "x")
        self.assertEqual(expect(payload, "b", bool, "p"), True)
        self.assertEqual(expect(payload, "l", list, "p"), [1])
        self.assertEqual(expect(payload, "d", dict, "p"), {"k": 1})

    def test_zero_and_empty_are_values_not_absences(self):
        """`if not val` would reject these. They are legitimate."""
        payload = {"n": 0, "s": "", "l": [], "d": {}, "b": False}
        self.assertEqual(expect(payload, "n", int, "p"), 0)
        self.assertEqual(expect(payload, "s", str, "p"), "")
        self.assertEqual(expect(payload, "l", list, "p"), [])
        self.assertEqual(expect(payload, "d", dict, "p"), {})
        self.assertEqual(expect(payload, "b", bool, "p"), False)


class TestFailsWithAName(unittest.TestCase):
    def assert_raises_saying(self, *args, contains):
        with self.assertRaises(ShapeError) as ctx:
            expect(*args)
        self.assertIn(contains, str(ctx.exception))

    def test_missing_key(self):
        self.assert_raises_saying(
            {}, "data", dict, "response", contains="response.data: missing"
        )

    def test_wrong_type_names_both_types(self):
        self.assert_raises_saying(
            {"data": "oops"},
            "data",
            dict,
            "response",
            contains="response.data: expected dict, got str",
        )

    def test_a_non_dict_container_is_a_named_failure_not_an_AttributeError(self):
        """The case that bit the rollup helper: a truthy non-dict reached
        `.get()` and raised AttributeError, escaping the failure protocol."""
        self.assert_raises_saying(
            "not a dict",
            "data",
            dict,
            "response",
            contains="response: expected an object, got str",
        )

    def test_none_container(self):
        self.assert_raises_saying(
            None, "data", dict, "response", contains="expected an object, got NoneType"
        )

    def test_a_null_value_is_reported_as_null_not_as_missing(self):
        """`{"k": None}` has the key. Saying "missing" would send a reader
        looking for an absent field."""
        self.assert_raises_saying(
            {"k": None}, "k", dict, "p", contains="p.k: expected dict, got NoneType"
        )

    def test_where_names_the_level_that_failed(self):
        self.assert_raises_saying(
            {"x": 1},
            "y",
            int,
            "response.data.repository",
            contains="response.data.repository.y",
        )


class TestBoolIsNotAnInt(unittest.TestCase):
    """Python makes `bool` a subclass of `int`, so `isinstance(True, int)` is
    True. A `totalCount` of `True` would satisfy `> 0` and mark an issue a
    parent rollup — the same family as the string-`totalCount` defect."""

    def test_bool_is_rejected_where_int_is_wanted(self):
        with self.assertRaises(ShapeError) as ctx:
            expect({"n": True}, "n", int, "p")
        self.assertIn("p.n: expected int, got bool", str(ctx.exception))

    def test_false_is_rejected_too(self):
        with self.assertRaises(ShapeError):
            expect({"n": False}, "n", int, "p")

    def test_int_is_still_rejected_where_bool_is_wanted(self):
        """The other direction is plain isinstance and must keep working: 1 is
        not a bool."""
        with self.assertRaises(ShapeError) as ctx:
            expect({"b": 1}, "b", bool, "p")
        self.assertIn("p.b: expected bool, got int", str(ctx.exception))

    def test_a_real_int_still_passes(self):
        self.assertEqual(expect({"n": 1}, "n", int, "p"), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
