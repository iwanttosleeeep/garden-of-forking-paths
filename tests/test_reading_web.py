import json

import pytest

from web import _shared as sh
from web import reading


class FakeMcp:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            self.routes[(path, tuple(methods))] = fn
            return fn
        return decorator


class FakeUpload:
    filename = "shared.md"

    async def read(self):
        return "# First\n\nA quiet opening.\n\n# Second\n\nA different path.".encode()


class FakeRequest:
    headers = {}
    query_params = {}
    cookies = {}

    def __init__(self, *, path_params=None, query_params=None, body=None, form=None):
        self.path_params = path_params or {}
        self.query_params = query_params or {}
        self._body = body or {}
        self._form = form or {}

    async def json(self):
        return self._body

    async def form(self):
        return self._form


class FakeDehydrator:
    api_available = True

    async def _chat(self, *_args, **_kwargs):
        return json.dumps({
            "summary": "A quiet beginning.",
            "themes": ["arrival"],
            "questions": ["What changed?"],
        })


def _json(response):
    return json.loads(bytes(response.body))


@pytest.mark.asyncio
async def test_authenticated_reading_route_flow(tmp_path, monkeypatch):
    mcp = FakeMcp()
    reading.register(mcp)
    monkeypatch.setattr(sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(sh, "config", {"buckets_dir": str(tmp_path)})
    monkeypatch.setattr(sh, "dehydrator", FakeDehydrator())

    upload = mcp.routes[("/api/reading", ("POST",))]
    created = _json(await upload(FakeRequest(form={"file": FakeUpload(), "title": "Shared Book", "author": "A"})))
    book_id = created["book"]["id"]
    assert created["book"]["chunk_count"] == 2

    progress = mcp.routes[("/api/reading/{book_id}/progress", ("PATCH",))]
    saved = _json(await progress(FakeRequest(path_params={"book_id": book_id}, body={"chunk": 1, "offset": 0.5})))
    assert saved["book"]["progress"]["human"]["chunk"] == 1

    add_note = mcp.routes[("/api/reading/{book_id}/annotations", ("POST",))]
    note = _json(await add_note(FakeRequest(
        path_params={"book_id": book_id},
        body={"chunk": 1, "quote": "different path", "note": "Notice this", "kind": "highlight"},
    )))
    assert note["annotation"]["author"] == "human"

    analyze = mcp.routes[("/api/reading/{book_id}/chunks/{chunk_id}/analyze", ("POST",))]
    analysis = _json(await analyze(FakeRequest(path_params={"book_id": book_id, "chunk_id": "1"})))
    assert analysis["analysis"]["themes"] == ["arrival"]

    get_chunk = mcp.routes[("/api/reading/{book_id}/chunks/{chunk_id}", ("GET",))]
    chunk = _json(await get_chunk(FakeRequest(path_params={"book_id": book_id, "chunk_id": "1"})))
    assert chunk["chunk"]["annotations"][0]["note"] == "Notice this"
    assert chunk["chunk"]["analysis"]["summary"] == "A quiet beginning."

    export = mcp.routes[("/api/reading/{book_id}/export", ("GET",))]
    exported = await export(FakeRequest(path_params={"book_id": book_id}))
    assert b"Notice this" in exported.body

    delete = mcp.routes[("/api/reading/{book_id}", ("DELETE",))]
    assert _json(await delete(FakeRequest(path_params={"book_id": book_id})))["ok"] is True


@pytest.mark.asyncio
async def test_weread_key_is_verified_and_data_stays_inside_reading(tmp_path, monkeypatch):
    mcp = FakeMcp()
    reading.register(mcp)
    monkeypatch.setattr(sh, "_require_auth", lambda _request: None)
    monkeypatch.setattr(sh, "config", {"buckets_dir": str(tmp_path), "weread": {"api_key": ""}})

    async def fake_gateway(_config, api_name, **_params):
        if api_name == "/shelf/sync":
            return {"books": [{"bookId": "b1", "title": "Shared Shelf", "author": "A"}]}
        if api_name == "/user/notebooks":
            return {
                "books": [{
                    "bookId": "b1",
                    "book": {"title": "Shared Shelf", "author": "A"},
                    "reviewCount": 1,
                    "noteCount": 2,
                    "bookmarkCount": 3,
                }]
            }
        raise AssertionError(api_name)

    def fake_persist(value):
        sh.config.setdefault("weread", {})["api_key"] = value

    monkeypatch.setattr(reading, "gateway_call", fake_gateway)
    monkeypatch.setattr(reading, "_persist_weread_key", fake_persist)

    save = mcp.routes[("/api/weread/config", ("POST",))]
    saved = _json(await save(FakeRequest(body={"api_key": "wrk-secret-key"})))
    assert saved["configured"] is True
    assert saved["visible_count"] == 1
    assert "wrk-secret-key" not in json.dumps(saved)

    get_config = mcp.routes[("/api/weread/config", ("GET",))]
    config = _json(await get_config(FakeRequest()))
    assert config["configured"] is True
    assert config["masked"] != "wrk-secret-key"

    get_data = mcp.routes[("/api/weread", ("GET",))]
    notebooks = _json(await get_data(FakeRequest(query_params={"view": "notebooks"})))
    assert notebooks["data"]["books"][0]["total_note_count"] == 6
