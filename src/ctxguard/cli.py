"""Command line interface for ctxguard."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from . import state as state_mod
from .config import expand_path, load, state_dir
from .cookie import CookieCheck, MeasurementError, evaluate as evaluate_cookie, measure
from .core import Finding, Thresholds, evaluate_files, format_finding, remember
from .report import build_report
from .sources import discover

CONFIG_TEMPLATE = """\
[general]
state_dir = "~/.ctxguard"

[thresholds]
warn_mb = 3.0
crit_mb = 5.0
active_hours = 72
cooldown_hours = 12
growth_mb = 0.5

[report]
gap_hours = 1.0

# --- check 1: transcript files grow until the context window is exceeded ---
[[checks]]
kind = "file_size"
name = "dsh transcripts"
root = "~/.dsh/sessions"
pattern = "**/session.v*.jsonl.zstd"

# --- check 2 (optional): cookie jar grows once per launch until the header
#     no longer fits and the server answers 431 ---
# [[checks]]
# kind = "cookie_jar"
# name = "dsh web cookies"
# path = "%APPDATA%/@deepseek-ai/dsh-desktop/Network/Cookies"
# warn_kb = 8
# crit_kb = 12
# limit_kb = 16
"""


def _paths(config: dict[str, Any]) -> dict[str, Path]:
    base = state_dir(config)
    return {
        "base": base,
        "state": base / "state.json",
        "events": base / "events.jsonl",
        "alerts": base / "alerts.txt",
        "report": base / "report.md",
    }


def _run_file_check(
    check: dict[str, Any],
    config: dict[str, Any],
    state: dict[str, Any],
    now: float,
    findings: list[Finding],
) -> dict[str, Any]:
    name = str(check.get("name", "files"))
    root = expand_path(check.get("root", ""))
    files = discover(root, str(check.get("pattern", "**/*")))
    thresholds = Thresholds.from_config({**config.get("thresholds", {}), **check})
    found = evaluate_files(files, thresholds, state, now, name)
    findings.extend(found)
    return {
        "name": name,
        "kind": "file_size",
        "items": len(files),
        "alerts": len(found),
    }


def _run_cookie_check(
    check: dict[str, Any],
    state: dict[str, Any],
    now: float,
    findings: list[Finding],
) -> dict[str, Any]:
    jar = CookieCheck.from_config(check)
    try:
        reading = measure(expand_path(jar.path))
    except MeasurementError as exc:
        return {
            "name": jar.name,
            "kind": "cookie_jar",
            "error": str(exc),
            "alerts": 0,
        }

    found = evaluate_cookie(reading, jar, state, now)
    findings.extend(found)
    return {
        "name": jar.name,
        "kind": "cookie_jar",
        "items": reading["rows"],
        "kb": round(reading["header_bytes"] / 1024.0, 2),
        "alerts": len(found),
    }


def _run_once(config: dict[str, Any]) -> tuple[list[Finding], list[dict[str, Any]]]:
    now = time.time()
    paths = _paths(config)
    state = state_mod.load_state(paths["state"])

    findings: list[Finding] = []
    stats: list[dict[str, Any]] = []
    for check in config.get("checks", []):
        kind = check.get("kind", "file_size")
        if kind == "file_size":
            stats.append(_run_file_check(check, config, state, now, findings))
        elif kind == "cookie_jar":
            stats.append(_run_cookie_check(check, state, now, findings))
        else:
            stats.append({"name": check.get("name", "?"), "kind": kind, "error": "unknown kind"})

    state_mod.append_event(paths["events"], {"ts": now, "type": "run", "checks": len(stats)})
    for finding in findings:
        state_mod.append_event(
            paths["events"],
            {
                "ts": now,
                "type": "alert",
                "level": finding.level,
                "check": finding.check,
                "subject": finding.subject,
                "value": finding.value,
                "unit": finding.unit,
            },
        )
    if findings:
        lines = [f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---"]
        for finding in findings:
            lines.append(format_finding(finding))
        state_mod.append_lines(paths["alerts"], lines)

    remember(state, findings, now)
    state_mod.save_state(paths["state"], state)
    return findings, stats


def _describe(stat: dict[str, Any]) -> str:
    if stat.get("error"):
        return f"  ! {stat['name']} ({stat['kind']}): {stat['error']}"
    if stat["kind"] == "cookie_jar":
        return (
            f"  * {stat['name']} ({stat['kind']}): {stat['items']} cookie(s), "
            f"{stat['kb']} KB, {stat['alerts']} alert(s)"
        )
    return f"  * {stat['name']} ({stat['kind']}): {stat['items']} file(s), {stat['alerts']} alert(s)"


def cmd_scan(args: argparse.Namespace) -> int:
    config = load(Path(args.config) if args.config else None)
    findings, stats = _run_once(config)

    if args.json:
        print(json.dumps({"checks": stats, "findings": [f.__dict__ for f in findings]}, indent=2))
    else:
        print(f"ctxguard {__version__}  {time.strftime('%Y-%m-%d %H:%M:%S')}")
        for stat in stats:
            print(_describe(stat))
        print("")
        if findings:
            for finding in findings:
                print(format_finding(finding))
        else:
            print("no alerts")

    return 2 if any(f.level == "crit" for f in findings) else 0


def cmd_watch(args: argparse.Namespace) -> int:
    config = load(Path(args.config) if args.config else None)
    print(f"ctxguard {__version__} — watching every {args.interval}s (Ctrl+C to stop)")
    code = 0
    try:
        while True:
            findings, stats = _run_once(config)
            stamp = time.strftime("%H:%M:%S")
            summary = ", ".join(f"{f.subject}={f.value_text}" for f in findings) or "ok"
            print(f"[{stamp}] {len(stats)} check(s), {summary}")
            code = 2 if any(f.level == "crit" for f in findings) else 0
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nstopped")
    return code


def cmd_report(args: argparse.Namespace) -> int:
    config = load(Path(args.config) if args.config else None)
    paths = _paths(config)
    now = time.time()
    since = now - float(args.hours) * 3600
    events = state_mod.read_events(paths["events"])
    gap_hours = float(config.get("report", {}).get("gap_hours", 1.0))
    lines = build_report(events, since, now, gap_hours)

    text = "\n".join(lines)
    print(text)
    try:
        paths["report"].write_text(text + "\n", encoding="utf-8")
        print(f"\nwritten to {paths['report']}")
    except OSError as exc:
        print(f"could not write report: {exc}", file=sys.stderr)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    target = Path(args.path).expanduser()
    if target.exists() and not args.force:
        print(f"{target} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    print(f"wrote {target}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ctxguard",
        description="Read-only context-size monitor for AI agent transcripts and cookie jars.",
    )
    parser.add_argument("--version", action="version", version=f"ctxguard {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="run every check once")
    scan.add_argument("--config", help="path to config.toml")
    scan.add_argument("--json", action="store_true", help="machine-readable output")
    scan.set_defaults(func=cmd_scan)

    watch = sub.add_parser("watch", help="run every check repeatedly")
    watch.add_argument("--config", help="path to config.toml")
    watch.add_argument("--interval", type=int, default=900, help="seconds between runs (default 900)")
    watch.set_defaults(func=cmd_watch)

    report = sub.add_parser("report", help="summarise what happened while you were away")
    report.add_argument("--config", help="path to config.toml")
    report.add_argument("--hours", type=float, default=24.0, help="look back this many hours (default 24)")
    report.set_defaults(func=cmd_report)

    init = sub.add_parser("init", help="write an example config file")
    init.add_argument("path", nargs="?", default="~/.config/ctxguard/config.toml")
    init.add_argument("--force", action="store_true")
    init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())