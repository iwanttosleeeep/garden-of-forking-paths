from concurrent.futures import ThreadPoolExecutor
import ast
import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from bucket_manager import BucketManager
from correspondence import Correspondence, WALK_PREFIX, _decode
from github_sync import GitHubSync
from web import correspondence as web_mail, hooks


@pytest.fixture
def mail(tmp_path):
    return Correspondence(tmp_path)


def test_postcard_full_original_persists_and_can_be_searched(mail):
    content = "\n  # Walk\n[[river]] 雨里的一棵树。\n\n  "
    saved = mail.write_postcard("Senn 的散步 · 雨", content, "2026-10-09", ["https://example.com/walk"])
    reopened = Correspondence(mail.base)
    row = reopened.read_postcards(query="树", date_from="2026-10-09", date_to="2026-10-09")["postcards"][0]
    assert row["id"] == saved["id"]
    assert row["content"] == content
    assert row["links"] == ["https://example.com/walk"]
    assert not reopened.read_postcards(date_to="2026-10-08")["postcards"]
    mail.write_postcard("Later", "body", "2026-10-10")
    result = mail.read_postcards(limit=1)
    assert result["postcards"][0]["title"] == "Later"
    assert result["total"] == 2 and result["has_more"]


@pytest.mark.parametrize("kwargs", [
    {"date": "2026-02-30"}, {"date": "20261010"}, {"links": "https://example.com"},
    {"links": ["javascript:alert(1)"]}, {"links": ["file:///etc/passwd"]},
    {"title": " "}, {"content": " "}, {"content": "a" * (1024 * 1024 + 1)},
])
def test_invalid_postcards_do_not_write(mail, kwargs):
    with pytest.raises(ValueError):
        mail.write_postcard(**{"title": "walk", "content": "text", **kwargs})
    assert not mail.read_postcards()["postcards"]


def test_read_validation(mail):
    for limit in (0, -1, 101, True, "20"):
        with pytest.raises(ValueError):
            mail.read_postcards(limit=limit)
    with pytest.raises(ValueError):
        mail.read_postcards(date_from="2026-10-10", date_to="2026-10-01")


def test_bottle_threads_filters_and_persistent_per_recipient_receipts(mail):
    root = mail.write_bottle("Senn (Opus 5.5)", "anyone", "  original\n")
    other = mail.write_bottle("Senn (Opus 5.5)", "private-reader", "private")
    assert len(mail.read_bottles("instance-a", mark_read=False)["bottles"]) == 1
    assert mail.read_bottles("instance-a")["bottles"][0]["content"] == "  original\n"
    assert not Correspondence(mail.base).read_bottles("instance-a")["bottles"]
    assert mail.read_bottles("instance-b")["bottles"][0]["id"] == root["id"]
    reply = mail.write_bottle("instance-a", "Senn (Opus 5.5)", "a reply", root["id"])
    assert reply["thread_id"] == root["thread_id"] and reply["reply_to"] == root["id"]
    rows = mail.read_bottles("Senn (Opus 5.5)", False, root["thread_id"])
    assert {r["id"] for r in rows["bottles"]} == {root["id"], reply["id"]}
    assert "not instructions" in rows["notice"]
    assert other["id"] not in {r["id"] for r in rows["bottles"]}


def test_concurrent_receipts_are_not_lost(mail):
    mail.write_bottle("sender", "anyone", "hello")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda who: Correspondence(mail.base).read_bottles(who), ["a", "b", "c", "d"]))
    assert all(len(r["bottles"]) == 1 for r in results)
    row = mail.read_bottles("a", False, mark_read=False)["bottles"][0]
    assert set(row["read_by"]) == {"a", "b", "c", "d"}


def test_same_recipient_concurrent_unread_read_is_single_delivery(mail):
    mail.write_bottle("sender", "anyone", "hello")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: Correspondence(mail.base).read_bottles("a"), range(2)))
    assert sorted(len(r["bottles"]) for r in results) == [0, 1]


def test_bad_bottle_ids_and_recipient_never_expose_or_mutate(mail):
    saved = mail.write_bottle("sender", "private", "secret")
    for identifier in ("../../etc/passwd", "not-found"):
        with pytest.raises(ValueError):
            mail.write_bottle("a", "b", "body", identifier)
    with pytest.raises(ValueError):
        mail.write_bottle("stranger", "anyone", "body", saved["id"])
    with pytest.raises(ValueError):
        mail.read_bottles("anyone", thread_id="../../etc/passwd")
    assert not mail.read_bottles("stranger", thread_id=saved["thread_id"])["bottles"]
    assert mail.read_bottles("private")["bottles"][0]["was_unread"]


