"""Tests for the check logic, cookie measurement, and the state/report helpers."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ctxguard import state as state_mod
from ctxguard.cookie import CookieCheck, MeasurementError, evaluate as evaluate_cookie, measure
from ctxguard.core import (
    Finding,
    Thresholds,
    evaluate_files,
    format_finding,
    remember,
    should_alert,
)
from ctxguard.report import build_report
from ctxguard.sources import FileInfo, summarize

MB = 1024 * 1024
NOW = 1_800_000_000.0
TH = Thresholds(warn_mb=3.0, crit_mb=5.0, active_hours=72.0, cooldown_hours=12.0, growth_mb=0.5)


def make_file(size_mb: float, age_hours: float = 0.5, name: str = "s1") -> FileInfo:
    return FileInfo(
        path=f"/tmp/{name}/session.v4.jsonl.zstd",
        session_id=f"ws/{name}/session.v4.jsonl.zstd",
        size=int(size_mb * MB),
        mtime=NOW - age_hours * 3600,
    )


def tmp_dir(test: unittest.TestCase) -> Path:
    base = Path(__file__).resolve().parent / "_tmp"
    base.mkdir(exist_ok=True)
    path = Path(tempfile.mkdtemp(dir=base))
    test.addCleanup(shutil.rmtree, path, True)
    return path


def make_cookie_db(path: Path, local: int = 0, other: int = 0, value_len: int = 230) -> Path:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE cookies(creation_utc INTEGER, host_key TEXT, name TEXT, value TEXT,"
        " encrypted_value BLOB, path TEXT, expires_utc INTEGER, is_secure INTEGER,"
        " is_httponly INTEGER, last_access_utc INTEGER, has_expires INTEGER,"
        " is_persistent INTEGER, priority INTEGER, samesite INTEGER, source_scheme INTEGER,"
        " source_port INTEGER, is_monolithic INTEGER, last_update_utc INTEGER)"
    )
    for host, count in (("127.0.0.1", local), ("example.com", other)):
        for i in range(count):
            con.execute(
                "INSERT INTO cookies VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (13000000000000000 + i, host, "token", "x" * value_len, b"", "/", 0, 0, 1,
                 0, 0, 0, 1, -1, 1, 52305, 0, 0),
            )
    con.commit()
    con.close()
    return path


class FileCheckTests(unittest.TestCase):
    def test_below_warn_is_ignored(self):
        self.assertEqual(evaluate_files([make_file(2.9)], TH, {}, NOW, "t"), [])

    def test_warn_level(self):
        findings = evaluate_files([make_file(3.5)], TH, {}, NOW, "t")
        self.assertEqual(findings[0].level, "warn")
        self.assertEqual(findings[0].unit, "MB")

    def test_crit_level(self):
        self.assertEqual(evaluate_files([make_file(6.1)], TH, {}, NOW, "t")[0].level, "crit")

    def test_stale_file_is_ignored(self):
        self.assertEqual(evaluate_files([make_file(8.0, age_hours=100)], TH, {}, NOW, "t"), [])

    def test_cooldown_suppresses_repeat(self):
        state = {
            "sessions": {
                "t/ws/s1/session.v4.jsonl.zstd": {"last_alert_ts": NOW - 3600, "last_value": 3.4}
            }
        }
        self.assertEqual(evaluate_files([make_file(3.4)], TH, state, NOW, "t"), [])

    def test_growth_overrides_cooldown(self):
        state = {
            "sessions": {
                "t/ws/s1/session.v4.jsonl.zstd": {"last_alert_ts": NOW - 3600, "last_value": 3.4}
            }
        }
        self.assertEqual(len(evaluate_files([make_file(4.2)], TH, state, NOW, "t")), 1)

    def test_sorted_by_size(self):
        findings = evaluate_files(
            [make_file(3.1, name="a"), make_file(6.0, name="b")], TH, {}, NOW, "t"
        )
        self.assertEqual(
            [f.subject for f in findings], ["b/session.v4.jsonl.zstd", "a/session.v4.jsonl.zstd"]
        )

    def test_format_mentions_check(self):
        finding = evaluate_files([make_file(6.1)], TH, {}, NOW, "transcripts")[0]
        text = format_finding(finding)
        self.assertIn("CRIT", text)
        self.assertIn("transcripts", text)


class PolicyTests(unittest.TestCase):
    def test_no_record_alerts(self):
        self.assertTrue(should_alert(None, 4.0, NOW, 12, 0.5))

    def test_disabled_growth_never_escalates(self):
        record = {"last_alert_ts": NOW - 60, "last_value": 1}
        self.assertFalse(should_alert(record, 999, NOW, 12, 1e9))

    def test_remember_records_value(self):
        finding = Finding(check="c", subject="s", level="warn", value=4.0, unit="MB")
        state = remember({}, [finding], NOW)
        self.assertEqual(state["sessions"]["c/s"]["last_value"], 4.0)


class CookieTests(unittest.TestCase):
    def test_measure_counts_header_bytes(self):
        db = make_cookie_db(tmp_dir(self) / "Cookies", local=5, other=2, value_len=100)
        reading = measure(db)
        self.assertEqual(reading["rows"], 7)
        self.assertEqual(reading["hosts"]["127.0.0.1"]["count"], 5)
        # 100 value + len("token") + 2 separator = 107 per cookie
        self.assertEqual(reading["hosts"]["127.0.0.1"]["bytes"], 5 * 107)

    def test_below_warn_is_quiet(self):
        db = make_cookie_db(tmp_dir(self) / "Cookies", local=2, value_len=50)
        check = CookieCheck(name="jar", path=str(db))
        self.assertEqual(evaluate_cookie(measure(db), check, {}, NOW), [])

    def test_warn_or_crit_when_large(self):
        db = make_cookie_db(tmp_dir(self) / "Cookies", local=40, value_len=230)
        check = CookieCheck(name="jar", path=str(db), warn_kb=8, crit_kb=12, limit_kb=16)
        level = evaluate_cookie(measure(db), check, {}, NOW)[0].level
        self.assertIn(level, ("warn", "crit"))

    def test_cooldown_applies(self):
        db = make_cookie_db(tmp_dir(self) / "Cookies", local=60, value_len=230)
        check = CookieCheck(name="jar", path=str(db))
        reading = measure(db)
        state = remember({}, evaluate_cookie(reading, check, {}, NOW), NOW)
        self.assertEqual(evaluate_cookie(reading, check, state, NOW + 3600), [])

    def test_missing_jar_raises(self):
        with self.assertRaises(MeasurementError):
            measure(Path("nope") / "Cookies")


class ReportTests(unittest.TestCase):
    def test_gap_detection(self):
        events = [
            {"ts": NOW - 5 * 3600, "type": "run"},
            {"ts": NOW - 4.8 * 3600, "type": "run"},
            {"ts": NOW - 1 * 3600, "type": "run"},
        ]
        self.assertIn("1 gap(s)", "\n".join(build_report(events, NOW - 6 * 3600, NOW, 1.0)))

    def test_alert_rendered_with_unit(self):
        events = [
            {"ts": NOW - 60, "type": "run"},
            {
                "ts": NOW - 30,
                "type": "alert",
                "level": "crit",
                "check": "jar",
                "subject": "Cookies",
                "value": 14.4,
                "unit": "KB",
            },
        ]
        text = "\n".join(build_report(events, NOW - 3600, NOW))
        self.assertIn("1 danger", text)
        self.assertIn("14.4 KB", text)

    def test_clean_verdict(self):
        text = "\n".join(build_report([{"ts": NOW - 60, "type": "run"}], NOW - 3600, NOW))
        self.assertIn("nothing needs your attention", text)


class StateTests(unittest.TestCase):
    def test_roundtrip(self):
        path = tmp_dir(self) / "state.json"
        state_mod.save_state(path, {"sessions": {"a": {"last_value": 4.2}}})
        self.assertEqual(state_mod.load_state(path)["sessions"]["a"]["last_value"], 4.2)

    def test_corrupt_state_is_tolerated(self):
        path = tmp_dir(self) / "state.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertEqual(state_mod.load_state(path), {})

    def test_events_appended(self):
        path = tmp_dir(self) / "events.jsonl"
        state_mod.append_event(path, {"ts": 1, "type": "run"})
        state_mod.append_event(path, {"ts": 2, "type": "alert"})
        self.assertEqual(len(state_mod.read_events(path)), 2)


class SummaryTests(unittest.TestCase):
    def test_summarize(self):
        result = summarize([make_file(2.0, name="a"), make_file(4.0, name="b")])
        self.assertEqual(result["count"], 2.0)
        self.assertEqual(result["largest_mb"], 4.0)


if __name__ == "__main__":
    unittest.main()