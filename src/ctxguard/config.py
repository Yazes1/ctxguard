"""Configuration loading. Zero dependencies beyond the stdlib."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "general": {"state_dir": "~/.ctxguard"},
    "thresholds": {
        "warn_mb": 3.0,
        "crit_mb": 5.0,
        "active_hours": 72,
        "cooldown_hours": 12,
        "growth_mb": 0.5,
    },
    "report": {"gap_hours": 1.0},
    "checks": [],
}


def expand_path(value: str | os.PathLike[str]) -> Path:
    """Expand ~ and environment variables, then return an absolute Path."""
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).absolute()


def default_config_path() -> Path | None:
    for candidate in (
        Path.home() / ".config" / "ctxguard" / "config.toml",
        Path.home() / ".ctxguard.toml",
    ):
        if candidate.is_file():
            return candidate
    return None


def load(path: Path | None = None) -> dict[str, Any]:
    """Load config, merging user values over the built-in defaults."""
    config: dict[str, Any] = {
        key: (dict(val) if isinstance(val, dict) else val) for key, val in DEFAULTS.items()
    }

    path = path or default_config_path()
    if path is not None and path.is_file():
        # utf-8-sig tolerates the BOM that Windows editors like to add
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))
        for key, value in raw.items():
            if isinstance(value, dict) and isinstance(config.get(key), dict):
                config[key].update(value)
            else:
                config[key] = value

    config["checks"] = _normalise_checks(config)
    return config


def _normalise_checks(config: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    for item in config.get("checks", []):
        if isinstance(item, dict):
            checks.append(dict(item))

    # backwards compatibility: [[sources]] was the 0.1 syntax for a file check
    for item in config.get("sources", []) or []:
        if not isinstance(item, dict) or "root" not in item:
            continue
        checks.append(
            {
                "kind": "file_size",
                "name": str(item.get("name", "files")),
                "root": str(item["root"]),
                "pattern": str(item.get("pattern", "**/*")),
                "enabled": bool(item.get("enabled", True)),
            }
        )

    cleaned: list[dict[str, Any]] = []
    for item in checks:
        if not item.get("enabled", True):
            continue
        item.setdefault("kind", "file_size")
        item.setdefault("name", item.get("root") or item.get("path") or "check")
        cleaned.append(item)
    return cleaned


def state_dir(config: dict[str, Any]) -> Path:
    return expand_path(config["general"].get("state_dir", "~/.ctxguard"))