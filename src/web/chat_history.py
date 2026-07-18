"""Private Markdown chat-history library, deliberately outside memos and MCP."""
import json
import os
import re
import tempfile
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

_MAX_BYTES = 10 * 1024 * 1024


def _directory() -> str:
    path = os.path.join(str(sh.config["buckets_dir"]), ".chat_history")
    os.makedirs(path, exist_ok=True)
    return path


def _index_path() -> str:
    return os.path.join(_directory(), "index.json")


def _index() -> dict:
    try:
        with open(_index_path(), encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_index(value: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix="chat-index-", dir=_directory())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
        os.replace(temporary, _index_path())
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]+", "_", os.path.basename(value)).strip(" .")
    if not name.lower().endswith(".md"):
        raise ValueError("只接受 .md 文件")
    if not name or name == ".md":
        raise ValueError("文件名无效")
    return name[:160]


def _list() -> list[dict]:
    index = _index()
    rows = []
    for name, metadata in index.items():
        path = os.path.join(_directory(), name)
        if not os.path.isfile(path):
            continue
        meta = metadata if isinstance(metadata, dict) else {}
        rows.append({"file": name, "title": meta.get("title") or name[:-3], "description": meta.get("description") or "", "uploaded_at": meta.get("uploaded_at") or "", "size": os.path.getsize(path)})
    return sorted(rows, key=lambda row: row["uploaded_at"], reverse=True)


def register(mcp) -> None:
    from starlette.responses import JSONResponse

    @mcp.custom_route("/api/chat-history", methods=["GET"])
    async def chats(request: Request) -> Response:
        err = sh._require_auth(request)
        return err or JSONResponse({"ok": True, "documents": _list()})

    @mcp.custom_route("/api/chat-history", methods=["POST"])
    async def upload_chat(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("请选择 Markdown 文件")
            name = _safe_name(str(getattr(upload, "filename", "")))
            content = await upload.read()
            if not content or len(content) > _MAX_BYTES:
                raise ValueError("文件必须介于 1 B 与 10 MB 之间")
            content.decode("utf-8")
            with open(os.path.join(_directory(), name), "wb") as handle:
                handle.write(content)
            index = _index()
            index[name] = {"title": name[:-3], "description": "", "uploaded_at": datetime.now().isoformat(timespec="seconds")}
            _write_index(index)
            return JSONResponse({"ok": True, "documents": _list()})
        except (UnicodeDecodeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/chat-history/{name}", methods=["PATCH"])
    async def edit_chat(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            name = _safe_name(request.path_params["name"])
            if not os.path.isfile(os.path.join(_directory(), name)):
                raise ValueError("未找到文件")
            body = await request.json()
            index = _index()
            item = index.setdefault(name, {})
            for field, maximum in (("title", 160), ("description", 1000)):
                if field in body:
                    item[field] = str(body[field]).strip()[:maximum]
            _write_index(index)
            return JSONResponse({"ok": True, "documents": _list()})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/chat-history/{name}", methods=["DELETE"])
    async def delete_chat(request: Request) -> Response:
        """Permanently remove one uploaded Markdown document and its metadata."""
        err = sh._require_auth(request)
        if err:
            return err
        try:
            name = _safe_name(request.path_params["name"])
            path = os.path.join(_directory(), name)
            if not os.path.isfile(path):
                raise ValueError("未找到文件")
            os.unlink(path)
            index = _index()
            index.pop(name, None)
            _write_index(index)
            return JSONResponse({"ok": True})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except OSError:
            sh.logger.exception("chat-history delete failed")
            return JSONResponse({"ok": False, "error": "删除文件失败"}, status_code=500)
