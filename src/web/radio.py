"""Authenticated Garden Radio routes backed by NetEase's official CLI."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ncm_client import NCMClient, NCMClientError
from radio_library import RadioLibraryError
from radio_service import RadioService
from utils import atomic_update_config_yaml

from . import _shared as sh


def _client() -> NCMClient:
    return NCMClient()


def _service() -> RadioService:
    return RadioService(str(sh.config["buckets_dir"]), _client())


def _radio_config() -> dict:
    value = sh.config.get("radio") or {}
    return value if isinstance(value, dict) else {}


def _persist_marker(configured: bool, app_id_masked: str = "") -> None:
    """Persist only connection metadata; the official CLI owns the secrets."""
    marker = {
        "configured": bool(configured),
        "app_id_masked": str(app_id_masked or ""),
    }

    def _mutate(saved: dict) -> None:
        saved["radio"] = dict(marker)

    atomic_update_config_yaml(_mutate)
    sh.config["radio"] = dict(marker)


def _error(exc: Exception, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


async def _perform(body: dict) -> object:
    service = _service()
    action = str(body.get("action") or "playlists").strip().lower()
    if action == "playlists":
        return await service.playlists(str(body.get("view") or "human"))
    if action == "playlist":
        return await service.playlist(body.get("reference") or body.get("playlist_id"))
    if action == "set_exposed":
        exposed = body.get("exposed")
        if not isinstance(exposed, bool):
            raise RadioLibraryError("exposed 必须是 true 或 false")
        return service.set_exposed(body.get("reference"), exposed)
    if action == "search":
        return await service.search(body.get("query"), str(body.get("kind") or "all"))
    if action == "create_playlist":
        return await service.create_playlist(body.get("name"), owner="human")
    raise NCMClientError("未知的 Radio 操作")


def register(mcp) -> None:
    @mcp.custom_route("/api/radio/status", methods=["GET"])
    async def radio_status(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        marker = _radio_config()
        status = await _client().status(configured=bool(marker.get("configured")))
        status["app_id_masked"] = str(marker.get("app_id_masked") or "")
        return JSONResponse({"ok": True, **status})

    @mcp.custom_route("/api/radio/config", methods=["POST"])
    async def save_radio_config(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise NCMClientError("请求格式不正确")
            result = await _client().configure(body.get("app_id"), body.get("private_key"))
            _persist_marker(True, str(result.get("app_id_masked") or ""))
            return JSONResponse({"ok": True, **result})
        except (NCMClientError, RadioLibraryError) as exc:
            return _error(exc)
        except Exception:
            sh.logger.exception("radio config save failed")
            return _error(Exception("Radio 凭证保存失败"), 500)

    @mcp.custom_route("/api/radio/login", methods=["POST"])
    async def start_radio_login(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            return JSONResponse({"ok": True, "data": await _client().start_login()})
        except (NCMClientError, RadioLibraryError) as exc:
            return _error(exc)

    @mcp.custom_route("/api/radio", methods=["POST"])
    async def radio_action(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise NCMClientError("请求格式不正确")
            return JSONResponse({"ok": True, "action": body.get("action"), "data": await _perform(body)})
        except (NCMClientError, RadioLibraryError) as exc:
            return _error(exc)
        except Exception:
            sh.logger.exception("radio action failed")
            return _error(Exception("网易云音乐请求失败"), 502)
