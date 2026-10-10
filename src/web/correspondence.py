"""Authenticated, explicit-only Postcard and Bottle HTTP operations."""
from starlette.responses import JSONResponse

from correspondence import Correspondence
from . import _shared as sh


def store():
    return Correspondence(sh.config["buckets_dir"])


def _boolean(value):
    if value not in ("true", "false"):
        raise ValueError("unread_only must be true or false")
    return value == "true"


def register(mcp):
    @mcp.custom_route("/api/postcards", methods=["GET", "POST"])
    async def postcards(request):
        if error := sh._require_auth(request):
            return error
        try:
            if request.method == "GET":
                q = request.query_params
                result = store().read_postcards(int(q.get("limit", "100")), q.get("query", ""), q.get("date_from", ""), q.get("date_to", ""))
            else:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                result = store().write_postcard(body.get("title"), body.get("content"), body.get("date", ""), body.get("links"))
            return JSONResponse({"ok": True, **result})
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/postcards/migrate-letters", methods=["GET", "POST"])
    async def migrate(request):
        if error := sh._require_auth(request):
            return error
        try:
            apply = request.method == "POST"
            if apply:
                body = await request.json()
                if not isinstance(body, dict) or body.get("confirm") is not True:
                    raise ValueError("confirm=true required")
            result = await store().migrate_walk_letters(sh.bucket_mgr, apply=apply)
            return JSONResponse({"ok": True, **result})
        except (ValueError, OSError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)

    @mcp.custom_route("/api/bottles", methods=["GET", "POST"])
    async def bottles(request):
        if error := sh._require_auth(request):
            return error
        try:
            if request.method == "GET":
                q = request.query_params
                result = store().read_bottles(q.get("to", "anyone"), _boolean(q.get("unread_only", "false")), q.get("thread_id", ""), mark_read=False)
            else:
                body = await request.json()
                if not isinstance(body, dict):
                    raise ValueError("Expected a JSON object")
                result = store().write_bottle(body.get("author"), body.get("to", "anyone"), body.get("content"), body.get("reply_to", ""))
            return JSONResponse({"ok": True, **result})
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/bottles/read", methods=["POST"])
    async def read_bottles(request):
        if error := sh._require_auth(request):
            return error
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("Expected a JSON object")
            result = store().read_bottles(body.get("to", "anyone"), body.get("unread_only", True), body.get("thread_id", ""))
            return JSONResponse({"ok": True, **result})
        except (ValueError, TypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
