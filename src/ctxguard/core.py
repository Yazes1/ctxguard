"""Findings, thresholds and the alert policy shared by every check."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .sources import FileInfo


@dataclass(frozen=True)
class Thresholds:
    warn_mb: float = 3.0
    crit_mb: float = 5.0
    active_hours: float = 72.0
    cooldown_hours: float = 12.0
    growth_mb: float = 0.5

    @classmethod
    def from_config(cls, section: dict[str, Any]) -> "Thresholds":
        return cls(
            warn_mb=float(section.get("warn_mb", 3.0)),
            crit_mb=float(section.get("crit_mb", 5.0)),
            active_hours=float(section.get("active_hours", 72)),
            cooldown_hours=float(section.get("cooldown_hours", 12)),
            growth_mb=float(section.get("growth_mb", 0.5)),
        )


@dataclass(frozen=True)
class Finding:
    """One thing worth telling the user about."""

    check: str
    subject: str
    level: str  # "warn" | "crit"
    value: float
    unit: str  # "MB" | "KB"
    detail: str = ""

    @property
    def key(self) -> str:
        return f"{self.check}/{self.subject}"

    @property
    def value_text(self) -> str:
        return f"{self.value:.2f} {self.unit}"


def should_alert(
    record: dict[str, Any] | None,
    value: float,
    now: float,
    cooldown_hours: float,
    growth: float,
) -> bool:
    """Cooldown with a growth override.

    Something already reported stays quiet for `cooldown_hours`, unless it grew
    by at least `growth` since the last report.
    """
    if not record:
        return True
    last_ts = float(record.get("last_alert_ts") or 0)
    if not last_ts:
        return True
    hours = (now - last_ts) / 3600.0
    grew = value - float(record.get("last_value") or 0)
    return hours >= cooldown_hours or grew >= growth


def remember(state: dict[str, Any], findings: list[Finding], now: float) -> dict[str, Any]:
    sessions = state.setdefault("sessions", {})
    for finding in findings:
        sessions[finding.key] = {
            "last_alert_ts": now,
            "last_value": finding.value,
            "last_level": finding.level,
        }
    state["last_run_ts"] = now
    return state


def evaluate_files(
    files: list[FileInfo],
    thresholds: Thresholds,
    state: dict[str, Any],
    now: float,
    check: str,
) -> list[Finding]:
    """Transcript-size check.

    Rules, in order:
      1. below the warn threshold          -> ignore
      2. untouched for `active_hours`      -> ignore (stale, cannot grow)
      3. reported recently, barely grown   -> ignore (cooldown)
    """
    findings: list[Finding] = []
    sessions: dict[str, Any] = state.get("sessions", {}) or {}

    for info in files:
        size_mb = info.size_mb
        if size_mb < thresholds.warn_mb:
            continue
        if (now - info.mtime) / 3600.0 > thresholds.active_hours:
            continue

        level = "crit" if size_mb >= thresholds.crit_mb else "warn"
        key = f"{check}/{info.session_id}"
        if not should_alert(
            sessions.get(key), size_mb, now, thresholds.cooldown_hours, thresholds.growth_mb
        ):
            continue

        limit = thresholds.crit_mb if level == "crit" else thresholds.warn_mb
        findings.append(
            Finding(
                check=check,
                subject=info.label,
                level=level,
                value=size_mb,
                unit="MB",
                detail=f"{info.path} (limit {limit:g} MB)",
            )
        )

    findings.sort(key=lambda f: f.value, reverse=True)
    return findings


def format_finding(finding: Finding) -> str:
    text = f"[{finding.level.upper()}] {finding.value_text} - {finding.subject}  ({finding.check})"
    if finding.detail:
        text += f"\n    {finding.detail}"
    return text