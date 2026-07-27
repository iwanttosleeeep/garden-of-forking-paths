"""One bounded MCP surface for Garden Radio."""

from __future__ import annotations

import json

from ncm_client import NCMClient, NCMClientError


def _json(payload: object) -> str:
    return "=== Garden Radio · NetEase Music ===\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


async def dispatch(
    action: str = "playlists",
    *,
    scope: str = "created",
    playlist_id: str = "",
    query: str = "",
    kind: str = "all",
    mode: str = "daily",
    name: str = "",
    song_ids: str = "",
    confirm: bool = False,
) -> str:
    """Perform an explicit music-library action without touching Garden memory."""
    client = NCMClient()
    wanted = str(action or "playlists").strip().lower()
    try:
        if wanted == "playlists":
            return _json(await client.playlists(scope))
        if wanted == "playlist":
            return _json(await client.playlist_tracks(playlist_id))
        if wanted == "search":
            return _json(await client.search(query, kind))
        if wanted == "recommend":
            return _json(await client.recommend(query, mode))
        if wanted == "create_playlist":
            if not confirm:
                return "创建歌单会修改网易云账号。确认名称后，请再次调用并传 confirm=true。"
            return _json(await client.create_playlist(name))
        if wanted == "add_tracks":
            if not confirm:
                return "添加歌曲会修改网易云歌单。确认 playlist_id 与 song_ids 后，请再次调用并传 confirm=true。"
            return _json(await client.add_tracks(playlist_id, song_ids))
        return (
            "未知 action。可用：playlists / playlist / search / recommend / "
            "create_playlist / add_tracks。"
        )
    except NCMClientError as exc:
        return f"Radio 操作失败：{exc}"
