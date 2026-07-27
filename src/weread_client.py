"""Small, bounded client for Tencent's official WeRead Agent API gateway."""

from __future__ import annotations

import os
from typing import Any

import httpx


WEREAD_GATEWAY = "https://i.weread.qq.com/api/agent/gateway"
WEREAD_SKILL_VERSION = "1.0.4"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
ALLOWED_APIS = frozenset({
    "/book/bookmarklist",
    "/book/getprogress",
    "/book/info",
    "/readdata/detail",
    "/review/list/mine",
    "/shelf/sync",
    "/store/search",
    "/user/notebooks",
})


class WeReadError(ValueError):
    """A safe, user-facing WeRead configuration or request error."""


def api_key(config: dict[str, Any]) -> str:
    configured = str((config.get("weread") or {}).get("api_key") or "").strip()
    return os.environ.get("WEREAD_API_KEY", "").strip() or configured


def key_source(config: dict[str, Any]) -> str:
    if os.environ.get("WEREAD_API_KEY", "").strip():
        return "environment"
    if str((config.get("weread") or {}).get("api_key") or "").strip():
        return "config"
    return ""


def masked_key(config: dict[str, Any]) -> str:
    value = api_key(config)
    if not value:
        return ""
    return f"{value[:4]}...{value[-4:]}" if len(value) > 8 else "***"


