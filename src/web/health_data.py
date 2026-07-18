"""Private, daily HealthKit summaries outside the memo store.

The iPhone companion authenticates with a dedicated, revocable sync key.
Health data is only exposed through the explicit ``check_up`` connector tool.
"""

import hashlib
import json
import os
import re
import secrets
import tempfile
from datetime import date, datetime
from typing import Any

import yaml
from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

_MAX_UPLOAD_BYTES = 256 * 1024
_MAX_DAYS = 400
_FLOW_VALUES = {"none", "light", "medium", "heavy"}
# Keep Shanghai as a compatibility alias for configurations saved by the last
# release; new UI presents the preferred Hong Kong label instead.
_TIMEZONES = {"UTC", "Asia/Hong_Kong", "Asia/Shanghai", "America/Los_Angeles", "America/New_York", "Europe/London", "Europe/Paris"}
_MEMO_NAME_TIMESTAMP = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}-\d{2}-\d{2})(?:\s|$)")


def _config() -> dict[str, Any]:
    return sh.config.setdefault("health_sync", {})


def _timezone() -> str:
    value = str(sh.config.get("timezone") or _config().get("timezone") or "UTC")
    return value if value in _TIMEZONES else "UTC"


def _save_config() -> None:
    try:
        from utils import config_file_path  # type: ignore
    except ImportError:  # pragma: no cover
        from ..utils import config_file_path  # type: ignore
    path = config_file_path()
    saved: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            saved = yaml.safe_load(handle) or {}
    saved["health_sync"] = dict(_config())
    saved["timezone"] = _timezone()
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(saved, handle, allow_unicode=True, default_flow_style=False)


def _data_path() -> str:
    directory = os.path.join(str(sh.config["buckets_dir"]), ".health")
    os.makedirs(directory, exist_ok=True)
    return os.path.join(directory, "daily_summaries.json")


def _read_store() -> dict[str, Any]:
    try:
        with open(_data_path(), "r", encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) and isinstance(value.get("daily"), dict) else {"version": 1, "daily": {}}
    except FileNotFoundError:
        return {"version": 1, "daily": {}}
    except (OSError, json.JSONDecodeError):
        sh.logger.warning("health summary store is unreadable", exc_info=True)
        return {"version": 1, "daily": {}}


