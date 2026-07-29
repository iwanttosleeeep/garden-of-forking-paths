import pytest

from ncm_client import NCMClient, NCMClientError, _api_error, _json_from_text


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
    await client.playlist_tracks("playlist-original-id", alternate_id="playlist-encrypted-id")
    await client.search("quiet evening", "playlist")
    await client.create_playlist("Garden at dusk")
    await client.add_tracks("playlist-id", "song-a,song-b")

    commands = [call[0] for call in runner.calls]
    assert commands[0][:2] == ["playlist", "created"]
    assert commands[1][:3] == ["playlist", "tracks", "--playlistId"]
    assert commands[2][:4] == ["search", "playlist", "--keyword", "quiet evening"]
    assert commands[3][:4] == ["playlist", "create", "--playlistName", "Garden at dusk"]
    assert commands[4][:3] == ["playlist", "add", "--playlistId"]
    assert commands[4][3:7] == [
        "playlist-id",
        "--songIdList",
        '["song-a","song-b"]',
        "--userInput",
    ]
    assert "--songIds" not in commands[4]
    assert all("--userInput" in command for command in commands)


@pytest.mark.asyncio
async def test_add_tracks_normalizes_a_song_id_collection_for_the_cli_contract():
    runner = FakeRunner()

    await NCMClient(runner).add_tracks("playlist-id", ["song-a", " song-b ", ""])

    command = runner.calls[0][0]
    assert command[:6] == [
        "playlist",
        "add",
        "--playlistId",
        "playlist-id",
        "--songIdList",
        '["song-a","song-b"]',
    ]


@pytest.mark.asyncio
async def test_add_tracks_accepts_a_json_array_string():
    runner = FakeRunner()

    await NCMClient(runner).add_tracks("playlist-id", '["song-a", "song-b"]')

    assert runner.calls[0][0][5] == '["song-a","song-b"]'


@pytest.mark.asyncio
async def test_add_tracks_retries_comma_format_only_after_a_shape_error():
    calls = []

    async def runner(args, _timeout):
        calls.append(args)
        if args[5].startswith("["):
            raise NCMClientError("参数错误：songIdList")
        return {"success": True}

    result = await NCMClient(runner).add_tracks("playlist-id", ["song-a", "song-b"])

    assert result["success"] is True
    assert [call[5] for call in calls] == ['["song-a","song-b"]', "song-a,song-b"]


@pytest.mark.asyncio
async def test_add_tracks_does_not_retry_non_validation_failures():
    calls = []

    async def runner(args, _timeout):
        calls.append(args)
        raise NCMClientError("请先登录")

    with pytest.raises(NCMClientError, match="请先登录"):
        await NCMClient(runner).add_tracks("playlist-id", ["song-a"])
    assert len(calls) == 1


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


@pytest.mark.asyncio
async def test_playlist_tracks_retries_the_paired_official_id():
    calls = []

    async def runner(args, _timeout):
        calls.append(args)
        if "wrong-encrypted-id" not in args:
            raise NCMClientError("参数错误：18024790541")
        return {"songs": [{"name": "Moon"}]}

    result = await NCMClient(runner).playlist_tracks(
        "18024790541", alternate_id="wrong-encrypted-id"
    )

    assert result["songs"][0]["name"] == "Moon"
    assert [call[3] for call in calls] == ["18024790541", "wrong-encrypted-id"]


def test_cli_api_error_is_detected_even_with_zero_exit_status():
    assert _api_error({"code": 400, "message": "参数错误：18024790541"}) == "参数错误：18024790541"


@pytest.mark.asyncio
async def test_playlist_tracks_does_not_retry_authentication_errors():
    calls = []

    async def runner(args, _timeout):
        calls.append(args)
        raise NCMClientError("请先登录")

    with pytest.raises(NCMClientError, match="请先登录"):
        await NCMClient(runner).playlist_tracks("one", alternate_id="two")
    assert len(calls) == 1


def test_cli_parser_recovers_pretty_json_after_log_line():
    payload = _json_from_text(
        '[info] checking session\n{\n  "success": true,\n  "data": {"isLogin": true}\n}\n'
    )
    assert payload["data"]["isLogin"] is True


@pytest.mark.asyncio
async def test_status_recognises_nested_official_login_shape():
    async def runner(args, _timeout):
        if args == ["login", "--check"]:
            return {"code": 200, "data": {"isLogin": True, "account": {"id": "encrypted"}}}
        return {"text": "0.1.6"}

    status = await NCMClient(runner).status(configured=True)

    assert status["configured"] is True
    assert status["logged_in"] is True
