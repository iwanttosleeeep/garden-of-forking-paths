import json

import pytest

from tools import _runtime as rt
from tools.journal.core import journal_core
from web import _shared as sh
from web import journal as journal_web


class FakeMcp:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(fn):
            self.routes[(path, tuple(methods))] = fn
            return fn
        return decorator


class FakeUpload:
    def __init__(self, payload):
        self.payload = payload

    async def read(self):
        return self.payload


class FakeRequest:
    headers = {}
    query_params = {}
    cookies = {}

    def __init__(self, payload):
        self.payload = payload

    async def form(self):
        return {"file": FakeUpload(self.payload)}


class FakeBucketManager:
    def __init__(self):
        self.buckets = []

    async def list_all(self, include_archive=False):
        return self.buckets

    async def create(self, **kwargs):
        bucket_id = f"journal-{len(self.buckets) + 1}"
        self.buckets.append({"id": bucket_id, "content": kwargs["content"], "metadata": {
            "source_tool": kwargs["source_tool"], "tags": kwargs["tags"],
            "created": "2026-07-18T12:00:00", "type": kwargs["bucket_type"],
        }})
        return bucket_id

    async def update(self, bucket_id, **kwargs):
        next(bucket for bucket in self.buckets if bucket["id"] == bucket_id)["metadata"].update(kwargs)


@pytest.mark.asyncio
async def test_sterling_import_is_deduplicated_and_hidden(monkeypatch):
    mcp = FakeMcp()
    journal_web.register(mcp)
    manager = FakeBucketManager()
    monkeypatch.setattr(sh, "_require_auth", lambda request: None)
    monkeypatch.setattr(sh, "bucket_mgr", manager)
    payload = json.dumps({"vault": {"entries": [
        {"id": "a", "timestamp": "2026-07-01T08:00:00.000Z", "mood": 4, "tags": ["work"], "note": "finished a task"},
        {"id": "b", "timestamp": "2026-07-02T08:00:00.000Z", "mood": 2, "note": "tired"},
    ]}}).encode()
    handler = mcp.routes[("/api/journal/sterling", ("POST",))]

    first = json.loads(bytes((await handler(FakeRequest(payload))).body))
    second = json.loads(bytes((await handler(FakeRequest(payload))).body))

    assert first["imported"] == 2
    assert first["average_mood"] == 3.0
    assert second["imported"] == 0
    assert second["skipped"] == 2
    assert manager.buckets[0]["metadata"]["dont_surface"] is True
    assert manager.buckets[0]["metadata"]["journal_date"] == "2026-07-01"


@pytest.mark.asyncio
async def test_journal_tool_only_reveals_text_for_explicit_query():
    manager = FakeBucketManager()
    manager.buckets = [{
        "id": "journal-1", "content": "Sterling 日记 · 2026-07-01 · 心情 4/5\n\nfinished a task",
        "metadata": {"source_tool": "sterling", "journal_date": "2026-07-01", "journal_mood": 4, "tags": ["work"]},
    }]
    old = rt.bucket_mgr
    rt.bucket_mgr = manager
    try:
        summary = await journal_core(days=30)
        result = await journal_core(query="task", days=30)
    finally:
        rt.bucket_mgr = old

    assert "finished a task" not in summary
    assert "finished a task" in result
