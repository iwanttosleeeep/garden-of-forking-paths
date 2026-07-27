"""Single MCP surface for shared Garden reading."""

from __future__ import annotations

import asyncio
import json

from reading_library import ReadingLibrary, ReadingLibraryError
from weread_client import (
    WeReadError,
    gateway_call,
    normalize_notebooks,
    normalize_notes,
    normalize_progress,
    normalize_search,
    normalize_shelf,
    normalize_stats,
)

from . import _runtime as rt


def _library() -> ReadingLibrary:
    return ReadingLibrary(str(rt.config["buckets_dir"]))


def _progress_line(book: dict) -> str:
    progress = book.get("progress") or {}
    human = progress.get("human") or {}
    ai = progress.get("ai") or {}
    count = max(1, int(book.get("chunk_count") or 1))
    return f"人类 {int(human.get('chunk') or 0) + 1}/{count} · 机 {int(ai.get('chunk') or 0) + 1}/{count}"


def _limit(value: int) -> int:
    try:
        return max(1, min(50, int(value)))
    except (TypeError, ValueError, OverflowError):
        return 20


def _weread_json(payload: dict) -> str:
    return "=== WeRead / 微信读书 ===\n" + json.dumps(payload, ensure_ascii=False, indent=2)


async def dispatch(
    action: str,
    *,
    book_id: str = "",
    chunk_id: int = -1,
    offset: float = 0.0,
    quote: str = "",
    note: str = "",
    kind: str = "note",
    query: str = "",
    limit: int = 20,
    period: str = "monthly",
    cursor: int = 0,
) -> str:
    """Execute one bounded reading action without touching Garden Memos."""
    library = _library()
    action = str(action or "library").strip().lower()
    try:
        if action == "weread_shelf":
            data = await gateway_call(rt.config, "/shelf/sync")
            return _weread_json(normalize_shelf(data, _limit(limit)))
        if action == "weread_notebooks":
            params = {"count": _limit(limit)}
            if int(cursor or 0) > 0:
                params["lastSort"] = int(cursor)
            data = await gateway_call(rt.config, "/user/notebooks", **params)
            return _weread_json(normalize_notebooks(data, _limit(limit)))
        if action == "weread_notes":
            if not book_id:
                return "需要 book_id；先用 action=weread_notebooks 选择一本书。"
            count = _limit(limit)
            highlights, thoughts = await asyncio.gather(
                gateway_call(rt.config, "/book/bookmarklist", bookId=book_id),
                gateway_call(
                    rt.config,
                    "/review/list/mine",
                    bookid=book_id,
                    synckey=max(0, int(cursor or 0)),
                    count=count,
                ),
            )
            return _weread_json(normalize_notes(highlights, thoughts, count))
        if action == "weread_progress":
            if not book_id:
                return "需要 book_id；先用 action=weread_shelf 选择一本电子书。"
            data = await gateway_call(rt.config, "/book/getprogress", bookId=book_id)
            return _weread_json(normalize_progress(data))
        if action == "weread_stats":
            mode = str(period or "monthly").strip().lower()
            if mode not in {"weekly", "monthly", "annually", "overall"}:
                return "period 必须是 weekly / monthly / annually / overall。"
            data = await gateway_call(rt.config, "/readdata/detail", mode=mode)
            return _weread_json(normalize_stats(data, mode))
        if action == "weread_search":
            keyword = str(query or "").strip()
            if not keyword:
                return "需要 query 作为微信读书搜索关键词。"
            data = await gateway_call(
                rt.config, "/store/search", keyword=keyword, scope=10, count=_limit(limit)
            )
            return _weread_json(normalize_search(data, _limit(limit)))
        if action == "library":
            books = library.list_books()
            if not books:
                return "Reading 书架还是空的。请先在 Garden → Reading 上传 EPUB、TXT 或 Markdown。"
            return "\n".join(
                f"- {book['title']} · {book.get('author') or '作者未知'} · ID {book['id']} · {_progress_line(book)}"
                for book in books
            )
        if not book_id:
            return "需要 book_id；先用 action=library 查看书架。"
        if action == "open":
            book = library.get_book(book_id)
            if chunk_id < 0:
                chunk_id = int((book.get("progress") or {}).get("human", {}).get("chunk") or 0)
            chunk = library.get_chunk(book_id, chunk_id)
            library.update_progress(book_id, "ai", chunk_id, 1.0)
            payload = {
                "book": chunk["book"]["title"],
                "author": chunk["book"].get("author") or "",
                "chunk_id": chunk["id"],
                "chunk_count": chunk["book"]["chunk_count"],
                "section": chunk["title"],
                "text": chunk["text"],
                "analysis": chunk.get("analysis"),
                "shared_annotations": chunk.get("annotations") or [],
                "previous": chunk.get("previous"),
                "next": chunk.get("next"),
            }
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if action == "progress":
            book = (
                library.update_progress(book_id, "ai", chunk_id, offset)
                if chunk_id >= 0
                else library.get_book(book_id)
            )
            return f"{book['title']} · {_progress_line(book)}"
        if action == "review":
            return json.dumps(library.review_book(book_id), ensure_ascii=False, indent=2)
        if action == "note":
            item = library.add_annotation(
                book_id, chunk_id, author="ai", quote=quote, note=note, kind=kind
            )
            return f"已把机的 {item['kind']} 留在第 {item['chunk'] + 1} 个阅读片段（{item['id']}）。"
        if action == "finish":
            book = library.get_book(book_id)
            last = max(0, int(book.get("chunk_count") or 1) - 1)
            book = library.update_progress(book_id, "ai", last, 1.0)
            return f"机已读完《{book['title']}》。{_progress_line(book)}"
        return (
            "未知 action。可用：library / open / progress / note / review / finish / "
            "weread_shelf / weread_notebooks / weread_notes / weread_progress / "
            "weread_stats / weread_search。"
        )
    except (ReadingLibraryError, WeReadError, TypeError, ValueError) as exc:
        return f"Reading 操作失败：{exc}"
