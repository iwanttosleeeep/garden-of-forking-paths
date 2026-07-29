"""Testable HTTP application and runtime lifecycle assembly.

This module deliberately has no Ombre engine construction at import time. The
CLI entry point creates the concrete services, then passes them into the small
factory and lifecycle objects below. A future desktop host can use the same
boundary without importing the side-effectful ``server`` module.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Mapping

import httpx
from starlette.middleware.cors import CORSMiddleware

from utils import parse_bool
from web.request_limits import (
    MCPRequestBodyLimitMiddleware,
    is_mcp_endpoint_path,
    is_sse_endpoint_path,
)


DEFAULT_MAX_MCP_REQUEST_BYTES = 4 * 1024 * 1024
DEFAULT_HEALTH_PROBE_TIMEOUT_SECONDS = 5.0
DEFAULT_KEEPALIVE_INITIAL_DELAY_SECONDS = 10.0
DEFAULT_KEEPALIVE_INTERVAL_SECONDS = 60.0

TokenValidator = Callable[..., bool]
AsyncCallback = Callable[[], Awaitable[Any]]


@dataclass(frozen=True)
class HTTPRuntimeSettings:
    """Normalized settings used while assembling an HTTP MCP application."""

    auth_required: bool
    max_request_bytes: int

    @classmethod
    def from_config(
        cls,
        config: Mapping[str, Any],
        *,
        default_max_request_bytes: int = DEFAULT_MAX_MCP_REQUEST_BYTES,
    ) -> "HTTPRuntimeSettings":
        limits = config.get("limits")
        if not isinstance(limits, Mapping):
            limits = {}
        try:
            max_request_bytes = int(
                limits.get("max_mcp_request_bytes", default_max_request_bytes)
            )
        except (TypeError, ValueError, OverflowError):
            max_request_bytes = default_max_request_bytes
        if max_request_bytes < 0:
            max_request_bytes = default_max_request_bytes
        return cls(
            auth_required=parse_bool(
                config.get("mcp_require_auth", True), default=True
            ),
            max_request_bytes=max_request_bytes,
        )


def _first_forwarded_value(value: str) -> str:
    return value.split(",", 1)[0].strip()


def _request_resource(scope: Mapping[str, Any], headers: Mapping[bytes, bytes]) -> tuple[str, str]:
    proto = _first_forwarded_value(
        headers.get(b"x-forwarded-proto", b"").decode("latin-1")
    ) or str(scope.get("scheme", "http"))
    host = _first_forwarded_value(
        (headers.get(b"x-forwarded-host") or headers.get(b"host", b"")).decode(
            "latin-1"
        )
    )
    path = str(scope.get("path", ""))
    base = f"{proto}://{host}"
    return f"{base}{path.rstrip('/')}", base


class MCPAuthMiddleware:
    """Require an OAuth bearer token for the streamable MCP endpoint."""

    def __init__(
        self,
        app: Any,
        *,
        auth_required: bool,
        token_validator: TokenValidator,
        path_matcher: Callable[[object], bool] = is_mcp_endpoint_path,
    ) -> None:
        self.app = app
        self.auth_required = bool(auth_required)
        self.token_validator = token_validator
        self.path_matcher = path_matcher

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = str(scope.get("path", ""))
        if (
            scope.get("type") == "http"
            and str(scope.get("method", "")).upper() != "OPTIONS"
            and self.auth_required
            and self.path_matcher(path)
        ):
            headers = {key.lower(): value for key, value in scope.get("headers", [])}
            auth = headers.get(b"authorization", b"").decode("latin-1")
            _request_endpoint, base = _request_resource(scope, headers)
            # Both HTTP transports represent the same OAuth resource.
            resource = f"{base}/mcp"
            valid = auth.startswith("Bearer ") and self.token_validator(
                auth[7:], resource=resource
            )
            if not valid:
                endpoint = "mcp"
                metadata_url = (
                    f"{base}/.well-known/oauth-protected-resource/{endpoint}"
                )
                challenge = (
                    'Bearer realm="Ombre Brain",'
                    f' resource_metadata="{metadata_url}", scope="mcp"'
                )
                body = json.dumps(
                    {
                        "error": "Unauthorized",
                        "resource_metadata": metadata_url,
                    }
                ).encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            [b"content-type", b"application/json"],
                            [b"www-authenticate", challenge.encode()],
                            [b"content-length", str(len(body)).encode()],
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": body,
                        "more_body": False,
                    }
                )
                return
        await self.app(scope, receive, send)


class MCPJSONAcceptShim:
    """Select JSON for Streamable HTTP clients that omit or wildcard Accept."""

    def __init__(
        self,
        app: Any,
        *,
        path_matcher: Callable[[object], bool] = is_mcp_endpoint_path,
    ) -> None:
        self.app = app
        self.path_matcher = path_matcher

    @staticmethod
    def _wildcard_allows_json(media_range: bytes) -> bool:
        parts = [part.strip().lower() for part in media_range.split(b";")]
        if not parts or parts[0] not in (b"*/*", b"application/*"):
            return False
        for parameter in parts[1:]:
            key, separator, value = parameter.partition(b"=")
            if separator and key.strip() == b"q":
                try:
                    quality = float(value.strip())
                except ValueError:
                    return False
                return 0 < quality <= 1
        return True

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") == "http" and self.path_matcher(scope.get("path")):
            headers = list(scope.get("headers", []))
            combined = b", ".join(
                value for key, value in headers if key.lower() == b"accept"
            ).strip()
            raw_ranges = [item.strip() for item in combined.lower().split(b",") if item.strip()]
            media_ranges = [item.split(b";", 1)[0].strip() for item in raw_ranges]
            has_json = b"application/json" in media_ranges
            accepts_wildcard = any(self._wildcard_allows_json(item) for item in raw_ranges)
            if not combined or (accepts_wildcard and not has_json):
                normalized = b"application/json" if not combined else combined + b", application/json"
                headers = [(key, value) for key, value in headers if key.lower() != b"accept"]
                headers.append((b"accept", normalized))
                scope = dict(scope)
                scope["headers"] = headers
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    """Apply browser hardening headers to success and error responses."""

    _HEADERS = (
        (b"content-security-policy", b"frame-ancestors 'none'"),
        (b"x-frame-options", b"DENY"),
        (b"x-content-type-options", b"nosniff"),
        (b"referrer-policy", b"no-referrer"),
        (b"permissions-policy", b"camera=(), geolocation=(), microphone=(), payment=(), usb=()"),
    )

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: dict) -> None:
            if message.get("type") == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _value in headers}
                headers.extend(
                    (key, value) for key, value in self._HEADERS if key not in existing
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)


@dataclass
class RuntimeLifecycle:
    """Own background service startup and shutdown for one HTTP app lifespan."""

    logger: Any
    decay_engine: Any = None
    embedding_outbox: Any = None
    ensure_ollama_child: AsyncCallback | None = None
    stop_ollama_child: AsyncCallback | None = None
    restart_github_auto_task: Callable[[int], Any] | None = None
    github_auto_interval: int = 0
    keepalive_url: str = ""
    keepalive_initial_delay: float = DEFAULT_KEEPALIVE_INITIAL_DELAY_SECONDS
    keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL_SECONDS
    health_probe_timeout: float = DEFAULT_HEALTH_PROBE_TIMEOUT_SECONDS
    _keepalive_task: asyncio.Task | None = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False, repr=False)

    async def _run_async_step(self, label: str, callback: AsyncCallback | None) -> None:
        if callback is None:
            return
        try:
            await callback()
        except Exception as exc:
            self.logger.warning("%s failed: %s", label, exc)

    def _start_optional_services(self) -> None:
        if self.github_auto_interval > 0 and self.restart_github_auto_task is not None:
            try:
                self.restart_github_auto_task(self.github_auto_interval)
            except Exception as exc:
                self.logger.warning("github auto-sync start failed: %s", exc)

    async def _keepalive_loop(self) -> None:
        await asyncio.sleep(max(0.0, self.keepalive_initial_delay))
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    await client.get(
                        self.keepalive_url,
                        timeout=self.health_probe_timeout,
                    )
                    self.logger.debug("Keepalive ping OK")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.logger.warning("Keepalive ping failed: %s", exc)
                await asyncio.sleep(max(0.01, self.keepalive_interval))

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._start_optional_services()
        await self._run_async_step(
            "decay engine start",
            getattr(self.decay_engine, "start", None),
        )
        await self._run_async_step("ollama child boot", self.ensure_ollama_child)
        await self._run_async_step(
            "embedding outbox start",
            getattr(self.embedding_outbox, "start", None),
        )
        if self.keepalive_url:
            self._keepalive_task = asyncio.create_task(
                self._keepalive_loop(),
                name="ombre-health-keepalive",
            )

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False

        task = self._keepalive_task
        self._keepalive_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        if self.restart_github_auto_task is not None:
            try:
                self.restart_github_auto_task(0)
            except Exception as exc:
                self.logger.warning("github auto-sync stop failed: %s", exc)

        await self._run_async_step(
            "embedding outbox stop",
            getattr(self.embedding_outbox, "stop", None),
        )
        await self._run_async_step(
            "decay engine stop",
            getattr(self.decay_engine, "stop", None),
        )
        await self._run_async_step("ollama child stop", self.stop_ollama_child)


def install_runtime_lifespan(app: Any, lifecycle: RuntimeLifecycle) -> Any:
    """Compose Ombre runtime services with an app's existing lifespan."""

    parent_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def managed_lifespan(lifespan_app: Any):
        async with parent_lifespan(lifespan_app):
            await lifecycle.start()
            try:
                yield
            finally:
                await lifecycle.stop()

    app.router.lifespan_context = managed_lifespan
    return app


