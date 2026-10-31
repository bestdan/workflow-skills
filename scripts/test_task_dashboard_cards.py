#!/usr/bin/env python3
"""Hermetic tests for scripts/task-dashboard/cards.py, the one card every tab
draws: its layout, and a guard that no other module writes a card of its own.
Needs Python 3.11: run via scripts/test-task-dashboard-cards.sh.
"""

import re
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "scripts" / "task-dashboard"
sys.path.insert(0, str(PKG))

import cards  # type: ignore[import-not-found]  # noqa: E402

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)
URL = "https://github.com/o/r/pull/7"


def card(**kw):
    base = dict(item=("github", URL), title="fix it", url=URL, ref="o/r#7")
    return cards.Card(**(base | kw))


class AgeTests(unittest.TestCase):
    def test_the_age_is_bare(self):
        cases = [
            (timedelta(seconds=30), "under a minute"),
            (timedelta(minutes=12), "12m"),
            (timedelta(hours=3), "3h"),
            (timedelta(days=130), "130d"),
        ]
        for ago, want in cases:
            self.assertEqual(cards.age_text(NOW - ago, NOW), want)
        self.assertEqual(cards.age_text(None, NOW), "age unknown")


class RenderTests(unittest.TestCase):
    def test_act_then_title_and_ref_then_one_meta_line(self):
        html = cards.render(
            card(
                act="fix its CI",
                age=cards.Age(NOW - timedelta(days=130), "opened"),
                meta=["your PR", cards.ci("failure")],
            ),
            None,
            NOW,
        )
        self.assertTrue(html.startswith('<div class="card" data-source="github"'))
        self.assertLess(html.index('class="act"'), html.index('class="title"'))
        self.assertIn('fix it</a> <span class="ref">o/r#7</span>', html)
        self.assertIn(
            '<div class="meta"><span class="age" title="opened 2026-06-16">130d</span>'
            ' · your PR · <span class="ci ci-bad" role="img" aria-label="CI failure"'
            ' title="CI failure"><svg ',
            html,
        )

    def test_ci_is_githubs_mark_named_by_its_state(self):
        cases = [
            ("success", "ci ci-ok", "CI success", "check"),
            ("failure", "ci ci-bad", "CI failure", "x"),
            ("pending", "ci ci-pending", "CI pending", "dot-fill"),
            (None, "ci", "CI unknown", "circle-dashed"),
        ]
        for state, cls, word, glyph in cases:
            self.assertEqual(
                cards.ci(state),
                f'<span class="{cls}" role="img" aria-label="{word}"'
                f' title="{word}">{cards.icons.svg(glyph)}</span>',
            )
        self.assertEqual(
            cards.ci_words("draft · CI failure"), "draft · " + cards.ci("failure")
        )

    def test_the_kind_icon_leads_the_title(self):
        html = cards.render(card(kind="pr-draft"), None, NOW)
        self.assertTrue(html.startswith('<div class="card" data-source="github"'))
        self.assertIn(
            '<span class="lead"><span class="kind k-draft" role="img"'
            ' aria-label="Draft pull request" title="Draft pull request"><svg ',
            html,
        )
        self.assertLess(html.index('class="lead"'), html.index('class="title"'))

    def test_a_status_icon_replaces_the_kind_icon_and_no_kind_draws_none(self):
        html = cards.render(card(kind="pr", icon="<i>s</i>"), None, NOW)
        self.assertIn('<span class="lead"><i>s</i></span>', html)
        self.assertNotIn('class="kind', html)
        self.assertNotIn('class="lead"', cards.render(card(), None, NOW))
        self.assertNotIn('class="lead"', cards.render(card(kind="?"), None, NOW))

    def test_the_score_sits_behind_a_question_mark(self):
        html = cards.render(
            card(score=cards.Score("why &amp; how", "2.5", {"terms": "ci"})), None, NOW
        )
        meta = re.search(r'<div class="meta">.*</span></div>', html, re.S)
        assert meta is not None
        self.assertIn('<span class="score">2.5</span>', meta.group(0))
        self.assertIn('aria-label="Score 2.5: why it ranks here"', meta.group(0))
        wid = re.search(r'aria-describedby="(why-[0-9a-f]{10})"', meta.group(0))
        assert wid is not None
        self.assertIn(
            f'<div class="why" data-terms="ci" id="{wid.group(1)}" role="tooltip">'
            "why &amp; how</div>",
            meta.group(0),
        )

    def test_a_rule_ranked_card_has_no_number(self):
        html = cards.render(card(score=cards.Score("smallest first")), None, NOW)
        self.assertNotIn('class="score"', html)
        self.assertIn('aria-label="Why it ranks here"', html)

    def test_a_folded_card_puts_title_and_meta_in_its_summary(self):
        html = cards.render(
            card(url=None, folded=True, meta=["idle"], act="open the PR"), None, NOW
        )
        self.assertTrue(html.startswith("<details "))
        self.assertIn(
            '<summary><span class="title">fix it</span> <span class="ref">o/r#7</span>'
            ' <span class="meta">idle</span></summary><div class="act">',
            html,
        )

    def test_the_title_and_ref_are_escaped(self):
        html = cards.render(card(title="a<b", ref="x&y"), None, NOW)
        self.assertIn(">a&lt;b</a>", html)
        self.assertIn('<span class="ref">x&amp;y</span>', html)

    def test_an_untitled_card_shows_its_ref_as_the_title(self):
        html = cards.render(card(title=""), None, NOW)
        self.assertIn(">o/r#7</a>", html)
        self.assertNotIn('class="ref"', html)


class OneCardTests(unittest.TestCase):
    """Every tab's card comes from cards.render, so none can drift."""

    # The Ignored footer is a list of names with an Unignore button, not a
    # card; it borrows the class for the overlay script's data-key lookup.
    ALLOWED = {("views.py", '<li class="card"')}

    def test_no_other_module_writes_a_card(self):
        found = []
        for path in sorted(PKG.glob("*.py")):
            if path.name == "cards.py":
                continue
            text = path.read_text()
            for m in re.finditer(r'(<li )?class="card[^"]*"?', text):
                if (path.name, m.group(0)) not in self.ALLOWED:
                    line = text.count("\n", 0, m.start()) + 1
                    found.append(f"{path.name}:{line}: {m.group(0)}")
        self.assertEqual(found, [], "build a cards.Card and call cards.render")

    def test_only_cards_calls_card_attrs(self):
        users = [
            p.name
            for p in sorted(PKG.glob("*.py"))
            if p.name not in ("cards.py", "overlay.py")
            and "card_attrs(" in p.read_text()
        ]
        self.assertEqual(users, [])


if __name__ == "__main__":
    unittest.main()