def _write_store(store: dict[str, Any]) -> None:
    path = _data_path()
    fd, temporary = tempfile.mkstemp(prefix="health-", suffix=".json", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(store, handle, ensure_ascii=False, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _number(value: Any, name: str, minimum: float = 0, maximum: float = 1000000) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是数字")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数字") from exc
    if not minimum <= number <= maximum:
        raise ValueError(f"{name} 超出合理范围")
    return round(number, 3)


def _clean_day(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("daily 必须是对象数组")
    raw_date = str(item.get("date") or "")
    try:
        day = date.fromisoformat(raw_date).isoformat()
    except ValueError as exc:
        raise ValueError("日期必须是 YYYY-MM-DD") from exc
    result: dict[str, Any] = {"date": day}
    for section, fields in {
        "sleep": {"duration_hours": (0, 24), "score": (0, 100)},
        "activity": {"steps": (0, 200000), "active_energy_kcal": (0, 30000)},
        "heart": {"resting_bpm": (20, 250), "hrv_ms": (0, 500)},
        "vitals": {"respiratory_rate": (0, 80), "wrist_temperature_c": (25, 45), "blood_oxygen_pct": (0, 100)},
    }.items():
        source = item.get(section)
        if source is None:
            continue
        if not isinstance(source, dict):
            raise ValueError(f"{section} 必须是对象")
        clean = {key: _number(source.get(key), key, *bounds) for key, bounds in fields.items() if source.get(key) is not None}
        if clean:
            result[section] = clean
    cycle = item.get("cycle")
    if cycle is not None:
        if not isinstance(cycle, dict):
            raise ValueError("cycle 必须是对象")
        clean_cycle: dict[str, Any] = {}
        if cycle.get("flow") is not None:
            if cycle["flow"] not in _FLOW_VALUES:
                raise ValueError("cycle.flow 无效")
            clean_cycle["flow"] = cycle["flow"]
        if cycle.get("cycle_start") is not None:
            if not isinstance(cycle["cycle_start"], bool):
                raise ValueError("cycle.cycle_start 必须是布尔值")
            clean_cycle["cycle_start"] = cycle["cycle_start"]
        if clean_cycle:
            result["cycle"] = clean_cycle
    workouts = item.get("workouts")
    if workouts is not None:
        if not isinstance(workouts, list) or len(workouts) > 30:
            raise ValueError("workouts 必须是不超过 30 条的数组")
        clean_workouts = []
        for workout in workouts:
            if not isinstance(workout, dict) or not str(workout.get("type") or "").strip():
                raise ValueError("workout 需要 type")
            clean_workouts.append({
                "id": str(workout.get("id") or "").strip()[:160],
                "type": str(workout["type"]).strip()[:80],
                "start": str(workout.get("start") or "")[:40],
                **{key: _number(workout.get(key), key, *bounds) for key, bounds in {
                    "duration_minutes": (0, 1440), "active_energy_kcal": (0, 30000),
                    "average_heart_rate_bpm": (20, 250), "max_heart_rate_bpm": (20, 300),
                }.items() if workout.get(key) is not None},
            })
        result["workouts"] = clean_workouts
    return result


def _sync_key_ok(request: Request) -> bool:
    expected = str(_config().get("token_hash") or "")
    supplied = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return bool(expected and supplied and secrets.compare_digest(expected, hashlib.sha256(supplied.encode()).hexdigest()))


def _status() -> dict[str, Any]:
    return {
        "configured": bool(_config().get("token_hash")), "key_set": bool(_config().get("token_hash")),
        "timezone": _timezone(),
    }


async def _restore_memo_timestamps_from_titles() -> int:
    """Restore pre-marker memo timestamps from their already-correct title.

    A previous repair path could move ``created`` and ``last_active`` more than
    once.  The title prefix is the stable civil-time source for those old
    records (for example ``2026-07-15 10-28-34``).  ``last_active`` is only
    restored when it is still identical to the bad historical ``created``
    value; a genuinely later activation is preserved.  Each repaired memo gets
    a timezone marker, so this operation is idempotent and never moves it again.
    """
    restored = 0
    for bucket in await sh.bucket_mgr.list_all(include_archive=True):
        if sh.is_sterling_journal(bucket):
            continue
        meta = bucket.get("metadata") or {}
        if meta.get("timestamp_timezone"):
            continue
        match = _MEMO_NAME_TIMESTAMP.match(str(meta.get("name") or ""))
        if not match:
            continue
        try:
            timestamp = datetime.strptime(match.group(1), "%Y-%m-%d %H-%M-%S").isoformat(timespec="seconds")
        except ValueError:
            continue
        updates: dict[str, str] = {"created": timestamp, "timestamp_timezone": _timezone()}
        # Old migration runs moved both fields together.  Do not overwrite a
        # later real activation: only fix last_active while it still equals the
        # malformed created timestamp.
        if str(meta.get("last_active") or "") == str(meta.get("created") or ""):
            updates["last_active"] = timestamp
        changed = await sh.bucket_mgr.update(bucket["id"], **updates)
        if changed:
            restored += 1
    return restored


def register(mcp) -> None:
    from starlette.responses import JSONResponse

    @mcp.custom_route("/api/health/summary", methods=["GET"])
    async def health_summary(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        daily = _read_store()["daily"]
        entries = [daily[key] for key in sorted(daily, reverse=True)[:30]]
        return JSONResponse({"ok": True, "days": entries, "count": len(daily)})

    @mcp.custom_route("/api/health/sync/status", methods=["GET"])
    async def health_status(request: Request) -> Response:
        err = sh._require_auth(request)
        return err or JSONResponse({"ok": True, **_status()})

    @mcp.custom_route("/api/health/sync/config", methods=["POST"])
    async def health_config(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("配置必须是 JSON 对象")
            if "timezone" in body:
                timezone = str(body.get("timezone") or "UTC")
                if timezone not in _TIMEZONES:
                    raise ValueError("不支持的时区")
                _config()["timezone"] = timezone
                sh.config["timezone"] = timezone
                try:
                    from utils import configure_timezone  # type: ignore
                except ImportError:  # pragma: no cover
                    from ..utils import configure_timezone  # type: ignore
                configure_timezone(timezone)
            key = ""
            if body.get("rotate_key") or not _config().get("token_hash"):
                key = secrets.token_urlsafe(32)
                _config()["token_hash"] = hashlib.sha256(key.encode()).hexdigest()
            restored_memos = await _restore_memo_timestamps_from_titles() if body.get("restore_memo_timestamps_from_titles") else 0
            _save_config()
            return JSONResponse({"ok": True, **_status(), "sync_key": key, "restored_memos": restored_memos})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/health/sync/push", methods=["POST"])
    async def health_push(request: Request) -> Response:
        if not _sync_key_ok(request):
            return JSONResponse({"ok": False, "error": "Health 同步未获授权"}, status_code=401)
        try:
            raw = await request.body()
            if len(raw) > _MAX_UPLOAD_BYTES:
                raise ValueError("Health 同步内容不能超过 256 KB")
            payload = json.loads(raw.decode("utf-8"))
            days = payload.get("daily") if isinstance(payload, dict) else None
            if not isinstance(days, list) or len(days) > _MAX_DAYS:
                raise ValueError("daily 必须是不超过 400 天的数组")
            cleaned = [_clean_day(day) for day in days]
            store = _read_store()
            store["daily"].update({day["date"]: day for day in cleaned})
            _write_store(store)
            return JSONResponse({"ok": True, "received": len(cleaned), "stored_days": len(store["daily"])})
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception:
            sh.logger.exception("health sync push failed")
            return JSONResponse({"ok": False, "error": "Health 同步失败"}, status_code=500)
