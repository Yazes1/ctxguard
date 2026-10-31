"""Chromium/Electron cookie-jar check.

A second failure class with the same shape as transcript growth: something grows
once per launch, nothing ever shrinks it, and past a hard limit the app stops
working. Here the limit is the request-header ceiling (16 KB by default): once
the accumulated cookies no longer fit, the server answers 431 and the app looks
like it is "broken on startup".

This module only *reads* the jar, and never while the owning app has it open —
Chromium keeps the SQLite file locked for its whole lifetime.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .core import Finding, should_alert

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
DEFAULT_LIMIT_KB = 16.0


class MeasurementError(RuntimeError):
    """The jar could not be read (missing, or locked by a running app)."""


@dataclass(frozen=True)
class CookieCheck:
    name: str
    path: str
    warn_kb: float = 8.0
    crit_kb: float = 12.0
    limit_kb: float = DEFAULT_LIMIT_KB
    cooldown_hours: float = 12.0

    @classmethod
    def from_config(cls, section: dict[str, Any]) -> "CookieCheck":
        return cls(
            name=str(section.get("name", "cookies")),
            path=str(section.get("path", "")),
            warn_kb=float(section.get("warn_kb", 8.0)),
            crit_kb=float(section.get("crit_kb", 12.0)),
            limit_kb=float(section.get("limit_kb", DEFAULT_LIMIT_KB)),
            cooldown_hours=float(section.get("cooldown_hours", 12.0)),
        )


def measure(path: str | Path) -> dict[str, Any]:
    """Estimate the size of the Cookie header that this jar would produce.

    Values that Chromium stored encrypted are counted by their blob length minus
    the v10 prefix/nonce/tag overhead, which is close enough for a warning line.
    """
    db = Path(path)
    if not db.is_file():
        raise MeasurementError(f"cookie jar not found: {db}")

    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error as exc:  # pragma: no cover - platform dependent
        raise MeasurementError(str(exc)) from exc

    try:
        rows = con.execute(
            "SELECT host_key, name, length(value), length(encrypted_value) FROM cookies"
        ).fetchall()
    except sqlite3.Error as exc:
        raise MeasurementError(f"cannot read cookies table: {exc}") from exc
    finally:
        con.close()

    total = 0
    local = 0
    by_host: dict[str, dict[str, int]] = {}
    for host, name, value_len, encrypted_len in rows:
        size = value_len if value_len else max((encrypted_len or 0) - 31, 0)
        cost = len(name) + size + 2  # "name=value; "
        bucket = by_host.setdefault(host, {"count": 0, "bytes": 0})
        bucket["count"] += 1
        bucket["bytes"] += cost
        total += cost
        if host in LOCAL_HOSTS:
            local += cost

    return {
        "path": str(db),
        "rows": len(rows),
        "header_bytes": total,
        "local_bytes": local,
        "hosts": by_host,
    }


def evaluate(
    measurement: dict[str, Any],
    check: CookieCheck,
    state: dict[str, Any],
    now: float,
) -> list[Finding]:
    kb = measurement["header_bytes"] / 1024.0
    if kb < check.warn_kb:
        return []

    level = "crit" if kb >= check.crit_kb else "warn"
    key = f"{check.name}/{measurement['path']}"
    # a jar does not "grow on its own": cooldown only, growth override disabled
    if not should_alert(state.get("sessions", {}).get(key), kb, now, check.cooldown_hours, 1e9):
        return []

    pct = kb / check.limit_kb * 100 if check.limit_kb else 0
    return [
        Finding(
            check=check.name,
            subject=measurement["path"],
            level=level,
            value=round(kb, 2),
            unit="KB",
            detail=(
                f"{measurement['rows']} cookies, {pct:.0f}% of the "
                f"{check.limit_kb:.0f} KB request-header budget; "
                "the app may start failing with 431 / authentication errors"
            ),
        )
    ]