async def gateway_call(config: dict[str, Any], api_name: str, **params: Any) -> dict[str, Any]:
    """Call one explicitly allowed WeRead API without exposing credentials."""
    if api_name not in ALLOWED_APIS:
        raise WeReadError("该微信读书操作未开放")
    token = api_key(config)
    if not token:
        raise WeReadError("尚未配置微信读书 API Key，请到 Garden → Reading 填写")
    if not token.startswith("wrk-"):
        raise WeReadError("微信读书 API Key 格式不正确（应以 wrk- 开头）")

    payload = {"api_name": api_name, "skill_version": WEREAD_SKILL_VERSION, **params}
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    timeout = httpx.Timeout(connect=8.0, read=20.0, write=10.0, pool=8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
            response = await client.post(WEREAD_GATEWAY, headers=headers, json=payload)
    except httpx.RequestError as exc:
        raise WeReadError("微信读书服务暂时无法连接") from exc

    if len(response.content) > MAX_RESPONSE_BYTES:
        raise WeReadError("微信读书返回的数据过大，请缩小查询范围")
    try:
        data = response.json()
    except ValueError as exc:
        raise WeReadError("微信读书返回了无法解析的数据") from exc
    if response.status_code in {401, 403}:
        raise WeReadError("微信读书 API Key 无效或已失效")
    if response.status_code == 429:
        raise WeReadError("微信读书请求过于频繁，请稍后再试")
    if response.status_code >= 400:
        raise WeReadError(f"微信读书请求失败（HTTP {response.status_code}）")
    if not isinstance(data, dict):
        raise WeReadError("微信读书返回格式不正确")
    upgrade = data.get("upgrade_info")
    if isinstance(upgrade, dict) and upgrade.get("message"):
        raise WeReadError(f"微信读书 Skill 需要升级：{str(upgrade['message'])[:300]}")
    try:
        errcode = int(data.get("errcode") or 0)
    except (TypeError, ValueError):
        errcode = -1
    if errcode != 0:
        message = str(data.get("errmsg") or data.get("message") or "未知错误")[:220]
        raise WeReadError(f"微信读书请求失败：{message}")
    return data


def normalize_shelf(data: dict[str, Any], limit: int = 50) -> dict[str, Any]:
    books = data.get("books") if isinstance(data.get("books"), list) else []
    albums = data.get("albums") if isinstance(data.get("albums"), list) else []
    mp = data.get("mp")
    book_rows = [
        {key: item.get(key) for key in (
            "bookId", "title", "author", "category", "readUpdateTime",
            "finishReading", "isTop", "secret", "deepLink",
        )}
        for item in books[:limit] if isinstance(item, dict)
    ]
    album_rows = []
    for item in albums[:limit]:
        if not isinstance(item, dict):
            continue
        info = item.get("albumInfo") if isinstance(item.get("albumInfo"), dict) else {}
        extra = item.get("albumInfoExtra") if isinstance(item.get("albumInfoExtra"), dict) else {}
        album_rows.append({
            "albumId": info.get("albumId"),
            "title": info.get("name"),
            "author": info.get("authorName"),
            "trackCount": info.get("trackCount"),
            "finishStatus": info.get("finishStatus"),
            "updateTime": info.get("updateTime"),
            "secret": extra.get("secret"),
        })
    return {
        "visible_count": len(books) + len(albums) + (1 if mp else 0),
        "book_count": len(books),
        "album_count": len(albums),
        "has_article_collection": bool(mp),
        "books": book_rows,
        "albums": album_rows,
        "truncated": len(books) > len(book_rows) or len(albums) > len(album_rows),
    }


def normalize_notebooks(data: dict[str, Any], limit: int = 50) -> dict[str, Any]:
    items = data.get("books") if isinstance(data.get("books"), list) else []
    rows = []
    for item in items[:limit]:
        if not isinstance(item, dict):
            continue
        book = item.get("book") if isinstance(item.get("book"), dict) else {}
        review_count = int(item.get("reviewCount") or 0)
        highlight_count = int(item.get("noteCount") or 0)
        bookmark_count = int(item.get("bookmarkCount") or 0)
        rows.append({
            "bookId": item.get("bookId") or book.get("bookId"),
            "title": book.get("title"),
            "author": book.get("author"),
            "review_count": review_count,
            "highlight_count": highlight_count,
            "bookmark_count": bookmark_count,
            "total_note_count": review_count + highlight_count + bookmark_count,
            "reading_progress": item.get("readingProgress"),
            "marked_status": item.get("markedStatus"),
            "sort": item.get("sort"),
        })
    return {
        "total_book_count": data.get("totalBookCount", len(items)),
        "total_note_count": data.get("totalNoteCount"),
        "has_more": bool(data.get("hasMore")),
        "next_cursor": rows[-1].get("sort") if rows and data.get("hasMore") else None,
        "books": rows,
    }


def normalize_notes(
    highlights_data: dict[str, Any], thoughts_data: dict[str, Any], limit: int = 50
) -> dict[str, Any]:
    chapters = highlights_data.get("chapters") if isinstance(highlights_data.get("chapters"), list) else []
    chapter_names = {
        str(item.get("chapterUid")): item.get("title")
        for item in chapters if isinstance(item, dict)
    }
    highlights = []
    for item in (highlights_data.get("updated") or [])[:limit]:
        if not isinstance(item, dict):
            continue
        uid = item.get("chapterUid")
        highlights.append({
            "id": item.get("bookmarkId"),
            "text": item.get("markText"),
            "chapter_uid": uid,
            "chapter": chapter_names.get(str(uid), ""),
            "created_at": item.get("createTime"),
            "range": item.get("range"),
            "color_style": item.get("colorStyle"),
        })
    thoughts = []
    raw_reviews = thoughts_data.get("reviews") if isinstance(thoughts_data.get("reviews"), list) else []
    for wrapper in raw_reviews[:limit]:
        if not isinstance(wrapper, dict):
            continue
        item = wrapper.get("review") if isinstance(wrapper.get("review"), dict) else wrapper
        thoughts.append({
            "id": item.get("reviewId"),
            "content": item.get("content"),
            "highlight": item.get("abstract"),
            "chapter": item.get("chapterName"),
            "chapter_uid": item.get("chapterUid"),
            "created_at": item.get("createTime"),
            "range": item.get("range"),
            "star": item.get("star"),
        })
    book = highlights_data.get("book") if isinstance(highlights_data.get("book"), dict) else {}
    return {
        "book": {key: book.get(key) for key in ("bookId", "title", "author", "deepLink")},
        "highlights": highlights,
        "thoughts": thoughts,
        "thoughts_total": thoughts_data.get("totalCount", len(thoughts)),
        "thoughts_have_more": bool(thoughts_data.get("hasMore")),
    }


def normalize_progress(data: dict[str, Any]) -> dict[str, Any]:
    book = data.get("book") if isinstance(data.get("book"), dict) else {}
    return {
        "book_id": data.get("bookId"),
        "chapter_uid": book.get("chapterUid"),
        "chapter_offset": book.get("chapterOffset"),
        "progress": book.get("progress"),
        "updated_at": book.get("updateTime"),
        "reading_seconds": book.get("recordReadingTime"),
        "finish_time": book.get("finishTime"),
        "started": book.get("isStartReading"),
    }


def normalize_stats(data: dict[str, Any], mode: str) -> dict[str, Any]:
    longest = data.get("readLongest") if isinstance(data.get("readLongest"), list) else []
    top = []
    for item in longest[:10]:
        if not isinstance(item, dict):
            continue
        book = item.get("book") if isinstance(item.get("book"), dict) else {}
        album = item.get("albumInfo") if isinstance(item.get("albumInfo"), dict) else {}
        top.append({
            "title": book.get("title") or album.get("name"),
            "author": book.get("author") or album.get("authorName"),
            "reading_seconds": item.get("readTime"),
            "tags": item.get("tags") or [],
        })
    return {
        "mode": mode,
        "base_time": data.get("baseTime"),
        "total_reading_seconds": data.get("totalReadTime"),
        "read_days": data.get("readDays"),
        "natural_day_average_seconds": data.get("dayAverageReadTime"),
        "compare": data.get("compare"),
        "read_stat": data.get("readStat") or [],
        "top_reads": top,
        "preferred_categories": data.get("preferCategory") or [],
        "preferred_time_text": data.get("preferTimeWord"),
        "preferred_category_text": data.get("preferCategoryWord"),
    }


def normalize_search(data: dict[str, Any], limit: int = 20) -> dict[str, Any]:
    rows = []
    groups = data.get("results") if isinstance(data.get("results"), list) else []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for item in group.get("books") or []:
            if not isinstance(item, dict) or len(rows) >= limit:
                break
            book = item.get("bookInfo") if isinstance(item.get("bookInfo"), dict) else {}
            rows.append({
                "group": group.get("title"),
                "bookId": book.get("bookId"),
                "title": book.get("title"),
                "author": book.get("author"),
                "category": book.get("category"),
                "rating": item.get("newRating"),
                "reading_count": item.get("readingCount"),
                "deepLink": book.get("deepLink"),
            })
    return {"results": rows, "has_more": bool(data.get("hasMore"))}