def build_http_app(
    mcp: Any,
    transport: str,
    *,
    settings: HTTPRuntimeSettings,
    token_validator: TokenValidator,
    lifecycle: RuntimeLifecycle,
) -> Any:
    """Build the HTTP/SSE ASGI app with one consistent middleware stack."""

    if transport == "streamable-http":
        app = mcp.streamable_http_app()
    elif transport == "sse":
        app = mcp.sse_app()
    else:
        raise ValueError(f"HTTP app cannot be built for transport: {transport}")

    mcp_path_matcher = is_sse_endpoint_path if transport == "sse" else is_mcp_endpoint_path

    install_runtime_lifespan(app, lifecycle)
    app.add_middleware(
        MCPRequestBodyLimitMiddleware,
        max_bytes=settings.max_request_bytes,
        path_matcher=mcp_path_matcher,
    )
    if transport == "streamable-http":
        app.add_middleware(MCPJSONAcceptShim, path_matcher=mcp_path_matcher)
    app.add_middleware(
        MCPAuthMiddleware,
        auth_required=settings.auth_required,
        token_validator=token_validator,
        path_matcher=mcp_path_matcher,
    )
    # Starlette wraps in reverse registration order. CORS must be outside auth
    # so browser preflights and 401 challenges receive the expected headers.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", "WWW-Authenticate"],
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.state.ombre_http_settings = settings
    app.state.ombre_runtime_lifecycle = lifecycle
    return app
