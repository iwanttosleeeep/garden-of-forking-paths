import pytest

from web.health_data import _clean_day
from web import health_data


def test_health_daily_summary_accepts_only_the_small_daily_schema():
    day = _clean_day({
        "date": "2026-07-18",
        "sleep": {"duration_hours": 7.5},
        "activity": {"steps": 4567},
        "heart": {"resting_bpm": 58, "hrv_ms": 42},
        "cycle": {"flow": "light", "cycle_start": True},
        "workouts": [{"id": "run-1", "type": "running", "duration_minutes": 25}],
    })
    assert day["date"] == "2026-07-18"
    assert day["activity"]["steps"] == 4567
    assert day["cycle"]["flow"] == "light"


@pytest.mark.parametrize("payload", [
    {"date": "not-a-date"},
    {"date": "2026-07-18", "heart": {"resting_bpm": 4}},
    {"date": "2026-07-18", "cycle": {"flow": "unknown"}},
    {"date": "2026-07-18", "workouts": [{}]},
])
def test_health_daily_summary_rejects_invalid_or_implausible_data(payload):
    with pytest.raises(ValueError):
        _clean_day(payload)


def test_legacy_health_dates_are_shifted_once_for_positive_utc_offset(monkeypatch):
    store = {"version": 1, "daily": {"2026-07-17": {"date": "2026-07-17", "activity": {"steps": 100}}}}
    monkeypatch.setattr(health_data.sh, "config", {"health_sync": {"timezone": "Asia/Shanghai"}})
    monkeypatch.setattr(health_data, "_read_store", lambda: store)
    monkeypatch.setattr(health_data, "_write_store", lambda value: None)

    assert health_data._repair_legacy_dates() == 1
    assert store["daily"]["2026-07-18"]["activity"]["steps"] == 100


def test_legacy_health_dates_stay_put_for_utc(monkeypatch):
    store = {"version": 1, "daily": {"2026-07-18": {"date": "2026-07-18"}}}
    monkeypatch.setattr(health_data.sh, "config", {"health_sync": {"timezone": "UTC"}})
    monkeypatch.setattr(health_data, "_read_store", lambda: store)
    monkeypatch.setattr(health_data, "_write_store", lambda value: None)

    assert health_data._repair_legacy_dates() == 0
    assert list(store["daily"]) == ["2026-07-18"]


def test_health_duplicate_cleanup_keeps_the_later_local_day(monkeypatch):
    store = {"version": 1, "daily": {
        "2026-07-17": {"date": "2026-07-17", "activity": {"steps": 100}},
        "2026-07-18": {"date": "2026-07-18", "activity": {"steps": 100}},
    }}
    monkeypatch.setattr(health_data, "_read_store", lambda: store)
    monkeypatch.setattr(health_data, "_write_store", lambda value: None)

    assert health_data._remove_adjacent_duplicate_days() == 1
    assert list(store["daily"]) == ["2026-07-18"]
