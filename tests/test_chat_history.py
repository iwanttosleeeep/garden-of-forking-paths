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