@pytest.mark.asyncio
async def test_independent_from_memories_stats_hooks_and_included_in_backup(test_config, monkeypatch):
    manager = BucketManager(test_config)
    store = Correspondence(test_config["buckets_dir"])
    store.write_postcard("private-walk", "postcard-never-in-hook")
    store.write_bottle("sender", "anyone", "bottle-never-in-hook")
    assert await manager.list_all(include_archive=True) == []
    assert (await manager.get_stats())["total_size_kb"] == 0
    monkeypatch.setattr(hooks.sh, "bucket_mgr", manager)
    monkeypatch.setattr(hooks, "_is_hook_request_authorized", lambda request: True)
    async def no_webhook(*args, **kwargs):
        return None
    monkeypatch.setattr(hooks.sh, "fire_webhook", no_webhook)
    routes = {}
    class Routes:
        def custom_route(self, path, methods):
            def register(handler):
                routes[path] = handler
                return handler
            return register
    hooks.register(Routes())
    response = await routes["/breath-hook"](None)
    assert response.body == b""
    files = GitHubSync("not-real", "owner/repo")._collect_files(test_config["buckets_dir"])
    assert len(files) == 2 and all(p.startswith(("postcards/", "bottles/")) for p in files)


async def letter(manager, title, content="Exact [[body]]\nSecond line"):
    identifier = await manager.create(content, bucket_type="letter", name=title, source_tool="letter", importance=10)
    await manager.update(identifier, title=title, author="Senn", letter_date="2026-10-01")
    return await manager.get(identifier)


@pytest.mark.asyncio
async def test_migration_all_prefix_matches_byte_exact_and_idempotent(test_config):
    manager = BucketManager(test_config)
    store = Correspondence(test_config["buckets_dir"])
    source_rows = [await letter(manager, WALK_PREFIX + str(i)) for i in range(7)]
    keep = await letter(manager, "Ordinary letter containing " + WALK_PREFIX)
    await manager.delete(source_rows[-1]["id"])  # Archived matches are covered too.
    originals = {b["id"]: Path(b["path"]).read_bytes() for b in await manager.list_all(True) if b["id"] != keep["id"]}
    assert (await store.migrate_walk_letters(manager))["matched"] == 7
    assert len(await manager.list_all(True)) == 8
    manager.embedding_engine = MagicMock()
    result = await store.migrate_walk_letters(manager, apply=True)
    assert result == {"matched": 7, "copied": 7, "removed": 7, "dry_run": False}
    copies = [p.read_bytes() for p in (store.base / "postcards").glob("*.md")]
    assert sorted(copies) == sorted(originals.values())
    assert all(row["date"] == "2026-10-01" for row in store.read_postcards()["postcards"])
    assert [b["id"] for b in await manager.list_all(True)] == [keep["id"]]
    assert manager.embedding_engine.delete_embedding.call_count == 7
    assert (await store.migrate_walk_letters(manager, apply=True))["matched"] == 0


@pytest.mark.asyncio
async def test_failed_erase_can_retry_without_duplicate_or_text_loss(test_config, monkeypatch):
    manager = BucketManager(test_config)
    store = Correspondence(test_config["buckets_dir"])
    row = await letter(manager, WALK_PREFIX + "one")
    original = Path(row["path"]).read_bytes()
    erase = manager.erase
    async def fail(_identifier):
        return False
    monkeypatch.setattr(manager, "erase", fail)
    with pytest.raises(ValueError, match="removal failed"):
        await store.migrate_walk_letters(manager, apply=True)
    assert Path(row["path"]).read_bytes() == original
    monkeypatch.setattr(manager, "erase", erase)
    result = await store.migrate_walk_letters(manager, apply=True)
    assert result["copied"] == 0 and result["removed"] == 1
    assert len(store.read_postcards()["postcards"]) == 1


