import pytest

from web.health_data import _clean_day
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
    assert day["sleep"] == {"duration_hours": 7.5, "bedtime": "2026-07-17T23:40:00+08:00", "score": 87.0, "score_source": "garden_estimate"}


@pytest.mark.parametrize("payload", [
    {"date": "not-a-date"},
    {"date": "2026-07-18", "heart": {"resting_bpm": 4}},
    {"date": "2026-07-18", "cycle": {"flow": "unknown"}},
    {"date": "2026-07-18", "workouts": [{}]},
    {"date": "2026-07-18", "sleep": {"bedtime": "after lunch"}},
    {"date": "2026-07-18", "sleep": {"score_source": "apple"}},
])
def test_health_daily_summary_rejects_invalid_or_implausible_data(payload):
    with pytest.raises(ValueError):
        _clean_day(payload)


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
