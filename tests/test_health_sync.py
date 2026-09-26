import pytest
import ast
import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from web.health_data import _clean_day, _prune_daily
from web import health_data


def test_health_daily_summary_accepts_only_the_small_daily_schema():
    day = _clean_day({
        "date": "2026-07-18",
        "sleep": {"duration_hours": 7.5, "bedtime": "2026-07-17T23:40:00+08:00", "score": 87, "score_source": "garden_estimate"},
        "activity": {"steps": 4567},
        "heart": {"resting_bpm": 58, "hrv_ms": 42},
        "cycle": {"flow": "light", "cycle_start": True},
        "workouts": [{"id": "run-1", "type": "running", "duration_minutes": 25}],
    })
    assert day["date"] == "2026-07-18"
    assert day["activity"]["steps"] == 4567
    assert day["cycle"]["flow"] == "light"
    # Scores from older companion builds are deliberately discarded: Apple
    # Health has no portable sleep-score field, and Garden no longer estimates one.
    assert day["sleep"] == {"duration_hours": 7.5, "bedtime": "2026-07-17T23:40:00+08:00"}


@pytest.mark.parametrize("payload", [
    {"date": "not-a-date"},
    {"date": "2026-07-18", "heart": {"resting_bpm": 4}},
    {"date": "2026-07-18", "cycle": {"flow": "unknown"}},
    {"date": "2026-07-18", "workouts": [{}]},
    {"date": "2026-07-18", "sleep": {"bedtime": "after lunch"}},
])
def test_health_daily_summary_rejects_invalid_or_implausible_data(payload):
    with pytest.raises(ValueError):
        _clean_day(payload)


def test_health_daily_store_keeps_only_the_newest_30_days(monkeypatch):
    monkeypatch.setattr(health_data, "_today", lambda: date(2026, 7, 31))
    store = {"daily": {
        f"2026-07-{day:02d}": {"date": f"2026-07-{day:02d}"}
        for day in range(1, 32)
    }}

    _prune_daily(store)

    assert len(store["daily"]) == 30
    assert "2026-07-01" not in store["daily"]
    assert "2026-07-31" in store["daily"]


def test_health_retention_expires_sparse_old_days_but_preserves_future_dates(monkeypatch):
    monkeypatch.setattr(health_data, "_today", lambda: date(2026, 9, 26))
    dates = ["2025-01-01", "2026-08-27", "2026-08-28", "2026-09-26", "2026-10-01"]
    store = {"daily": {day: {"date": day} for day in dates}}

    assert _prune_daily(store) is True
    assert list(store["daily"]) == dates[2:]
    assert _prune_daily(store) is False


def test_health_read_expires_records_without_another_sync(tmp_path, monkeypatch):
    path = tmp_path / "health.json"
    path.write_text(json.dumps({"version": 1, "daily": {
        "2026-08-27": {"date": "2026-08-27"},
        "2026-09-26": {"date": "2026-09-26"},
    }}), encoding="utf-8")
    monkeypatch.setattr(health_data, "_data_path", lambda: str(path))
    monkeypatch.setattr(health_data, "_today", lambda: date(2026, 9, 26))

    assert list(health_data._read_store()["daily"]) == ["2026-09-26"]
    assert list(json.loads(path.read_text(encoding="utf-8"))["daily"]) == ["2026-09-26"]
    monkeypatch.setattr(health_data, "_today", lambda: date(2026, 10, 26))
    assert health_data.read_daily_summaries() == []
    assert json.loads(path.read_text(encoding="utf-8"))["daily"] == {}


def test_health_query_uses_calendar_days_not_record_count(monkeypatch):
    monkeypatch.setattr(health_data, "_today", lambda: date(2026, 9, 26))
    dates = ["2026-09-27", "2026-09-26", "2026-09-20", "2026-09-19", "2026-08-28"]
    monkeypatch.setattr(health_data, "_read_store", lambda: {
        "daily": {day: {"date": day} for day in dates},
    })

    assert [row["date"] for row in health_data.read_daily_summaries(7)] == dates[1:3]
    assert [row["date"] for row in health_data.read_daily_summaries(1)] == dates[1:2]


def test_health_calendar_uses_configured_timezone(monkeypatch):
    class FixedDatetime:
        @staticmethod
        def now(zone):
            return datetime.fromisoformat("2026-09-25T17:00:00+00:00").astimezone(zone)

    monkeypatch.setattr(health_data, "datetime", FixedDatetime)
    monkeypatch.setattr(health_data.sh, "config", {"timezone": "Asia/Hong_Kong"})
    assert health_data._today() == date(2026, 9, 26)
    monkeypatch.setattr(health_data.sh, "config", {"timezone": "UTC"})
    assert health_data._today() == date(2026, 9, 25)


@pytest.mark.asyncio
async def test_check_up_uses_shared_calendar_reader(monkeypatch):
    # Load only this tool function: importing server starts real runtime services.
    source = Path(__file__).resolve().parents[1] / "src" / "server.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    function = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef) and node.name == "check_up")
    function.decorator_list = []
    namespace = {"Optional": Optional, "json": json}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"), namespace)
    requested = []

    def read_days(days):
        requested.append(days)
        return [{"date": "2026-09-26"}]

    monkeypatch.setattr(health_data, "read_daily_summaries", read_days)
    assert "2026-09-26" in await namespace["check_up"](7)
    assert requested == [7]


@pytest.mark.asyncio
async def test_restore_memo_times_uses_title_once_and_skips_journals(monkeypatch):
    class Manager:
        def __init__(self):
            self.buckets = [
                {"id": "memo", "metadata": {"name": "2026-07-15 10-28-34 a memo", "created": "2026-07-15T18:28:34", "last_active": "2026-07-15T18:28:34"}},
                {"id": "journal", "metadata": {"name": "2026-07-15 10-28-34 journal", "source_tool": "sterling"}},
            ]
            self.updates = []

        async def list_all(self, include_archive=False):
            return self.buckets

        async def update(self, bucket_id, **updates):
            self.updates.append((bucket_id, updates))
            return True

    manager = Manager()
    monkeypatch.setattr(health_data.sh, "config", {"timezone": "Asia/Hong_Kong"})
    monkeypatch.setattr(health_data.sh, "bucket_mgr", manager)

    assert await health_data._restore_memo_timestamps_from_titles() == 1
    assert manager.updates == [("memo", {
        "created": "2026-07-15T10:28:34", "last_active": "2026-07-15T10:28:34",
        "timestamp_timezone": "Asia/Hong_Kong",
    })]


@pytest.mark.asyncio
async def test_restore_memo_times_preserves_a_later_real_activation(monkeypatch):
    class Manager:
        async def list_all(self, include_archive=False):
            return [{"id": "memo", "metadata": {
                "name": "2026-07-15 10-28-34 a memo",
                "created": "2026-07-15T18:28:34", "last_active": "2026-07-19T09:00:00",
            }}]

        async def update(self, bucket_id, **updates):
            self.updates = updates
            return True

    manager = Manager()
    monkeypatch.setattr(health_data.sh, "config", {"timezone": "Asia/Hong_Kong"})
    monkeypatch.setattr(health_data.sh, "bucket_mgr", manager)

    assert await health_data._restore_memo_timestamps_from_titles() == 1
    assert manager.updates == {
        "created": "2026-07-15T10:28:34", "timestamp_timezone": "Asia/Hong_Kong",
    }
