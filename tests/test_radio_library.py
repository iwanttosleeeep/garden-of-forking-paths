import pytest

from radio_library import RadioLibrary, playlist_rows, resource_reference
from radio_service import RadioService

ENCRYPTED_SONG_ID = "a" * 32


class FakeClient:
    def __init__(self):
        self.track_calls = []
        self.add_calls = []
        self.created = [
            {"name": "Human One", "originalId": "101", "encryptedId": "enc101", "trackCount": 2},
            {"name": "Senn One", "originalId": "202", "encryptedId": "enc202", "trackCount": 1},
        ]

    async def playlists(self, scope):
        assert scope == "created"
        return {"data": {"playlists": self.created}}

    async def playlist_tracks(self, playlist_id, *, alternate_id=""):
        self.track_calls.append((playlist_id, alternate_id))
        return {
            "songs": [
                {"name": "A Song", "originalId": "301", "encryptedId": "enc301", "artists": [{"name": "A"}]}
            ]
        }

    async def create_playlist(self, name):
        self.created.append({"name": name, "originalId": "303", "encryptedId": "enc303", "trackCount": 0})
        return {"code": 200}

    async def search(self, query, kind):
        return {
            "query": query,
            "kind": kind,
            "songs": [
                {"name": "Echo", "originalId": 17822773, "id": ENCRYPTED_SONG_ID}
            ],
        }

    async def add_tracks(self, playlist_id, song_ids):
        self.add_calls.append((playlist_id, song_ids))
        return {"code": 200}


def test_radio_library_keeps_paired_ids_visibility_and_notes(tmp_path):
    library = RadioLibrary(str(tmp_path))
    reference = resource_reference({"originalId": 101, "encryptedId": "enc101"})

    library.set_exposed(reference, True)
    library.set_note("playlist", reference, "For the blue hour.")

    assert reference == {"original_id": "101", "encrypted_id": "enc101"}
    assert library.can_senn_read({"encrypted_id": "enc101"}) is True
    assert library.note_for("playlist", {"original_id": "101"}) == "For the blue hour."


def test_official_generic_id_is_kept_as_encrypted_when_original_id_exists():
    assert resource_reference({"id": "encrypted-value", "originalId": 18024790541}) == {
        "original_id": "18024790541",
        "encrypted_id": "encrypted-value",
    }


def test_playlist_rows_prefers_the_playlist_collection():
    payload = {
        "data": {
            "creator": {"id": "9", "name": "Someone"},
            "playlists": [{"id": "1", "name": "One", "trackCount": 3}],
        }
    }
    assert [item["name"] for item in playlist_rows(payload)] == ["One"]


@pytest.mark.asyncio
async def test_radio_service_partitions_human_senn_and_connector_playlists(tmp_path):
    client = FakeClient()
    service = RadioService(str(tmp_path), client)
    service.library.set_exposed({"original_id": "101"}, True)
    service.library.register_senn_playlist({"original_id": "202", "encrypted_id": "enc202"})

    human = await service.playlists("human")
    senn = await service.playlists("senn")
    connector = await service.playlists("connector")

    assert [row["name"] for row in human["playlists"]] == ["Human One"]
    assert [row["name"] for row in senn["playlists"]] == ["Senn One"]
    assert {row["name"] for row in connector["playlists"]} == {"Human One", "Senn One"}

    tracks = await service.playlist(
        {"original_id": "101", "encrypted_id": "enc101"}, enforce_visibility=True
    )
    assert tracks["tracks"][0]["name"] == "A Song"
    assert client.track_calls == [("101", "enc101")]


@pytest.mark.asyncio
async def test_senn_created_playlist_is_registered(tmp_path):
    service = RadioService(str(tmp_path), FakeClient())
    result = await service.create_playlist("Fresh Air", owner="senn")

    assert result["owner"] == "senn"
    assert service.library.is_senn({"original_id": "303"}) is True


@pytest.mark.asyncio
async def test_search_remembers_encrypted_id_and_add_resolves_original_id(tmp_path):
    client = FakeClient()
    service = RadioService(str(tmp_path), client)
    service.library.register_senn_playlist({"original_id": "303"})

    await service.search("Echo", "song")
    result = await service.add_tracks({"original_id": "303"}, 17822773)

    assert result["code"] == 200
    assert client.add_calls == [("303", [ENCRYPTED_SONG_ID])]


@pytest.mark.asyncio
async def test_add_with_unknown_original_id_explains_that_search_is_required(tmp_path):
    service = RadioService(str(tmp_path), FakeClient())
    service.library.register_senn_playlist({"original_id": "303"})

    with pytest.raises(ValueError, match="请先用 radio 的 search"):
        await service.add_tracks({"original_id": "303"}, 17822773)
