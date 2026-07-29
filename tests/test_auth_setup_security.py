import asyncio
import json
from types import SimpleNamespace

import pytest
from starlette.datastructures import Headers

from web import auth as auth_web


class FakeMCP:
    def __init__(self):
        self.routes = {}

    def custom_route(self, path, methods):
        def decorator(handler):
            for method in methods:
                self.routes[(method, path)] = handler
            return handler

        return decorator


def request(headers, peer):
    return SimpleNamespace(headers=Headers(headers), client=SimpleNamespace(host=peer))


@pytest.mark.parametrize(
    ("authority", "allowed"),
    [
        ("localhost", True),
        ("127.0.0.1:8000", True),
        ("[::1]:8000", True),
        ("attacker.example", False),
        ("127.1", False),
        ("localhost:0", False),
    ],
)
def test_first_setup_requires_unambiguous_direct_loopback(monkeypatch, authority, allowed):
    monkeypatch.delenv("OMBRE_SETUP_TOKEN", raising=False)
    assert auth_web._setup_request_allowed(request({"Host": authority}, "127.0.0.1")) is allowed


def test_forwarded_request_is_not_treated_as_local(monkeypatch):
    monkeypatch.delenv("OMBRE_SETUP_TOKEN", raising=False)
    candidate = request(
        {"Host": "localhost", "X-Forwarded-For": "203.0.113.10"},
        "127.0.0.1",
    )
    assert auth_web._setup_request_allowed(candidate) is False


def test_remote_setup_accepts_only_matching_bootstrap_token(monkeypatch):
    monkeypatch.setenv("OMBRE_SETUP_TOKEN", "bootstrap-secret")
    valid = request(
        {"Host": "garden.example", "X-Ombre-Setup-Token": "bootstrap-secret"},
        "203.0.113.10",
    )
    invalid = request(
        {"Host": "garden.example", "X-Ombre-Setup-Token": "wrong-token"},
        "203.0.113.10",
    )
    assert auth_web._setup_request_allowed(valid) is True
    assert auth_web._setup_request_allowed(invalid) is False


@pytest.mark.asyncio
async def test_remote_setup_is_rejected_before_request_body(monkeypatch):
    class NeverReadRequest:
        headers = Headers({"Host": "garden.example"})
        client = SimpleNamespace(host="203.0.113.10")

        async def json(self):
            pytest.fail("unauthorized setup must not parse a password body")

    monkeypatch.delenv("OMBRE_SETUP_TOKEN", raising=False)
    monkeypatch.setattr(auth_web.sh, "_is_setup_needed", lambda: True)
    mcp = FakeMCP()
    auth_web.register(mcp)

    response = await mcp.routes[("POST", "/auth/setup")](NeverReadRequest())

    assert response.status_code == 403
    assert "首次设置" in json.loads(response.body)["error"]


@pytest.mark.asyncio
async def test_concurrent_first_setup_has_one_winner(monkeypatch):
    configured = {"value": False}
    saved = []
    sessions = []
    ready = asyncio.Event()

    class LocalRequest:
        headers = Headers({"Host": "localhost"})
        client = SimpleNamespace(host="127.0.0.1")

        async def json(self):
            await ready.wait()
            return {"password": "safe-password"}

    monkeypatch.delenv("OMBRE_SETUP_TOKEN", raising=False)
    monkeypatch.setattr(auth_web.sh, "_is_setup_needed", lambda: not configured["value"])

    def save_password(password):
        saved.append(password)
        configured["value"] = True

    monkeypatch.setattr(auth_web.sh, "_save_password_hash", save_password)
    monkeypatch.setattr(
        auth_web.sh,
        "_create_session",
        lambda: sessions.append("session") or "session",
    )
    monkeypatch.setattr(auth_web.sh, "_set_session_cookie", lambda *_args: None)
    mcp = FakeMCP()
    auth_web.register(mcp)
    handler = mcp.routes[("POST", "/auth/setup")]
    tasks = [asyncio.create_task(handler(LocalRequest())) for _ in range(2)]
    ready.set()

    responses = await asyncio.gather(*tasks)

    assert sorted(response.status_code for response in responses) == [200, 400]
    assert saved == ["safe-password"]
    assert sessions == ["session"]
