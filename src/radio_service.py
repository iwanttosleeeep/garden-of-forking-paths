"""Shared orchestration for the Radio web page and its single MCP tool."""

from __future__ import annotations

from typing import Any

from ncm_client import NCMClient, _song_id_tokens
from radio_library import (
    RadioLibrary,
    RadioLibraryError,
    playlist_rows,
    resource_reference,
    track_rows,
)


class RadioService:
    def __init__(self, buckets_dir: str, client: NCMClient | None = None):
        self.client = client or NCMClient()
        self.library = RadioLibrary(buckets_dir)

    def _decorate(self, item: dict, target_type: str) -> dict:
        try:
            reference = resource_reference(item)
        except RadioLibraryError:
            return dict(item)
        return {
            **item,
            "_garden": {
                "reference": reference,
                "owner": "senn" if self.library.is_senn(reference) else "human",
                "exposed": self.library.is_exposed(reference),
                "note": self.library.note_for(target_type, reference),
            },
        }

    async def playlists(self, view: str = "human") -> dict:
        wanted = str(view or "human").strip().lower()
        if wanted not in {"human", "senn", "connector"}:
            raise RadioLibraryError("view 必须是 human / senn / connector")
        raw = await self.client.playlists("created")
        rows = [self._decorate(item, "playlist") for item in playlist_rows(raw)]
        if wanted == "human":
            rows = [item for item in rows if item.get("_garden", {}).get("owner") != "senn"]
        elif wanted == "senn":
            rows = [item for item in rows if item.get("_garden", {}).get("owner") == "senn"]
        else:
            rows = [
                item
                for item in rows
                if item.get("_garden", {}).get("owner") == "senn"
                or item.get("_garden", {}).get("exposed") is True
            ]
        return {"view": wanted, "playlists": rows}

    async def playlist(self, reference: Any, *, enforce_visibility: bool = False) -> dict:
        ref = resource_reference(reference)
        if enforce_visibility and not self.library.can_senn_read(ref):
            raise RadioLibraryError("这张歌单尚未授权给 Senn；请先在 Garden → Radio 勾选展示")
        raw = await self.client.playlist_tracks(
            ref.get("original_id", ""), alternate_id=ref.get("encrypted_id", "")
        )
        rows = track_rows(raw)
        self.library.remember_song_references(rows)
        return {
            "playlist": {
                "reference": ref,
                "note": self.library.note_for("playlist", ref),
            },
            "tracks": [self._decorate(item, "track") for item in rows],
        }

    def set_exposed(self, reference: Any, exposed: bool) -> dict:
        ref = self.library.set_exposed(reference, exposed)
        return {"reference": ref, "exposed": bool(exposed)}

    async def search(self, query: Any, kind: str = "all") -> Any:
        result = await self.client.search(query, kind)
        self.library.remember_song_references(track_rows(result))
        return result

    async def create_playlist(self, name: Any, *, owner: str = "human") -> dict:
        before_ids: set[str] = set()
        if owner == "senn":
            before = await self.client.playlists("created")
            for item in playlist_rows(before):
                try:
                    before_ids.update(resource_reference(item).values())
                except RadioLibraryError:
                    continue
            before_ids.discard("")
        result = await self.client.create_playlist(name)
        if owner != "senn":
            return {"created": result, "owner": "human"}

        title = str(name or "").strip()
        reference = None
        raw = await self.client.playlists("created")
        for item in playlist_rows(raw):
            item_title = str(item.get("name") or item.get("title") or item.get("playlistName") or "").strip()
            if item_title == title:
                try:
                    candidate = resource_reference(item)
                except RadioLibraryError:
                    continue
                if not ({value for value in candidate.values() if value} & before_ids):
                    reference = candidate
                    break
        if reference is None:
            try:
                reference = resource_reference(result)
            except RadioLibraryError:
                pass
        if reference:
            self.library.register_senn_playlist(reference, name=title)
        return {"created": result, "owner": "senn", "reference": reference}

    async def add_tracks(self, reference: Any, song_ids: Any) -> Any:
        ref = resource_reference(reference)
        if not self.library.is_senn(ref):
            raise RadioLibraryError("Senn 只能修改自己创建的歌单")
        encrypted_ids = self.library.resolve_encrypted_song_ids(_song_id_tokens(song_ids))
        return await self.client.add_tracks(
            ref["original_id"] or ref["encrypted_id"], encrypted_ids
        )

    def set_note(self, target_type: str, reference: Any, note: Any) -> dict:
        if target_type == "playlist" and not self.library.can_senn_read(reference):
            raise RadioLibraryError("这张歌单尚未授权给 Senn")
        return self.library.set_note(target_type, reference, note)