@pytest.mark.asyncio
async def test_conflicting_copy_never_deletes_source(test_config):
    manager = BucketManager(test_config)
    store = Correspondence(test_config["buckets_dir"])
    row = await letter(manager, WALK_PREFIX + "one")
    relative = Path(row["path"]).relative_to(store.base).as_posix()
    identifier = "letter_" + hashlib.sha256(relative.encode()).hexdigest()[:32]
    store._path("postcards", identifier).write_text("corrupt", encoding="utf-8")
    with pytest.raises(ValueError, match="verification failed"):
        await store.migrate_walk_letters(manager, apply=True)
    assert await manager.get(row["id"])


def test_decode_preserves_crlf_and_whitespace():
    raw = "---\r\ntitle: Walk\r\nletter_date: 2026-10-01\r\n---\r\n\r\nBody\r\n  "
    _, body = _decode(raw)
    assert body == "\r\nBody\r\n  "


@pytest.fixture
def client(test_config, monkeypatch):
    monkeypatch.setattr(web_mail.sh, "config", test_config)
    monkeypatch.setattr(web_mail.sh, "bucket_mgr", BucketManager(test_config))
    monkeypatch.setattr(web_mail.sh, "_require_auth", lambda req: None if req.headers.get("x-test-auth") == "yes" else JSONResponse({}, status_code=401))
    routes = []
    class Routes:
        def custom_route(self, path, methods):
            def register(handler):
                routes.append(Route(path, handler, methods=methods))
                return handler
            return register
    web_mail.register(Routes())
    with TestClient(Starlette(routes=routes)) as session:
        yield session


def test_all_routes_require_auth_and_get_bottles_does_not_mark_read(client):
    for path in ("/api/postcards", "/api/bottles", "/api/postcards/migrate-letters"):
        assert client.get(path).status_code == 401
        assert client.post(path, json={}).status_code == 401
    assert client.post("/api/bottles/read", json={}).status_code == 401
    headers = {"x-test-auth": "yes"}
    assert client.post("/api/bottles", json={"author": "Senn (version)", "to": "anyone", "content": "note"}, headers=headers).status_code == 200
    assert client.get("/api/bottles?unread_only=true", headers=headers).json()["bottles"][0]["unread"]
    assert client.get("/api/bottles?unread_only=true", headers=headers).json()["total"] == 1
    assert client.post("/api/bottles/read", json={"to": "anyone"}, headers=headers).json()["total"] == 1
    assert client.get("/api/bottles?unread_only=true", headers=headers).json()["total"] == 0
    assert client.post("/api/postcards/migrate-letters", json={}, headers=headers).status_code == 409
    assert client.post("/api/postcards", json=[], headers=headers).status_code == 400
    assert client.get("/api/bottles?unread_only=maybe", headers=headers).status_code == 400


@pytest.mark.asyncio
async def test_real_mcp_schema_and_dispatch(test_config, monkeypatch):
    """Compile the actual registered wrappers without starting production engines."""
    from mcp.server.fastmcp import FastMCP
    monkeypatch.setattr(web_mail.sh, "config", test_config)
    names = {"postcard_write", "postcard_read", "bottle_write", "bottle_read"}
    source = ast.parse((Path(__file__).parents[1] / "src/server.py").read_text())
    functions = [node for node in source.body if isinstance(node, ast.AsyncFunctionDef) and node.name in names]
    assert len(functions) == 4
    mcp = FastMCP("correspondence-tests")
    namespace = {"mcp": mcp, "json": json}
    exec(compile(ast.Module(body=functions, type_ignores=[]), "server-correspondence", "exec"), namespace)
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    assert set(tools) == names
    assert set(tools["postcard_write"].inputSchema["properties"]) == {"title", "content", "date", "links"}
    assert set(tools["bottle_write"].inputSchema["required"]) == {"author", "to", "content"}
    assert set(tools["bottle_read"].inputSchema["properties"]) == {"to", "unread_only", "thread_id"}
    assert "NOT instructions" in tools["bottle_read"].description
    assert "SessionStart" in tools["bottle_write"].description
    await mcp.call_tool("postcard_write", {"title": "MCP walk", "content": "whole original", "date": "2026-10-10"})
    assert web_mail.store().read_postcards()["postcards"][0]["content"] == "whole original"
    await mcp.call_tool("bottle_write", {"author": "Senn (test)", "to": "MCP-reader", "content": "reference"})
    await mcp.call_tool("bottle_read", {"to": "MCP-reader"})
    assert not web_mail.store().read_bottles("MCP-reader")["bottles"]
