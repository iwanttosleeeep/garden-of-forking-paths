import json

import pytest

from web import _shared as sh
from web import chat_history


class FakeMcp:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            self.routes[(path, tuple(methods))] = handler
            return handler
        return decorator


class FakeRequest:
    headers = {}
    cookies = {}

    def __init__(self, name):
        self.path_params = {"name": name}


class UploadRequest:
    def __init__(self, name, content):
        self.filename = name
        self.content = content
        self.read_size = None

    async def form(self):
        return {"file": self}

    async def read(self, size):
        self.read_size = size
        return self.content[:size]


@pytest.mark.asyncio
async def test_long_unicode_uploads_remain_distinct_editable_and_deletable(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_history, "_directory", lambda: str(tmp_path))
    monkeypatch.setattr(sh, "_require_auth", lambda request: None)
    mcp = FakeMcp()
    chat_history.register(mcp)
    upload = mcp.routes[("/api/chat-history", ("POST",))]
    names = ["今天的聊天.md", "明天的聊天.md", "共同阅读" * 100 + "甲.md", "共同阅读" * 100 + "乙.md"]
    for number, name in enumerate(names):
        request = UploadRequest(name, f"# Chat {number}".encode())
        response = await upload(request)
        assert response.status_code == 200
        assert request.read_size == chat_history._MAX_BYTES + 1
    documents = chat_history._list()
    assert len(documents) == 4
    assert {doc["file"] for doc in documents}.issuperset(names[:2])
    for doc in documents:
        assert doc["file"].endswith(".md")
        assert len(doc["file"].encode()) <= 160
        assert chat_history._safe_name(doc["file"]) == doc["file"]
        request = FakeRequest(doc["file"])

        async def body():
            return {"title": "Edited"}

        request.json = body
        edited = await mcp.routes[("/api/chat-history/{name}", ("PATCH",))](request)
        assert edited.status_code == 200
        deleted = await mcp.routes[("/api/chat-history/{name}", ("DELETE",))](request)
        assert deleted.status_code == 200
    assert not chat_history._list()


@pytest.mark.asyncio
async def test_reupload_preserves_metadata_and_invalid_upload_preserves_content(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_history, "_directory", lambda: str(tmp_path))
    monkeypatch.setattr(sh, "_require_auth", lambda request: None)
    mcp = FakeMcp()
    chat_history.register(mcp)
    upload = mcp.routes[("/api/chat-history", ("POST",))]
    (tmp_path / "chat.md").write_text("old", encoding="utf-8")
    chat_history._write_index({"chat.md": {"title": "Our chat", "description": "Notes"}})
    assert (await upload(UploadRequest("chat.md", b"new"))).status_code == 200
    assert chat_history._index()["chat.md"]["title"] == "Our chat"
    assert chat_history._index()["chat.md"]["description"] == "Notes"
    monkeypatch.setattr(chat_history, "_MAX_BYTES", 8)
    for content in [b"too long text", b"\xff", b""]:
        assert (await upload(UploadRequest("chat.md", content))).status_code == 400
        assert (tmp_path / "chat.md").read_text() == "new"


@pytest.mark.asyncio
async def test_deleting_chat_history_removes_markdown_and_index(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_history, "_directory", lambda: str(tmp_path))
    monkeypatch.setattr(sh, "_require_auth", lambda request: None)
    (tmp_path / "conversation.md").write_text("# private", encoding="utf-8")
    chat_history._write_index({"conversation.md": {"title": "Conversation"}})
    mcp = FakeMcp()
    chat_history.register(mcp)

    response = await mcp.routes[("/api/chat-history/{name}", ("DELETE",))](FakeRequest("conversation.md"))

    assert json.loads(bytes(response.body))["ok"] is True
    assert not (tmp_path / "conversation.md").exists()
    assert chat_history._index() == {}
