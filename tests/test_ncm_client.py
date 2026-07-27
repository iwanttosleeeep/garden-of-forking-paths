import pytest

from ncm_client import NCMClient, NCMClientError


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def __call__(self, args, timeout):
        self.calls.append((args, timeout))
        if args == ["login", "--check"]:
            return {"success": True, "message": "已登录"}
        return {"success": True, "items": []}


@pytest.mark.asyncio
async def test_radio_client_exposes_only_bounded_music_actions():
    runner = FakeRunner()
    client = NCMClient(runner)

    await client.playlists("created")
    await client.playlist_tracks("playlist-encrypted-id")
    await client.search("quiet evening", "playlist")
    await client.recommend(mode="daily")
    await client.create_playlist("Garden at dusk")
    await client.add_tracks("playlist-id", "song-a,song-b")

    commands = [call[0] for call in runner.calls]
    assert commands[0][:2] == ["playlist", "created"]
    assert commands[1][:3] == ["playlist", "tracks", "--playlistId"]
    assert commands[2][:4] == ["search", "playlist", "--keyword", "quiet evening"]
    assert commands[3][:2] == ["recommend", "daily"]
    assert commands[4][:4] == ["playlist", "create", "--playlistName", "Garden at dusk"]
    assert commands[5][:3] == ["playlist", "add", "--playlistId"]
    assert all("--userInput" in command for command in commands)


@pytest.mark.asyncio
async def test_radio_client_configuration_never_returns_private_key():
    runner = FakeRunner()
    client = NCMClient(runner)

    result = await client.configure("123456789", "very-secret-private-key")

    assert result == {"configured": True, "app_id_masked": "123…789"}
    assert "very-secret-private-key" not in repr(result)
    assert runner.calls[1][0] == ["config", "set", "privateKey", "very-secret-private-key"]


@pytest.mark.asyncio
async def test_radio_client_rejects_unbounded_ids_and_unknown_modes():
    client = NCMClient(FakeRunner())

    with pytest.raises(NCMClientError):
        await client.add_tracks("playlist", "song; arbitrary-command")
    with pytest.raises(NCMClientError):
        await client.playlists("everything")
    with pytest.raises(NCMClientError):
        await client.recommend(mode="surprise-shell")
