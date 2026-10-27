"""The ranking-side source seam: what a source hands Now to rank.

dev_docs/designs/2026-10-27-shared-now-ranker.md ("The source seam"). Each
source turns its rows into Candidates, and the ranker (fleet_now.rank) reads
Candidates only: their category puts them in a tier, their `terms` feed the
score, and `need` is what the card shows. A new source (Jira tickets, Linear
issues, repo-pr task files) is one more function that returns Candidates.

A leaf: it imports nothing from the dashboard, because fleet_now imports it
and close_out -> workstreams -> views -> fleet_now is already a cycle.
"""

from dataclasses import dataclass, field
from typing import Any

# The score inputs a source may supply in `terms`. One the source has no data
# for is left out, never set to a stand-in value.
PRIO = "prio"  # a `prio:N` label, as score.prio_of returns it
SINCE = "since"  # when the wait began (datetime): the `age` term


@dataclass
class Candidate:
    # What the card shows (a needs.Need): its overlay item, kind, title,
    # link, state and next step. Not imported, to keep this module a leaf.
    need: Any
    category: str  # one of score.FLEET_CATEGORIES, or of fleet_now.LITTLE_TIERS
    why: str  # the evidence that put it in its category
    terms: dict[str, Any] = field(default_factory=dict)
    # Whether the item is blocked. No source sets it yet, and no tier reads
    # it yet.
    blocked: bool = False
    # What `tune` and "why not now" record beyond what every item has
    # (whynot.features adds kind, category, age, workstream and mode).
    features: dict[str, Any] = field(default_factory=dict)
    # Items this one folds into. No source sets it yet.
    links: tuple = ()
    from_needs: bool = False  # Needs you lists it too
    # A close-out's steps that have evidence (close_out.Step); empty otherwise.
    checklist: list = field(default_factory=list)
    mode: str = "ship"  # the mode whose collector found it

    @property
    def item(self) -> "tuple[str, str]":
        """The overlay key, `(source, key)`."""
        return self.need.item

    @property
    def kind(self) -> str:
        """For the include filter (include.KIND_GROUPS)."""
        return self.need.kind
