"""Small private index for Garden Radio curation metadata.

The NetEase account remains the source of truth for playlists and tracks.  This
module stores only Garden-specific visibility choices, Senn ownership markers,
and short notes.  It deliberately never copies music-library payloads.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from typing import Any


MAX_NOTE_LENGTH = 1200
_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,128}")


class RadioLibraryError(ValueError):
    """A safe, user-facing Radio metadata error."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_id(value: Any) -> str:
    text = str(value or "").strip()
    return text if _ID_RE.fullmatch(text) else ""


def resource_reference(value: Any, *, fallback_id: Any = "") -> dict[str, str]:
    """Keep both official resource IDs instead of collapsing them into one."""
    item = value if isinstance(value, dict) else {}
    explicit_original = _clean_id(item.get("original_id") or item.get("originalId"))
    generic_id = _clean_id(item.get("id"))
    encrypted = _clean_id(
        item.get("encrypted_id")
        or item.get("encryptedId")
        or item.get("encryptId")
        or item.get("encrypt_id")
        or (generic_id if explicit_original else "")
    )
    original = _clean_id(
        explicit_original
        or item.get("playlistId")
        or item.get("songId")
        or generic_id
        or fallback_id
    )
    if not original and not encrypted:
        raise RadioLibraryError("音乐资源 ID 无效")
    return {"original_id": original, "encrypted_id": encrypted}


def reference_ids(reference: Any) -> set[str]:
    try:
        ref = resource_reference(reference)
    except RadioLibraryError:
        return set()
    return {value for value in ref.values() if value}


def reference_key(reference: Any) -> str:
    ref = resource_reference(reference)
    return ref["original_id"] or ref["encrypted_id"]


def _item_title(item: dict) -> str:
    return str(
        item.get("name")
        or item.get("title")
        or item.get("playlistName")
        or item.get("songName")
        or ""
    ).strip()


def _playlist_score(item: dict) -> int:
    score = 0
    if any(key in item for key in ("playlistId", "playlistName", "trackCount", "coverImgUrl")):
        score += 6
    if any(key in item for key in ("encryptedId", "encrypted_id", "originalId", "original_id")):
        score += 3
    if _item_title(item) and any(key in item for key in ("id", "playlistId", "originalId")):
        score += 2
    return score


def playlist_rows(payload: Any) -> list[dict]:
    """Find the most playlist-like array in a version-variable CLI response."""
    candidates: list[list[dict]] = []

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 7:
            return
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            if rows:
                candidates.append(rows)
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item, depth + 1)

    visit(payload)
    if not candidates:
        return []
    ranked = sorted(
        candidates,
        key=lambda rows: (sum(_playlist_score(item) for item in rows), len(rows)),
        reverse=True,
    )
    return [dict(item) for item in ranked[0] if _playlist_score(item) > 0]


def track_rows(payload: Any) -> list[dict]:
    """Find the most track-like array while avoiding nested artist arrays."""
    candidates: list[list[dict]] = []

    def score(item: dict) -> int:
        value = 0
        if any(key in item for key in ("songId", "songName", "duration", "album", "artists")):
            value += 5
        if any(key in item for key in ("encryptedId", "encrypted_id", "originalId", "original_id")):
            value += 3
        if _item_title(item) and "id" in item:
            value += 1
        return value

    def visit(value: Any, depth: int = 0) -> None:
        if depth > 7:
            return
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            if rows:
                candidates.append(rows)
            for item in value:
                visit(item, depth + 1)
        elif isinstance(value, dict):
            for item in value.values():
                visit(item, depth + 1)

    visit(payload)
    if not candidates:
        return []
    ranked = sorted(candidates, key=lambda rows: (sum(score(item) for item in rows), len(rows)), reverse=True)
    return [dict(item) for item in ranked[0] if score(item) > 0]


class RadioLibrary:
    """Atomic Garden-local state for playlist curation and Senn notes."""

    def __init__(self, buckets_dir: str):
        self.path = os.path.join(str(buckets_dir), ".radio_library.json")
        self._lock = threading.RLock()

    @staticmethod
    def _empty() -> dict:
        return {"version": 1, "exposed": [], "senn": [], "notes": {}}

    def _read(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as handle:
                value = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return self._empty()
        if not isinstance(value, dict):
            return self._empty()
        if not isinstance(value.get("exposed"), list):
            value["exposed"] = []
        if not isinstance(value.get("senn"), list):
            value["senn"] = []
        if not isinstance(value.get("notes"), dict):
            value["notes"] = {}
        return value

    def _write(self, value: dict) -> None:
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="radio-", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _matches(reference: Any, saved: Any) -> bool:
        return bool(reference_ids(reference) & reference_ids(saved))

    def is_exposed(self, reference: Any) -> bool:
        return any(self._matches(reference, item) for item in self._read().get("exposed", []))

    def is_senn(self, reference: Any) -> bool:
        return any(self._matches(reference, item) for item in self._read().get("senn", []))

    def can_senn_read(self, reference: Any) -> bool:
        return self.is_exposed(reference) or self.is_senn(reference)

    def set_exposed(self, reference: Any, exposed: bool) -> dict:
        ref = resource_reference(reference)
        with self._lock:
            state = self._read()
            rows = [item for item in state["exposed"] if not self._matches(ref, item)]
            if exposed:
                rows.append(ref)
            state["exposed"] = rows
            self._write(state)
        return ref

    def register_senn_playlist(self, reference: Any, *, name: str = "") -> dict:
        ref = resource_reference(reference)
        saved = {**ref, "name": str(name or "").strip()[:160], "created_at": _now()}
        with self._lock:
            state = self._read()
            state["senn"] = [item for item in state["senn"] if not self._matches(ref, item)] + [saved]
            self._write(state)
        return saved

    def note_for(self, target_type: str, reference: Any) -> str:
        kind = "playlist" if target_type == "playlist" else "track"
        ids = reference_ids(reference)
        notes = self._read().get("notes", {})
        for resource_id in ids:
            item = notes.get(f"{kind}:{resource_id}")
            if isinstance(item, dict) and item.get("text"):
                return str(item["text"])
        return ""

    def set_note(self, target_type: str, reference: Any, text: Any) -> dict:
        kind = str(target_type or "").strip().lower()
        if kind not in {"playlist", "track"}:
            raise RadioLibraryError("target_type 必须是 playlist 或 track")
        ref = resource_reference(reference)
        note = str(text or "").strip()
        if not note:
            raise RadioLibraryError("请填写 Senn 留言")
        if len(note) > MAX_NOTE_LENGTH:
            raise RadioLibraryError(f"Senn 留言不能超过 {MAX_NOTE_LENGTH} 字")
        key = f"{kind}:{reference_key(ref)}"
        item = {"text": note, "updated_at": _now(), "reference": ref}
        with self._lock:
            state = self._read()
            state["notes"][key] = item
            self._write(state)
        return item
