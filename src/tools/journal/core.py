"""Bounded, explicit journal retrieval for the MCP-facing AI."""

from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from .. import _runtime as rt
from web._shared import is_sterling_journal


def _mood(value) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if 1 <= result <= 5 else None


async def journal_core(
    query: Optional[str] = "",
    days: Optional[int] = 30,
    limit: Optional[int] = 8,
) -> str:
    """Return a summary by default; return entries only for an explicit query."""
    try:
        days = max(1, min(365, int(days if days is not None else 30)))
        limit = max(1, min(20, int(limit if limit is not None else 8)))
    except (TypeError, ValueError, OverflowError):
        return "days 和 limit 必须是数字。"
    query = str(query or "").strip().lower()
    try:
        buckets = await rt.bucket_mgr.list_all(include_archive=False)
    except Exception as exc:
        return f"读取日记失败: {exc}"
    cutoff = (date.today() - timedelta(days=days - 1)).isoformat()
    journals = []
    for bucket in buckets:
        meta = bucket.get("metadata") or {}
        if not is_sterling_journal(bucket):
            continue
        entry_date = str(meta.get("journal_date") or meta.get("created") or "")[:10]
        if entry_date and entry_date < cutoff:
            continue
        journals.append(bucket)
    journals.sort(key=lambda bucket: str((bucket.get("metadata") or {}).get("journal_date") or ""), reverse=True)

    moods_by_day: dict[str, list[int]] = defaultdict(list)
    for bucket in journals:
        meta = bucket.get("metadata") or {}
        mood = _mood(meta.get("journal_mood"))
        entry_date = str(meta.get("journal_date") or meta.get("created") or "")[:10]
        if mood is not None and entry_date:
            moods_by_day[entry_date].append(mood)
    mood_values = [mood for values in moods_by_day.values() for mood in values]
    average = (sum(mood_values) / len(mood_values)) if mood_values else None
    header = f"=== Sterling 日记摘要（近 {days} 天）===\n记录: {len(journals)} 条"
    header += f" · 心情均值: {average:.2f}/5" if average is not None else " · 暂无心情数据"
    if not query:
        return header + "\n默认不展开日记正文；需要相关原文时，请以 journal(query=关键词) 明确查询。"

    matches = [
        bucket for bucket in journals
        if query in (bucket.get("content") or "").lower()
        or query in " ".join((bucket.get("metadata") or {}).get("tags") or []).lower()
    ][:limit]
    if not matches:
        return header + f"\n未找到与「{query}」相关的日记。"
    lines = [header, f"\n与「{query}」相关（{len(matches)} 条）："]
    for bucket in matches:
        meta = bucket.get("metadata") or {}
        entry_date = str(meta.get("journal_date") or meta.get("created") or "")[:10]
        mood = _mood(meta.get("journal_mood"))
        lines.append(f"\n{entry_date} · 心情 {mood if mood is not None else '—'}/5 · {bucket.get('id', '')}\n{(bucket.get('content') or '').strip()}")
    return "\n".join(lines)
