import json

import pytest

from web import _shared as sh
from web import radio


class FakeMcp:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            self.routes[(path, tuple(methods))] = fn
            return fn
        return decorator


class FakeRequest:
    headers = {}
    query_params = {}
    cookies = {}
    path_params = {}

    def __init__(self, body=None):
        self._body = body or {}

    async def json(self):
        return self._body


class FakeClient:
    async def status(self, *, configured=False):
        return {"configured": configured, "logged_in": True, "login_message": "已登录"}

    async def configure(self, app_id, private_key):
        assert app_id == "app-123456"
        assert private_key == "private-secret"
        return {"configured": True, "app_id_masked": "app…456"}

    async def start_login(self):
        return {"url": "https://example.test/qr"}

    async def playlists(self, scope):
        return {"scope": scope, "playlists": [{"name": "Night Radio"}]}

    async def playlist_tracks(self, playlist_id):
        return {"playlist_id": playlist_id}

    async def search(self, query, kind):
        return {"query": query, "kind": kind}

    async def recommend(self, query, mode):
        return {"query": query, "mode": mode}

    async def create_playlist(self, name):
        return {"created": name}

    async def add_tracks(self, playlist_id, song_ids):
        return {"playlist_id": playlist_id, "song_ids": song_ids}


def _json(response):
    return json.loads(bytes(response.body))


@pytest.mark.asyncio
async def test_radio_routes_configure_login_read_and_create(monkeypatch):
    mcp = FakeMcp()
    radio.register(mcp)
    monkeypatch.setattr(sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(sh, "config", {"radio": {"configured": False}})
    monkeypatch.setattr(radio, "_client", lambda: FakeClient())

    def fake_marker(configured, app_id_masked=""):
        sh.config["radio"] = {"configured": configured, "app_id_masked": app_id_masked}

    monkeypatch.setattr(radio, "_persist_marker", fake_marker)

    save = mcp.routes[("/api/radio/config", ("POST",))]
    saved = _json(await save(FakeRequest({"app_id": "app-123456", "private_key": "private-secret"})))
    assert saved["configured"] is True
    assert "private-secret" not in json.dumps(saved)

    login = mcp.routes[("/api/radio/login", ("POST",))]
    assert _json(await login(FakeRequest()))["data"]["url"].startswith("https://")

    action = mcp.routes[("/api/radio", ("POST",))]
    listed = _json(await action(FakeRequest({"action": "playlists", "scope": "created"})))
    assert listed["data"]["playlists"][0]["name"] == "Night Radio"
    created = _json(await action(FakeRequest({"action": "create_playlist", "name": "New path"})))
    assert created["data"]["created"] == "New path"
