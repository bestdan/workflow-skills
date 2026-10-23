"""Load and validate ~/.config/task-dashboard/config.toml.

The dashboard is per-user, not per-repo, and a launchd agent has no repo working
directory, so the daemon reads exactly one file and only ever reads it (Claude
seeds it at install time; the daemon never writes TOML). tomllib is 3.11+, which
is the reason the whole program carries a 3.11 floor.

Defaults live here rather than in the file so a sparse config is valid: a user
who sets one score weight keeps the other five. Required keys are the ones with
no sane default, the per-site Jira custom-field ids and the board ids, because
acli cannot list them. Failing with the dotted key name beats a KeyError three
frames into a sync.
"""

import copy
import os
import tomllib
from pathlib import Path
from typing import Any

ENV_VAR = "TASK_DASHBOARD_CONFIG"
DEFAULT_PATH = Path("~/.config/task-dashboard/config.toml")

DEFAULTS: dict[str, Any] = {
    "jira": {"blocked_statuses": [], "acli_path": "acli"},
    "github": {"enabled": True, "orgs": []},
    "sync": {
        "interval_minutes": 5,
        "reconcile_minutes": 60,
        "done_window_days": 14,
        "view_concurrency": 8,
    },
    "server": {"port": 8765, "extra_allowed_hosts": []},
    "score": {
        "weights": {
            "priority": 3,
            "deadline": 2,
            "unblocks": 2,
            "stuck": 1,
            "review": 2,
            "blocked": -10,
        }
    },
}

REQUIRED_KEYS = (
    "jira.site",
    "jira.board_ids",
    "jira.fields.sprint",
    "jira.fields.flagged",
)


class ConfigError(ValueError):
    """The config file is missing, unparseable, or lacks a required key."""


def config_path(path: "str | os.PathLike[str] | None" = None) -> Path:
    """Argument, else $TASK_DASHBOARD_CONFIG, else the default location."""
    chosen = path if path is not None else os.environ.get(ENV_VAR) or DEFAULT_PATH
    return Path(chosen).expanduser()


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """`override` over `base`, recursing into tables; neither input is mutated."""
    out = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _lookup(cfg: dict[str, Any], dotted: str) -> Any:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            raise ConfigError(f"missing required config key: {dotted}")
        node = node[part]
    return node


def load(path: "str | os.PathLike[str] | None" = None) -> dict[str, Any]:
    """Read, default-fill and validate the config; returns a plain dict."""
    p = config_path(path)
    try:
        with open(p, "rb") as fh:
            user = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {p}") from None
    except OSError as exc:  # a directory, unreadable, ...: after the clause above
        raise ConfigError(f"cannot read {p}: {exc}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {p}: {exc}") from None
    cfg = deep_merge(DEFAULTS, user)
    for dotted in REQUIRED_KEYS:
        _lookup(cfg, dotted)
    for dotted in ("jira.site", "jira.fields.sprint", "jira.fields.flagged"):
        _non_empty_str(cfg, dotted)
    _non_empty_str(cfg, "jira.acli_path")
    # An empty list is valid and means "no sprint scope": the assigned and
    # reported/watched scopes still work for someone who is on no board.
    ids = cfg["jira"]["board_ids"]
    if not isinstance(ids, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in ids
    ):
        raise ConfigError("jira.board_ids must be a list of integer board ids")
    return cfg


def _non_empty_str(cfg: dict[str, Any], dotted: str) -> None:
    value = _lookup(cfg, dotted)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config key {dotted} must be a non-empty string")
