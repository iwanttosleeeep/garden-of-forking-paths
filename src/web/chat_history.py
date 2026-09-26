"""Private Markdown chat-history library, read explicitly through recall."""
import hashlib
import json
import os
import re
from datetime import datetime

from starlette.requests import Request
from starlette.responses import Response
from utils import atomic_write_text

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
    atomic_write_text(_index_path(), json.dumps(value, ensure_ascii=False, indent=2))


def _safe_name(value: str) -> str:
    name = re.sub(r"[^\w. -]+", "_", os.path.basename(value.replace("\\", "/"))).strip(" .")
    if not name.lower().endswith(".md"):
        raise ValueError("只接受 .md 文件")
    if not name or name == ".md":
        raise ValueError("文件名无效")
    # Keep the extension and distinguish long names sharing the same prefix.
    # Bound UTF-8 bytes, not characters, for portable filesystem limits.
    if len(name.encode("utf-8")) > 160:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:12]
        stem = name[:-3].encode("utf-8")[:144].decode("utf-8", errors="ignore")
        name = f"{stem}-{digest}{name[-3:]}"
    return name


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
            content = await upload.read(_MAX_BYTES + 1)
            if not content or len(content) > _MAX_BYTES:
                raise ValueError("文件必须介于 1 B 与 10 MB 之间")
            text = content.decode("utf-8")
            atomic_write_text(os.path.join(_directory(), name), text)
            index = _index()
            previous = index.get(name)
            metadata = previous if isinstance(previous, dict) else {}
            index[name] = {"title": metadata.get("title", name[:-3]), "description": metadata.get("description", ""), "uploaded_at": datetime.now().isoformat(timespec="seconds")}
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
            if not isinstance(body, dict):
                raise ValueError("请求格式必须是对象")
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
