"""One bounded MCP surface for Garden Radio."""

from __future__ import annotations

import json

from ncm_client import NCMClient, NCMClientError
from radio_library import RadioLibraryError
from radio_service import RadioService

from . import _runtime as rt


def _json(payload: object) -> str:
    return "=== Garden Radio · NetEase Music ===\n" + json.dumps(
        payload, ensure_ascii=False, indent=2
    )


async def dispatch(
    action: str = "playlists",
    *,
    playlist_id: str = "",
    original_id: str = "",
    encrypted_id: str = "",
    query: str = "",
    kind: str = "all",
    name: str = "",
    song_ids: str = "",
    target_type: str = "playlist",
    note: str = "",
    confirm: bool = False,
) -> str:
    """Perform an explicit music-library action without touching Garden memory."""
    service = RadioService(str(rt.config["buckets_dir"]), NCMClient())
    reference = {
        "original_id": original_id or playlist_id,
        "encrypted_id": encrypted_id,
    }
    wanted = str(action or "playlists").strip().lower()
    try:
        if wanted == "playlists":
            return _json(await service.playlists("connector"))
        if wanted == "playlist":
            return _json(await service.playlist(reference, enforce_visibility=True))
        if wanted == "search":
            return _json(await service.search(query, kind))
        if wanted == "create_playlist":
            if not confirm:
                return "创建歌单会修改网易云账号。确认名称后，请再次调用并传 confirm=true。"
            return _json(await service.create_playlist(name, owner="senn"))
        if wanted == "add_tracks":
            if not confirm:
                return "添加歌曲会修改 Senn 的网易云歌单。确认歌单 ID 与 song_ids 后，请再次调用并传 confirm=true。"
            return _json(await service.add_tracks(reference, song_ids))
        if wanted == "comment":
            return _json(service.set_note(target_type, reference, note))
        return (
            "未知 action。可用：playlists / playlist / search / create_playlist / "
            "add_tracks / comment。"
        )
    except (NCMClientError, RadioLibraryError) as exc:
        return f"Radio 操作失败：{exc}"
