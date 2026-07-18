import json

import pytest

from web import _shared as sh
from web import buckets as buckets_web


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

    def __init__(self, body):
        self.body = body

    async def json(self):
        return self.body


class FakeBucketManager:
    def __init__(self):
        self.create_calls = []
        self.updated = []

    async def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return "self-bucket-1"

    async def update(self, bucket_id, **kwargs):
        self.updated.append((bucket_id, kwargs))


@pytest.fixture
def self_write_route(monkeypatch):
    mcp = FakeMcp()
    buckets_web.register(mcp)
    manager = FakeBucketManager()
    monkeypatch.setattr(sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(sh, "bucket_mgr", manager)
    return mcp.routes[("/api/self", ("POST",))], manager


@pytest.mark.asyncio
async def test_dashboard_can_write_i_entry(self_write_route):
    handler, manager = self_write_route

    response = await handler(FakeRequest({"content": "I value careful changes.", "aspect": "values"}))
    data = json.loads(bytes(response.body))

    assert response.status_code == 200
    assert data == {"ok": True, "id": "self-bucket-1", "aspect": "values"}
    assert manager.create_calls == [{
        "content": "I value careful changes.",
        "tags": ["__i__", "aspect:values"],
        "importance": 6,
        "domain": ["self"],
        "valence": 0.5,
        "arousal": 0.3,
        "name": None,
        "bucket_type": "i",
        "why_remembered": "",
        "weight": 0.8,
        "source_tool": "I",
    }]
    assert manager.updated == [("self-bucket-1", {"dont_surface": True})]


@pytest.mark.asyncio
async def test_dashboard_rejects_empty_i_entry(self_write_route):
    handler, manager = self_write_route

    response = await handler(FakeRequest({"content": "  ", "aspect": "values"}))
    data = json.loads(bytes(response.body))

    assert response.status_code == 400
    assert data["ok"] is False
    assert manager.create_calls == []
