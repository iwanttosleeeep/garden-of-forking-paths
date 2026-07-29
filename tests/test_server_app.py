import asyncio
import ast
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette

from server_app import (
    DEFAULT_MAX_MCP_REQUEST_BYTES,
    HTTPRuntimeSettings,
    MCPJSONAcceptShim,
    MCPAuthMiddleware,
    RuntimeLifecycle,
    build_http_app,
    install_runtime_lifespan,
)


class RecordingLogger:
    def __init__(self):
        self.messages = []

    def _record(self, level, message, *args):
        self.messages.append((level, message % args if args else message))

    def debug(self, message, *args):
        self._record("debug", message, *args)

    def info(self, message, *args):
        self._record("info", message, *args)

    def warning(self, message, *args):
        self._record("warning", message, *args)


class RecordingASGIApp:
    def __init__(self):
        self.scopes = []

    async def __call__(self, scope, receive, send):
        self.scopes.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})


async def _empty_receive():
    return {"type": "http.request", "body": b"", "more_body": False}


def _collect_into(messages):
    async def send(message):
        messages.append(message)

    return send


async def _discard_send(_message):
    return None


def test_server_registers_all_tools_on_one_fastmcp_instance():
    tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "src" / "server.py").read_text(
            encoding="utf-8"
        )
    )
    fastmcp_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "FastMCP"
    ]
    assert len(fastmcp_calls) == 1
    assert "mcp_extra" not in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


@pytest.mark.parametrize(
    ("config", "auth_required", "limit"),
    [
        ({}, True, DEFAULT_MAX_MCP_REQUEST_BYTES),
        ({"mcp_require_auth": "false", "limits": {"max_mcp_request_bytes": 0}}, False, 0),
        ({"limits": {"max_mcp_request_bytes": "1024"}}, True, 1024),
        ({"limits": {"max_mcp_request_bytes": -1}}, True, DEFAULT_MAX_MCP_REQUEST_BYTES),
        ({"limits": {"max_mcp_request_bytes": "bad"}}, True, DEFAULT_MAX_MCP_REQUEST_BYTES),
    ],
)
def test_http_runtime_settings_are_normalized(config, auth_required, limit):
    settings = HTTPRuntimeSettings.from_config(config)

    assert settings.auth_required is auth_required
    assert settings.max_request_bytes == limit


@pytest.mark.asyncio
async def test_accept_shim_adds_json_for_wildcard_client():
    downstream = RecordingASGIApp()
    middleware = MCPJSONAcceptShim(downstream)
    messages = []
    scope = {
        "type": "http",
        "path": "/mcp",
        "headers": [(b"accept", b"*/*")],
    }

    await middleware(scope, _empty_receive, _collect_into(messages))

    forwarded = dict(downstream.scopes[0]["headers"])[b"accept"]
    assert b"application/json" in forwarded


@pytest.mark.asyncio
async def test_accept_shim_leaves_non_mcp_routes_unchanged():
    downstream = RecordingASGIApp()
    middleware = MCPJSONAcceptShim(downstream)
    scope = {
        "type": "http",
        "path": "/health",
        "headers": [(b"accept", b"application/json")],
    }

    await middleware(scope, _empty_receive, _discard_send)

    assert downstream.scopes[0] is scope


@pytest.mark.asyncio
async def test_accept_shim_preserves_explicit_sse_only_accept():
    downstream = RecordingASGIApp()
    middleware = MCPJSONAcceptShim(downstream)
    scope = {
        "type": "http",
        "path": "/mcp",
        "headers": [(b"accept", b"text/event-stream")],
    }

    await middleware(scope, _empty_receive, _discard_send)

    assert dict(downstream.scopes[0]["headers"])[b"accept"] == b"text/event-stream"


@pytest.mark.asyncio
async def test_auth_middleware_rejects_missing_token_with_canonical_metadata_url():
    downstream = RecordingASGIApp()
    middleware = MCPAuthMiddleware(
        downstream,
        auth_required=True,
        token_validator=lambda *_args, **_kwargs: False,
    )
    messages = []
    scope = {
        "type": "http",
        "scheme": "http",
        "path": "/mcp",
        "headers": [
            (b"host", b"internal:8000"),
            (b"x-forwarded-proto", b"https, http"),
            (b"x-forwarded-host", b"ombre.example, proxy.local"),
        ],
    }

    await middleware(scope, _empty_receive, _collect_into(messages))

    assert downstream.scopes == []
    assert messages[0]["status"] == 401
    payload = json.loads(messages[1]["body"])
    assert payload["resource_metadata"] == (
        "https://ombre.example/.well-known/oauth-protected-resource/mcp"
    )


