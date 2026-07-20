"""
========================================
web/dashboard.py — 仪表板页面 + 静态资源 + 健康检查
========================================

承载根路径仪表板、前端静态资源（icon/favicon/manifest/字体）、/favicon.ico 跳转、
以及 /health 健康检查。

对外暴露：register(mcp)。
========================================
"""

import os
import html as _html

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh


def register(mcp) -> None:

    async def serve_frontend_page(filename: str) -> Response:
        """Serve a frontend document without caching stale deployed HTML."""
        from starlette.responses import HTMLResponse
        path = os.path.join(sh.repo_root, "frontend", filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                page = f.read()
            for asset in ("/static/favicon-32.png", "/static/icon-180.png"):
                page = page.replace(asset, f"{asset}?v={sh.version}")
            return HTMLResponse(page, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
        except FileNotFoundError:
            return HTMLResponse(
                f"<h1>{_html.escape(filename)} not found</h1>"
                "<p>Update or rebuild Garden so its frontend files are present.</p>",
                status_code=404,
            )

    @mcp.custom_route("/", methods=["GET"])
    async def root_dashboard(request: Request) -> Response:
        """Serve the illustrated map front page at root."""
        return await serve_frontend_page("front-page.html")

    @mcp.custom_route("/garden", methods=["GET"])
    async def garden_dashboard(request: Request) -> Response:
        """Serve the authenticated Garden dashboard after the map entry."""
        return await serve_frontend_page("dashboard.html")

    # iter 1.7 §C/§H: serve frontend static assets (app icons / manifest)
    # 安全要点：必须白名单过滤文件名，绝不能让 request 直接拼路径，
    # 否则会被 ?name=../../etc/passwd 这种「目录穿越」攻击拿走任意文件。
    @mcp.custom_route("/static/{name}", methods=["GET"])
    async def static_asset(request: Request) -> Response:
        from starlette.responses import Response as _Resp, JSONResponse
        name = request.path_params.get("name", "")
        allowed = {
            "icon.svg": "image/svg+xml",
            "favicon.svg": "image/svg+xml",
            "favicon-32.png": "image/png",
            "icon-180.png": "image/png",
            "icon-192.png": "image/png",
            "icon-512.png": "image/png",
            "islands-user.png": "image/png",
            "manifest.json": "application/manifest+json",
        }
        if name not in allowed:
            return JSONResponse({"error": "not found"}, status_code=404)
        path = os.path.join(sh.repo_root, "frontend", name)
        try:
            with open(path, "rb") as f:
                return _Resp(f.read(), media_type=allowed[name])
        except FileNotFoundError:
            return JSONResponse({"error": "not found"}, status_code=404)

    # 浏览器打开任意页都会自动请求 /favicon.ico，301 永久重定向到 PNG 版本。
    @mcp.custom_route("/favicon.ico", methods=["GET"])
    async def favicon_redirect(request: Request) -> Response:
        from starlette.responses import RedirectResponse
        return RedirectResponse(url="/static/favicon-32.png", status_code=301)

    @mcp.custom_route("/health", methods=["GET"])
    async def health_check(request: Request) -> Response:
        from starlette.responses import JSONResponse
        try:
            stats = await sh.bucket_mgr.get_stats()
            return JSONResponse({
                "status": "ok",
                "buckets": stats["permanent_count"] + stats["dynamic_count"],
                "decay_engine": "running" if sh.decay_engine.is_running else "stopped",
            })
        except Exception as e:
            return JSONResponse({"status": "error", "detail": str(e)}, status_code=500)
