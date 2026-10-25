import json
import re

import pytest

from web import _shared as sh
from web import chat_history


@pytest.fixture
def transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_history, "_directory", lambda: str(tmp_path))

    def write(content, name="conversation.md", title="Our chat"):
        (tmp_path / name).write_text(content, encoding="utf-8")
        index = chat_history._index()
        index[name] = {"title": title}
        chat_history._write_index(index)

    return write


def _page_body(result):
    return result.split("\n", 1)[1].rsplit("\n\n[字符范围 ", 1)[0]


def test_recall_unicode_pages_reconstruct_original_without_gaps(transcript):
    original = "中文🙂 café e\u0301\n  保留空白  \n" * 2400
    transcript(original)
    offset = 0
    bodies = []
    while True:
        result = chat_history.read_document("Our chat", offset=offset, limit=1999)
        body = _page_body(result)
        assert len(body) <= 1999
        bodies.append(body)
        if "已到文档末尾" in result:
            break
        continuation = re.search(r'recall\(title="conversation.md", offset=(\d+), limit=1999\)', result)
        assert continuation is not None
        next_offset = int(continuation.group(1))
        assert next_offset > offset
        offset = next_offset
    assert "".join(bodies) == original


def test_recall_default_limit_and_reading_previously_truncated_tail(transcript):
    transcript("首" * 30000 + "这是原来无法读取的末尾🙂")
    first = chat_history.read_document("Our chat")
    assert _page_body(first) == "首" * 30000
    assert 'recall(title="conversation.md", offset=30000, limit=30000)' in first
    last = chat_history.read_document("conversation.md", offset=30000, limit=100)
    assert _page_body(last) == "这是原来无法读取的末尾🙂"
    assert "已到文档末尾" in last


@pytest.mark.parametrize("content", ["", "中🙂文"])
def test_recall_exact_end_empty_document_and_out_of_range(transcript, content):
    transcript(content)
    result = chat_history.read_document("Our chat", limit=3)
    assert _page_body(result) == content
    assert "已到文档末尾" in result
    assert "按需继续" not in result
    end = chat_history.read_document("Our chat", offset=len(content))
    assert _page_body(end) == ""
    assert "已到文档末尾" in end
    assert "offset 超出文档长度" in chat_history.read_document("Our chat", offset=len(content) + 1)


@pytest.mark.parametrize("offset,limit,error", [
    (-1, 10, "offset"), (1.5, 10, "offset"), (True, 10, "offset"),
    (0, 0, "limit"), (0, -1, "limit"), (0, 30001, "limit"),
    (0, 1.5, "limit"), (0, False, "limit"),
])
def test_recall_rejects_invalid_pagination_before_reading(monkeypatch, offset, limit, error):
    def unexpected_read():
        raise AssertionError("Invalid parameters must not read the library")

    monkeypatch.setattr(chat_history, "_list", unexpected_read)
    assert f"{error} 必须" in chat_history.read_document("Our chat", offset=offset, limit=limit)


def test_recall_keeps_exact_selection_and_handles_duplicate_titles(transcript):
    transcript("first document")
    assert "请提供" in chat_history.read_document("")
    assert "未找到" in chat_history.read_document("Our")
    transcript("second document", name="other.md")
    assert "标题不唯一" in chat_history.read_document("Our chat")
    assert _page_body(chat_history.read_document("conversation.md")) == "first document"
    assert _page_body(chat_history.read_document("other.md")) == "second document"


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
