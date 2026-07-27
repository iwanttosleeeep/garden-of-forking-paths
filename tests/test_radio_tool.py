import json

import pytest

from tools import radio


class FakeClient:
    async def playlists(self, scope):
        return {"scope": scope, "playlists": []}

    async def create_playlist(self, name):
        return {"name": name, "created": True}

    async def add_tracks(self, playlist_id, song_ids):
        return {"playlist_id": playlist_id, "song_ids": song_ids}


@pytest.mark.asyncio
async def test_single_radio_tool_reads_and_requires_confirmation_for_writes(monkeypatch):
    monkeypatch.setattr(radio, "NCMClient", FakeClient)

    listed = await radio.dispatch("playlists", scope="collected")
    payload = json.loads(listed.split("\n", 1)[1])
    assert payload["scope"] == "collected"

    prompt = await radio.dispatch("create_playlist", name="Night", confirm=False)
    assert "confirm=true" in prompt

    created = await radio.dispatch("create_playlist", name="Night", confirm=True)
    assert json.loads(created.split("\n", 1)[1])["created"] is True