@pytest.mark.asyncio
async def test_auth_middleware_validates_token_against_exact_resource():
    downstream = RecordingASGIApp()
    seen = {}

    def validator(token, *, resource):
        seen.update(token=token, resource=resource)
        return True

    middleware = MCPAuthMiddleware(
        downstream,
        auth_required=True,
        token_validator=validator,
    )
    scope = {
        "type": "http",
        "scheme": "https",
        "path": "/mcp/",
        "headers": [
            (b"host", b"ombre.example"),
            (b"authorization", b"Bearer token-1"),
        ],
    }

    await middleware(scope, _empty_receive, _discard_send)

    assert seen == {"token": "token-1", "resource": "https://ombre.example/mcp"}
    assert downstream.scopes == [scope]


@pytest.mark.asyncio
async def test_auth_middleware_can_be_explicitly_disabled():
    downstream = RecordingASGIApp()
    middleware = MCPAuthMiddleware(
        downstream,
        auth_required=False,
        token_validator=lambda *_args, **_kwargs: False,
    )
    scope = {"type": "http", "path": "/mcp", "headers": []}

    await middleware(scope, _empty_receive, _discard_send)

    assert downstream.scopes == [scope]


@pytest.mark.asyncio
async def test_auth_middleware_allows_cors_preflight_without_bearer_token():
    downstream = RecordingASGIApp()
    middleware = MCPAuthMiddleware(
        downstream,
        auth_required=True,
        token_validator=lambda *_args, **_kwargs: False,
    )
    scope = {"type": "http", "method": "OPTIONS", "path": "/mcp", "headers": []}

    await middleware(scope, _empty_receive, _discard_send)

    assert downstream.scopes == [scope]


@pytest.mark.asyncio
async def test_auth_middleware_does_not_match_mcp_lookalike_path():
    downstream = RecordingASGIApp()
    middleware = MCPAuthMiddleware(
        downstream,
        auth_required=True,
        token_validator=lambda *_args, **_kwargs: False,
    )
    scope = {"type": "http", "method": "GET", "path": "/mcp-evil", "headers": []}

    await middleware(scope, _empty_receive, _discard_send)

    assert downstream.scopes == [scope]


class RecordingService:
    def __init__(self, name, events):
        self.name = name
        self.events = events

    async def start(self):
        self.events.append(f"{self.name}:start")

    async def stop(self):
        self.events.append(f"{self.name}:stop")


@pytest.mark.asyncio
async def test_runtime_lifecycle_cancels_keepalive_on_shutdown():
    lifecycle = RuntimeLifecycle(
        logger=RecordingLogger(),
        keepalive_url="http://127.0.0.1:1/health",
        keepalive_initial_delay=3600,
    )

    await lifecycle.start()
    task = lifecycle._keepalive_task
    await asyncio.sleep(0)
    await lifecycle.stop()

    assert task is not None
    assert task.done()
    assert lifecycle._keepalive_task is None


@pytest.mark.asyncio
async def test_runtime_lifespan_composes_with_parent_lifespan():
    events = []

    @asynccontextmanager
    async def parent(_app):
        events.append("parent:start")
        try:
            yield
        finally:
            events.append("parent:stop")

    class FakeLifecycle:
        async def start(self):
            events.append("runtime:start")

        async def stop(self):
            events.append("runtime:stop")

    app = SimpleNamespace(router=SimpleNamespace(lifespan_context=parent))
    install_runtime_lifespan(app, FakeLifecycle())

    async with app.router.lifespan_context(app):
        events.append("body")

    assert events == [
        "parent:start",
        "runtime:start",
        "body",
        "runtime:stop",
        "parent:stop",
    ]


@pytest.mark.parametrize("transport", ["streamable-http", "sse"])
def test_build_http_app_uses_same_managed_stack_for_both_http_transports(transport):
    class FakeMCP:
        def streamable_http_app(self):
            return Starlette()

        def sse_app(self):
            return Starlette()

    lifecycle = RuntimeLifecycle(logger=RecordingLogger())
    settings = HTTPRuntimeSettings(auth_required=False, max_request_bytes=2048)

    app = build_http_app(
        FakeMCP(),
        transport,
        settings=settings,
        token_validator=lambda *_args, **_kwargs: False,
        lifecycle=lifecycle,
    )

    middleware_names = {item.cls.__name__ for item in app.user_middleware}
    assert middleware_names >= {
        "CORSMiddleware",
        "MCPRequestBodyLimitMiddleware",
        "MCPAuthMiddleware",
        "SecurityHeadersMiddleware",
    }
    if transport == "streamable-http":
        assert "MCPJSONAcceptShim" in middleware_names
    assert app.state.ombre_http_settings is settings
    assert app.state.ombre_runtime_lifecycle is lifecycle


def test_build_http_app_rejects_stdio_transport():
    with pytest.raises(ValueError, match="stdio"):
        build_http_app(
            SimpleNamespace(),
            "stdio",
            settings=HTTPRuntimeSettings(True, 1024),
            token_validator=lambda *_args, **_kwargs: False,
            lifecycle=RuntimeLifecycle(logger=RecordingLogger()),
        )
