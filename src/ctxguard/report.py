"""Return summary: what happened while nobody was watching.

Built from the append-only event log, so it works with no network access and
survives a reboot.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def _fmt(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _alert_line(alert: dict[str, Any]) -> str:
    subject = alert.get("subject", "?")
    value = alert.get("value")
    unit = alert.get("unit", "")
    check = alert.get("check", "")
    tail = f"  [{check}]" if check else ""
    if value is None:
        return f"    - {subject}{tail}"
    return f"    - {subject}  {value:g} {unit}{tail}"


def build_report(
    events: Iterable[dict[str, Any]],
    since: float,
    until: float,
    gap_hours: float = 1.0,
) -> list[str]:
    """Summarise the events between `since` and `until` as printable lines."""
    window = [e for e in events if since <= float(e.get("ts", 0)) <= until]
    runs = [e for e in window if e.get("type") == "run"]
    alerts = [e for e in window if e.get("type") == "alert"]

    crit = [a for a in alerts if a.get("level") == "crit"]
    warn = [a for a in alerts if a.get("level") == "warn"]

    minutes = int((until - since) / 60)
    span = f"{minutes // 60} h {minutes % 60} min" if minutes >= 60 else f"{minutes} min"

    lines = [
        "While you were away:",
        f"  from {_fmt(since)} to {_fmt(until)}  ({span})",
        "",
        f"Runs    : {len(runs)}",
        f"Alerts  : {len(crit)} danger, {len(warn)} attention",
    ]

    if crit:
        lines.append("  danger:")
        lines.extend(_alert_line(a) for a in crit[-5:])
    if warn:
        lines.append("  attention:")
        lines.extend(_alert_line(a) for a in warn[-5:])

    gaps: list[int] = []
    stamps = sorted(float(r.get("ts", 0)) for r in runs)
    for previous, current in zip(stamps, stamps[1:]):
        minutes_gap = int((current - previous) / 60)
        if minutes_gap > gap_hours * 60:
            gaps.append(minutes_gap)

    if gaps:
        lines.append(f"Downtime: {len(gaps)} gap(s), longest {max(gaps)} min")
    else:
        lines.append("Downtime: none")

    lines.append("")
    if crit:
        lines.append("Verdict: act on the danger items before continuing.")
    elif warn:
        lines.append("Verdict: keep an eye on the attention items.")
    else:
        lines.append("Verdict: nothing needs your attention.")
    return lines