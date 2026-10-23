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
import re
import tomllib
from pathlib import Path
from typing import Any

ENV_VAR = "TASK_DASHBOARD_CONFIG"
DEFAULT_PATH = Path("~/.config/task-dashboard/config.toml")

DEFAULTS: dict[str, Any] = {
    # `me` is my Jira display name, which the mirror stores people by. Empty
    # means "not set": the views then treat every in-scope issue as mine.
    "jira": {"blocked_statuses": [], "acli_path": "acli", "me": ""},
    # design_repos: `owner/name` repos whose dev_docs/designs/ the fleet view
    # reads for `status: proposed` (github_fleet.py). Empty reads none.
    "github": {"enabled": True, "orgs": [], "design_repos": []},
    "sync": {
        "interval_minutes": 5,
        "reconcile_minutes": 60,
        "done_window_days": 14,
        "view_concurrency": 8,
    },
    "server": {"port": 8765, "extra_allowed_hosts": []},
    # Host name -> "sha256:<hex>" of that machine's ingest token. Empty means
    # the server has no /ingest route at all (the work-laptop instance).
    "ingest": {"machines": {}},
    "score": {
        "weights": {
            "priority": 3,
            "deadline": 2,
            "unblocks": 2,
            "stuck": 1,
            "review": 2,
            "blocked": -10,
        },
        # Days in a status category before the Stuck term starts counting.
        # Stale uses the `indeterminate` one. v1 measures time in category, not
        # in status: acli cannot read the changelog (Fields, under Sync, in
        # dev_docs/designs/2026-10-23-task-dashboard.md).
        "stuck_days": {"new": 14, "indeterminate": 5},
    },
}

REQUIRED_KEYS = (
    "jira.site",
    "jira.board_ids",
    "jira.fields.sprint",
    "jira.fields.flagged",
)


# A snapshot's `host` is the machine's short host name, and it ends up in a row
# source (`machine:<host>`), so it is held to a plain label.
HOST_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


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
    port = cfg["server"]["port"]
    if not isinstance(port, int) or isinstance(port, bool) or not 0 < port < 65536:
        raise ConfigError("server.port must be an integer port (1-65535)")
    if not isinstance(cfg["ingest"], dict):
        raise ConfigError("ingest must be a table")
    machines = cfg["ingest"]["machines"]
    if not isinstance(machines, dict):
        raise ConfigError("ingest.machines must be a table of host = token digest")
    # One token per machine: the server maps a token to the machine it belongs
    # to, so a shared digest would let only one of them ever ingest.
    seen: dict[str, str] = {}
    for host, digest in machines.items():
        if not HOST_RE.fullmatch(host):
            raise ConfigError(f"ingest.machines: {host!r} is not a plain host name")
        if not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
            raise ConfigError(
                f'ingest.machines.{host} must be the "sha256:<hex>" value'
                " that `dashboard.py ingest-token add` prints"
            )
        if digest in seen:
            raise ConfigError(
                f"ingest.machines.{host} has the same token digest as"
                f" ingest.machines.{seen[digest]}; give each machine its own token"
            )
        seen[digest] = host
    return cfg


def _non_empty_str(cfg: dict[str, Any], dotted: str) -> None:
    value = _lookup(cfg, dotted)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"config key {dotted} must be a non-empty string")
