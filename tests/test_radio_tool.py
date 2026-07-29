import json

import pytest

from tools import radio


class FakeService:
    def __init__(self, _buckets_dir, _client):
        pass

    async def playlists(self, view):
        return {"view": view, "playlists": []}

    async def create_playlist(self, name, *, owner):
        return {"name": name, "created": True, "owner": owner}

    async def add_tracks(self, reference, song_ids):
        return {"reference": reference, "song_ids": song_ids}

    def set_note(self, target_type, reference, note):
        return {"target_type": target_type, "reference": reference, "note": note}


@pytest.mark.asyncio
async def test_single_radio_tool_reads_and_requires_confirmation_for_writes(monkeypatch, tmp_path):
    monkeypatch.setattr(radio, "RadioService", FakeService)
    monkeypatch.setattr(radio.rt, "config", {"buckets_dir": str(tmp_path)})

    listed = await radio.dispatch("playlists")
    payload = json.loads(listed.split("\n", 1)[1])
    assert payload["view"] == "connector"

    prompt = await radio.dispatch("create_playlist", name="Night", confirm=False)
    assert "confirm=true" in prompt

    created = await radio.dispatch("create_playlist", name="Night", confirm=True)
    assert json.loads(created.split("\n", 1)[1])["created"] is True
    assert json.loads(created.split("\n", 1)[1])["owner"] == "senn"

    add_prompt = await radio.dispatch(
        "add_tracks", original_id="playlist-1", song_ids="song-a,song-b", confirm=False
    )
    assert "confirm=true" in add_prompt

    added = await radio.dispatch(
        "add_tracks", original_id="playlist-1", song_ids="song-a,song-b", confirm=True
    )
    added_payload = json.loads(added.split("\n", 1)[1])
    assert added_payload["reference"]["original_id"] == "playlist-1"
    assert added_payload["song_ids"] == "song-a,song-b"

    commented = await radio.dispatch(
        "comment", original_id="playlist-1", target_type="playlist", note="Because dusk needs strings."
    )
    assert json.loads(commented.split("\n", 1)[1])["note"].startswith("Because")
