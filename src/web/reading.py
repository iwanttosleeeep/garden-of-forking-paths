"""Authenticated Reading library routes, separate from Memos and Journal."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from reading_library import ReadingLibrary, ReadingLibraryError
from utils import clean_llm_json

from . import _shared as sh


def _library() -> ReadingLibrary:
    return ReadingLibrary(str(sh.config["buckets_dir"]))


def _error(exc: Exception, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"ok": False, "error": str(exc)}, status_code=status_code)


async def _analyze_chunk(book_id: str, chunk_id: int) -> dict:
    dehydrator = sh.dehydrator
    if dehydrator is None or not getattr(dehydrator, "api_available", False):
        raise ReadingLibraryError("DeepSeek／压缩 LLM 尚未配置，请先到设置 → 引擎填写 API")
    chunk = _library().get_chunk(book_id, chunk_id)
    system = (
        "你是共同阅读的导读员。只分析给出的当前阅读片段，不补写书中不存在的内容。"
        "返回严格 JSON：summary 为不超过 180 字的概述；themes 为最多 5 个短主题；"
        "questions 为最多 3 个适合两位读者讨论、没有标准答案的问题。"
    )
    user = json.dumps(
        {"book": chunk["book"]["title"], "section": chunk["title"], "text": chunk["text"]},
        ensure_ascii=False,
    )
    try:
        raw = await dehydrator._chat(system, user, max_tokens=900, temperature=0.2)
        parsed = json.loads(clean_llm_json(raw))
    except Exception as exc:
        sh.logger.exception("reading chunk analysis failed")
        raise ReadingLibraryError(f"DeepSeek 导读失败：{str(exc)[:180]}") from exc
    if not isinstance(parsed, dict):
        raise ReadingLibraryError("DeepSeek 没有返回可用的导读")
    return _library().store_analysis(book_id, chunk_id, parsed)


def register(mcp) -> None:
    @mcp.custom_route("/api/reading", methods=["GET"])
    async def list_books(request: Request) -> Response:
        err = sh._require_auth(request)
        return err or JSONResponse({"ok": True, "books": _library().list_books()})

    @mcp.custom_route("/api/reading", methods=["POST"])
    async def upload_book(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ReadingLibraryError("请选择 EPUB、TXT 或 Markdown 文件")
            data = await upload.read()
            library = _library()
            book = library.import_book(
                str(getattr(upload, "filename", "")),
                data,
                title=str(form.get("title") or ""),
                author=str(form.get("author") or ""),
            )
            return JSONResponse({"ok": True, "book": book, "books": library.list_books()})
        except ReadingLibraryError as exc:
            return _error(exc)
        except Exception:
            sh.logger.exception("reading upload failed")
            return _error(Exception("书籍导入失败"), 500)

    @mcp.custom_route("/api/reading/{book_id}", methods=["GET"])
    async def get_book(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            return JSONResponse({"ok": True, "book": _library().get_book(request.path_params["book_id"])})
        except ReadingLibraryError as exc:
            return _error(exc, 404)

    @mcp.custom_route("/api/reading/{book_id}", methods=["DELETE"])
    async def delete_book(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            _library().delete_book(request.path_params["book_id"])
            return JSONResponse({"ok": True})
        except ReadingLibraryError as exc:
            return _error(exc, 404)

    @mcp.custom_route("/api/reading/{book_id}/chunks/{chunk_id}", methods=["GET"])
    async def get_chunk(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            chunk = _library().get_chunk(request.path_params["book_id"], int(request.path_params["chunk_id"]))
            return JSONResponse({"ok": True, "chunk": chunk})
        except (ReadingLibraryError, ValueError) as exc:
            return _error(exc, 404)

    @mcp.custom_route("/api/reading/{book_id}/progress", methods=["PATCH"])
    async def save_progress(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
            book = _library().update_progress(
                request.path_params["book_id"],
                "human",
                int(body.get("chunk", 0)),
                float(body.get("offset", 0.0)),
            )
            return JSONResponse({"ok": True, "book": book})
        except (ReadingLibraryError, TypeError, ValueError) as exc:
            return _error(exc)

    @mcp.custom_route("/api/reading/{book_id}/annotations", methods=["POST"])
    async def add_annotation(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
            item = _library().add_annotation(
                request.path_params["book_id"],
                int(body.get("chunk", 0)),
                author="human",
                quote=str(body.get("quote") or ""),
                note=str(body.get("note") or ""),
                kind=str(body.get("kind") or "note"),
            )
            return JSONResponse({"ok": True, "annotation": item})
        except (ReadingLibraryError, TypeError, ValueError) as exc:
            return _error(exc)

    @mcp.custom_route("/api/reading/{book_id}/annotations/{annotation_id}", methods=["DELETE"])
    async def delete_annotation(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            _library().delete_annotation(request.path_params["book_id"], request.path_params["annotation_id"])
            return JSONResponse({"ok": True})
        except ReadingLibraryError as exc:
            return _error(exc, 404)

    @mcp.custom_route("/api/reading/{book_id}/chunks/{chunk_id}/analyze", methods=["POST"])
    async def analyze_chunk(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            analysis = await _analyze_chunk(request.path_params["book_id"], int(request.path_params["chunk_id"]))
            return JSONResponse({"ok": True, "analysis": analysis})
        except (ReadingLibraryError, ValueError) as exc:
            return _error(exc)

    @mcp.custom_route("/api/reading/{book_id}/export", methods=["GET"])
    async def export_notes(request: Request) -> Response:
        err = sh._require_auth(request)
        if err:
            return err
        try:
            book_id = request.path_params["book_id"]
            return PlainTextResponse(
                _library().export_markdown(book_id),
                media_type="text/markdown; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="garden-reading-{book_id}.md"'},
            )
        except ReadingLibraryError as exc:
            return _error(exc, 404)
