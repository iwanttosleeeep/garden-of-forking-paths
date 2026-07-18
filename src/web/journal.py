"""Sterling journal import and mood-summary endpoints.

The Garden deliberately receives an exported JSON file instead of attempting to
reach into Sterling's browser storage.  Imported entries stay regular Markdown
memos, marked ``dont_surface``, so they are available to the dedicated journal
tool without becoming unsolicited conversational context.
"""

import json
from collections import defaultdict
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_ENTRIES = 500


def _entries_from_export(payload: Any) -> list[dict[str, Any]]:
    """Accept Sterling's current export shape and its vault-only variant."""
    if not isinstance(payload, dict):
        raise ValueError("Sterling 导出文件必须是 JSON 对象")
    vault = payload.get("vault") if isinstance(payload.get("vault"), dict) else payload
    entries = vault.get("entries") if isinstance(vault, dict) else None
    if not isinstance(entries, list):
        raise ValueError("没有找到 Sterling entries；请从 Sterling 的导出功能生成 JSON")
    if len(entries) > _MAX_ENTRIES:
        raise ValueError(f"一次最多导入 {_MAX_ENTRIES} 条日记")
    return [entry for entry in entries if isinstance(entry, dict)]


def _journal_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10]


def _mood(value: Any) -> int | None:
    try:
        mood = int(value)
    except (TypeError, ValueError):
        return None
    return mood if 1 <= mood <= 5 else None


def _entry_content(entry: dict[str, Any], date: str, mood: int | None) -> str:
    note = str(entry.get("note") or "").strip()
    echo = str(entry.get("lyrics") or entry.get("echo") or "").strip()
    stamp = date or "未标注日期"
    mood_text = f"心情 {mood}/5" if mood is not None else "未记录心情"
    parts = [f"Sterling 日记 · {stamp} · {mood_text}"]
    if note:
        parts.append(note)
    if echo:
        parts.append(f"回声：{echo}")
    # A mood-only record is still useful for the curve.
    return "\n\n".join(parts)


def _summary_from_buckets(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    journals = [
        bucket for bucket in buckets
        if (bucket.get("metadata") or {}).get("source_tool") == "sterling"
        and not (bucket.get("metadata") or {}).get("deleted_at")
    ]
    days: dict[str, list[int]] = defaultdict(list)
    for bucket in journals:
        meta = bucket.get("metadata") or {}
        date = str(meta.get("journal_date") or meta.get("created") or "")[:10]
        mood = _mood(meta.get("journal_mood"))
        if date and mood is not None:
            days[date].append(mood)
    curve = [
        {"date": date, "mood": round(sum(values) / len(values), 2), "count": len(values)}
        for date, values in sorted(days.items())[-30:]
    ]
    all_moods = [point["mood"] for point in curve]
    return {
        "count": len(journals),
        "mood_count": sum(point["count"] for point in curve),
        "average_mood": round(sum(all_moods) / len(all_moods), 2) if all_moods else None,
        "curve": curve,
    }


def register(mcp) -> None:
    @mcp.custom_route("/api/journal/summary", methods=["GET"])
    async def api_journal_summary(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            buckets = await sh.bucket_mgr.list_all(include_archive=False)
            return JSONResponse({"ok": True, **_summary_from_buckets(buckets)})
        except Exception as exc:
            sh.logger.exception("journal summary failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @mcp.custom_route("/api/journal/sterling", methods=["POST"])
    async def api_import_sterling(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("请选择 Sterling 导出的 .json 文件")
            raw = await upload.read()
            if len(raw) > _MAX_UPLOAD_BYTES:
                raise ValueError("日记导出文件不能超过 2 MB")
            entries = _entries_from_export(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"读取文件失败：{exc}"}, status_code=400)

        try:
            existing = await sh.bucket_mgr.list_all(include_archive=True)
            existing_ids = {
                str((bucket.get("metadata") or {}).get("journal_source_id"))
                for bucket in existing
                if (bucket.get("metadata") or {}).get("source_tool") == "sterling"
            }
            imported = skipped = 0
            for entry in entries:
                source_id = str(entry.get("id") or "").strip()
                if not source_id:
                    skipped += 1
                    continue
                if source_id in existing_ids:
                    skipped += 1
                    continue
                date = _journal_date(entry.get("timestamp") or entry.get("createdAt"))
                mood = _mood(entry.get("mood"))
                original_tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
                tags = ["__journal__", "source:sterling"]
                if mood is not None:
                    tags.append(f"mood:{mood}")
                tags.extend(str(tag).strip()[:80] for tag in original_tags if str(tag).strip())
                bucket_id = await sh.bucket_mgr.create(
                    content=_entry_content(entry, date, mood),
                    tags=tags,
                    importance=3,
                    domain=["日记"],
                    valence=((mood - 1) / 4) if mood is not None else 0.5,
                    arousal=0.3,
                    bucket_type="journal",
                    name=f"Sterling {date}" if date else "Sterling 日记",
                    source_tool="sterling",
                )
                await sh.bucket_mgr.update(
                    bucket_id,
                    dont_surface=True,
                    journal_date=date,
                    journal_source_id=source_id,
                    journal_mood=mood,
                )
                existing_ids.add(source_id)
                imported += 1
            buckets = await sh.bucket_mgr.list_all(include_archive=False)
            return JSONResponse({"ok": True, "imported": imported, "skipped": skipped,
                                 **_summary_from_buckets(buckets)})
        except Exception as exc:
            sh.logger.exception("Sterling import failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)
