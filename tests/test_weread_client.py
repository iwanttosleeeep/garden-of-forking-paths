import pytest

import weread_client


@pytest.mark.asyncio
async def test_gateway_uses_fixed_endpoint_and_skill_version(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        content = b'{"books": []}'

        def json(self):
            return {"books": []}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["client"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return FakeResponse()

    monkeypatch.setattr(weread_client.httpx, "AsyncClient", FakeClient)
    result = await weread_client.gateway_call(
        {"weread": {"api_key": "wrk-private"}}, "/shelf/sync"
    )

    assert result == {"books": []}
    assert captured["url"] == weread_client.WEREAD_GATEWAY
    assert captured["json"]["skill_version"] == weread_client.WEREAD_SKILL_VERSION
    assert captured["json"]["api_name"] == "/shelf/sync"
    assert captured["headers"]["Authorization"] == "Bearer wrk-private"


@pytest.mark.asyncio
async def test_gateway_requires_key():
    with pytest.raises(weread_client.WeReadError, match="尚未配置"):
        await weread_client.gateway_call({"weread": {}}, "/shelf/sync")


def test_normalizers_keep_official_note_count_semantics():
    shelf = weread_client.normalize_shelf({
        "books": [{"bookId": "b", "title": "Book"}],
        "albums": [{"albumInfo": {"albumId": "a", "name": "Audio"}}],
        "mp": {"name": "Articles"},
    })
    notebooks = weread_client.normalize_notebooks({
        "books": [{
            "bookId": "b",
            "book": {"title": "Book"},
            "reviewCount": 2,
            "noteCount": 3,
            "bookmarkCount": 4,
        }]
    })

    assert shelf["visible_count"] == 3
    assert shelf["has_article_collection"] is True
    assert notebooks["books"][0]["total_note_count"] == 9
