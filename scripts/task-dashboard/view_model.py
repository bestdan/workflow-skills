"""What a view's lanes hold before they are HTML: lanes, items and actions.

A page and `dashboard.py view <name> --json` (agent_view.py) are drawn from the
same lanes, so the two cannot disagree about what a view shows. Every field
here is plain text: HTML is made from it at render time, never stored in it.

An action's text may name a command in backticks ("decide it, then
`/promote-tasks`"); code() turns those spans into `<code>` when rendering, and
the JSON keeps the backticks.
"""

import html
import re
from dataclasses import dataclass, field
from typing import Any

_e = html.escape
_SPAN = re.compile(r"`([^`]+)`")


def code(text: str) -> str:
    """`text`, escaped, with each backtick span as `<code>`."""
    return _SPAN.sub(lambda m: f"<code>{m.group(1)}</code>", _e(text))


@dataclass(frozen=True)
class Action:
    """What resolves an item: something to do by hand, a command, or both.

    With both, the review comes first: "look before removing; then
    `worktree-remove.sh <path>`".
    """

    review: str = ""
    command: "tuple[str, ...] | None" = None
    url: "str | None" = None

    def __bool__(self) -> bool:
        return bool(self.review or self.command)

    def html(self) -> str:
        cmd = f"<code>{_e(' '.join(self.command))}</code>" if self.command else ""
        if self.review and cmd:
            return f"{code(self.review)}; then {cmd}"
        return cmd or code(self.review)

    def as_dict(self) -> "dict[str, Any] | None":
        if not self:
            return None
        out: dict[str, Any] = {}
        if self.review:
            out["review"] = self.review
        if self.command:
            out["command"] = list(self.command)
        if self.url:
            out["url"] = self.url
        return out


@dataclass
class Lane:
    """One lane of a view: its id (`data-section`), heading and items.

    `group` is the include group the whole lane belongs to, when it has one: a
    lane whose group is switched off renders a note instead of its items.
    `items` are whatever the view's cards are drawn from.
    """

    id: str
    title: "str | None"
    items: list[Any] = field(default_factory=list)
    empty: str = ""
    group: "str | None" = None
    tab: "str | None" = None

    def off(self, inc: Any) -> bool:
        return self.group is not None and inc is not None and not inc.has(self.group)
