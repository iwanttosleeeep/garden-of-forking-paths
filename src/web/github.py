"""
========================================
web/github.py — GitHub 同步配置与触发
========================================

把所有 bucket .md 备份到 GitHub 仓库。状态/保存配置/验证/立即同步四个路由。

状态共享：github 实例存在 sh.github_sync_instance（server.py 的后台定时同步循环
_github_sync_loop / _restart_github_auto_task 也读 sh.github_sync_instance，
保证这里改了实例后台循环立刻看到）。后台任务起停走 sh.restart_github_auto_task。

对外暴露：register(mcp)。
========================================
"""

import os
import time
import zipfile

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

logger = sh.logger

try:
    from github_sync import GitHubSync  # type: ignore
    from utils import atomic_update_config_yaml, parse_bool  # type: ignore
except ImportError:  # pragma: no cover
    from ..github_sync import GitHubSync  # type: ignore
    from ..utils import atomic_update_config_yaml, parse_bool  # type: ignore


def _pre_import_backup(buckets_dir: str) -> str:
    """导入前把当前所有 .md 打成 zip 存到 <buckets_dir>/.import_backups/。
    返回 zip 路径（失败返回 "" —— 备份失败不应阻断恢复，但会在结果里如实标注）。"""
    try:
        bdir = os.path.join(buckets_dir, ".import_backups")
        os.makedirs(bdir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        zpath = os.path.join(bdir, f"pre_import_{ts}.zip")
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for root, _, files in os.walk(buckets_dir):
                if os.path.basename(root) == ".import_backups":
                    continue
                for fn in files:
                    if fn.endswith(".md"):
                        full = os.path.join(root, fn)
                        z.write(full, os.path.relpath(full, buckets_dir))
        return zpath
    except Exception as e:
        logger.warning(f"[github] pre-import backup failed: {e}")
        return ""


def register(mcp) -> None:

    @mcp.custom_route("/api/github/status", methods=["GET"])
    async def api_github_status(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        _gh_cfg_now = sh.config.get("github_sync", {}) or {}
        _auto_min = int(_gh_cfg_now.get("auto_interval_minutes") or 0)
        if sh.github_sync_instance is None:
            return JSONResponse({
                "ok": True,
                "configured": False,
                "repo": _gh_cfg_now.get("repo", ""),
                "branch": _gh_cfg_now.get("branch", "main"),
                "path_prefix": _gh_cfg_now.get("path_prefix", "ombre"),
                "token_set": bool(os.environ.get("OMBRE_GITHUB_TOKEN") or _gh_cfg_now.get("token")),
                "auto_interval_minutes": _auto_min,
            })
        return JSONResponse({"ok": True, "configured": True, "auto_interval_minutes": _auto_min, **sh.github_sync_instance.status()})

    @mcp.custom_route("/api/github/config", methods=["POST"])
    async def api_github_config(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "无效 JSON"}, status_code=400)

        token = str(body.get("token") or "").strip()
        repo = str(body.get("repo") or "").strip()
        branch = str(body.get("branch") or "main").strip() or "main"
        path_prefix = str(body.get("path_prefix") or "ombre").strip()
        auto_interval = int(body.get("auto_interval_minutes") or 0)

        if not token and not repo:
            # 清空配置
            cleared = {
                "repo": "",
                "branch": branch,
                "path_prefix": path_prefix,
                "auto_interval_minutes": 0,
            }
            try:
                atomic_update_config_yaml(
                    lambda saved: saved.__setitem__("github_sync", dict(cleared))
                )
            except Exception as exc:
                return JSONResponse(
                    {"ok": False, "error": f"config.yaml 写入失败: {exc}"},
                    status_code=500,
                )
            sh.config["github_sync"] = dict(cleared)
            sh.github_sync_instance = None
            sh.restart_github_auto_task(0)
            return JSONResponse({"ok": True, "message": "已清空 GitHub 同步配置"})

        # 持久化到 config.yaml（含 token，config.yaml 是 bind mount 重启不丢）
        gh_cfg = dict(sh.config.get("github_sync") or {})
        if token:
            gh_cfg["token"] = token
        gh_cfg["repo"] = repo
        gh_cfg["branch"] = branch
        gh_cfg["path_prefix"] = path_prefix
        gh_cfg["auto_interval_minutes"] = auto_interval
        try:
            atomic_update_config_yaml(
                lambda saved: saved.__setitem__("github_sync", dict(gh_cfg))
            )
        except Exception as exc:
            return JSONResponse(
                {"ok": False, "error": f"config.yaml 写入失败: {exc}"},
                status_code=500,
            )
        sh.config["github_sync"] = gh_cfg

        # 重建实例
        _tok = token or sh.config.get("github_sync", {}).get("token") or os.environ.get("OMBRE_GITHUB_TOKEN", "")
        sh.github_sync_instance = GitHubSync(token=_tok, repo=repo, branch=branch, path_prefix=path_prefix)
        # 重启定时任务
        sh.restart_github_auto_task(auto_interval)
        return JSONResponse({"ok": True, "message": "配置已保存"})

    @mcp.custom_route("/api/github/validate", methods=["POST"])
    async def api_github_validate(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        if sh.github_sync_instance is None:
            return JSONResponse({"ok": False, "error": "尚未配置 GitHub 同步"}, status_code=400)
        result = await sh.github_sync_instance.validate()
        return JSONResponse(result)

    @mcp.custom_route("/api/github/sync", methods=["POST"])
    async def api_github_sync(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        if sh.github_sync_instance is None:
            return JSONResponse({"ok": False, "error": "尚未配置 GitHub 同步，请先填写配置并保存"}, status_code=400)
        buckets_dir = sh.config.get("buckets_dir", "")
        if not buckets_dir:
            return JSONResponse({"ok": False, "error": "buckets_dir 未配置"}, status_code=500)
        result = await sh.github_sync_instance.sync(buckets_dir)
        return JSONResponse(result)

    @mcp.custom_route("/api/github/import", methods=["POST"])
    async def api_github_import(request: Request) -> Response:
        """从 GitHub 拉回记忆（恢复 / 回滚）。⚠️ 会覆盖本地同名记忆。

        合并覆盖语义 + 导入前自动 zip 备份本地（可退回）。导入后建议跑 backfill 重建
        向量（前端会自动接着调 /api/embedding/backfill）。embeddings.db 不在仓库里。
        """
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        if sh.github_sync_instance is None:
            return JSONResponse({"ok": False, "error": "尚未配置 GitHub 同步，请先填写配置并保存"}, status_code=400)
        buckets_dir = sh.config.get("buckets_dir", "")
        if not buckets_dir:
            return JSONResponse({"ok": False, "error": "buckets_dir 未配置"}, status_code=500)
        try:
            body = await request.json()
        except Exception:
            body = {}
        try:
            force = parse_bool(body.get("force", False))
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        # 1) 导入前自动备份本地（合并覆盖会改动本地，留个后悔药）
        backup = _pre_import_backup(buckets_dir)
        # 记忆安全闸门：备份没成功就默认不动本地记忆——覆盖不可逆，宁可拦下。
        # 用户确认愿意冒险（force=true）才放行，并如实标注这次没有后悔药。
        if not backup and not force:
            return JSONResponse({
                "ok": False,
                "error": "导入前的本地备份没有成功，为避免覆盖后无法找回记忆，已取消本次导入。"
                         "请检查数据目录是否可写、磁盘是否有空间后重试；确要强制导入可带 force=true。",
                "backup_failed": True,
            }, status_code=409)
        # 2) 从 GitHub 拉回
        result = await sh.github_sync_instance.import_from_github(buckets_dir)
        result["pre_import_backup"] = backup
        # 3) 让 bucket_mgr 的 BM25 索引失效（导入直写磁盘，绕过了 bucket_mgr 的脏标记）
        try:
            if sh.bucket_mgr is not None:
                sh.bucket_mgr._invalidate_bm25()
        except Exception:
            pass
        return JSONResponse(result)
