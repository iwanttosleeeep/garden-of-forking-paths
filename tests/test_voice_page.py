"""Only a static UI is added; no live data, model, or relay is needed."""
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from server_app import SecurityHeadersMiddleware
from web import dashboard


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(dashboard.sh, "repo_root", str(Path(__file__).resolve().parents[1]))
    monkeypatch.setattr(dashboard.sh, "version", "voice-test")
    routes = []

    class Routes:
        def custom_route(self, path, methods):
            def register(handler):
                routes.append(Route(path, handler, methods=methods))
                return handler
            return register

    dashboard.register(Routes())
    app = Starlette(routes=routes)
    app.add_middleware(SecurityHeadersMiddleware)
    with TestClient(app) as test_client:
        yield test_client


def test_voice_page_has_scoped_permissions_and_no_cache(client):
    response = client.get("/voice")
    assert response.status_code == 200
    assert "传声小屋" in response.text
    assert 'id="voice-workspace" disabled' in response.text
    assert "no-store" in response.headers["cache-control"]
    assert "microphone=(self)" in response.headers["permissions-policy"]
    assert "usb=()" in response.headers["permissions-policy"]
    assert "http://127.0.0.1:8765" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "microphone=()" in client.get("/garden").headers["permissions-policy"]


def test_voice_assets_are_whitelisted_versioned_and_available(client):
    page = client.get("/voice").text
    for name in ("voice.css", "voice-bridge.js", "voice-endpoint.js", "voice-app.js", "voice-delivery.js"):
        assert f"/static/{name}?v=voice-test" in page
        response = client.get(f"/static/{name}")
        assert response.status_code == 200
        assert "text/css" in response.headers["content-type"] if name.endswith(".css") else (
            "javascript" in response.headers["content-type"]
        )
    assert client.get("/static/server.py").status_code == 404


def test_voice_links_do_not_enter_dashboard_tab_switcher(client):
    assert 'href="/voice"' in client.get("/").text
    page = client.get("/garden").text
    assert 'href="/voice" class="voice-link"' in page
    assert 'data-tab="voice"' not in page


def test_map_voice_landmark_uses_marked_island_position_and_english_only(client):
    page = client.get("/").text
    landmark = page.split('href="/voice"', 1)[1].split("</a>", 1)[0]
    assert 'left:81.875%;top:27.1%' in landmark
    assert ">VOICE</div>" in landmark
    assert ">Signal Hut</div>" in landmark
    assert "传声小屋" not in landmark
    assert "M 720 268 C 870 236, 935 194, 1006 208 S 1122 223, 1179 225" in page
    assert "传声小屋" in client.get("/voice").text
    assert "传声小屋" in client.get("/garden").text
