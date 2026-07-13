"""
========================================
web/import_api.py — 宿主机 vault 设置 / 桶编辑 / 导出
========================================

- /api/host-vault：读写 docker-compose 挂载的宿主机记忆目录（写 .env）
- /api/bucket/{id}/edit：编辑桶正文（带内容体积校验）
- /api/export：导出全部记忆 zip

对外暴露：register(mcp)。
========================================
"""

import os
import time
import asyncio
from datetime import datetime as _dt

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

try:
    from utils import parse_bool  # type: ignore
except ImportError:  # pragma: no cover
    from ..utils import parse_bool  # type: ignore

try:
    from backup_archive import MAX_ARCHIVE_BYTES, BackupArchiveError, build_export_archive  # type: ignore
except ImportError:  # pragma: no cover
    from ..backup_archive import MAX_ARCHIVE_BYTES, BackupArchiveError, build_export_archive  # type: ignore

logger = sh.logger

try:
    from tools._common import (  # type: ignore
        check_content_size as _check_content_size,
        check_pinned_quota as _check_pinned_quota,
    )
except ImportError:  # pragma: no cover
    from ..tools._common import (  # type: ignore
        check_content_size as _check_content_size,
        check_pinned_quota as _check_pinned_quota,
    )














def register(mcp) -> None:

    @mcp.custom_route("/api/host-vault", methods=["GET"])
    async def api_host_vault_get(request: Request) -> Response:
        """Read the host-side vault path without pretending a container can change its mount."""
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        compose_managed = sh.in_docker()
        if compose_managed:
            # A container-local .env cannot affect the host-side volume source used
            # before this container starts. Only report the value Compose injected.
            value = os.environ.get("OMBRE_HOST_VAULT_DIR", "").strip()
            source = "env" if value else ""
            env_file = None
        else:
            value = sh._read_env_var("OMBRE_HOST_VAULT_DIR")
            source = "env" if os.environ.get("OMBRE_HOST_VAULT_DIR", "").strip() else ("file" if value else "")
            env_file = sh._project_env_path()
        return JSONResponse({
            "value": value,
            "source": source,
            "env_file": env_file,
            "compose_managed": compose_managed,
            "message": (
                "该挂载由宿主机 Compose 管理。请在 compose 文件旁的 .env 设置 "
                "OMBRE_HOST_VAULT_DIR，然后执行 docker compose up -d --force-recreate。"
                if compose_managed else ""
            ),
        })


    @mcp.custom_route("/api/host-vault", methods=["POST"])
    async def api_host_vault_set(request: Request) -> Response:
        """
        Persist OMBRE_HOST_VAULT_DIR for non-container deployments.
        Body: {"value": "/path/to/vault"}  (empty string clears the entry)

        Docker mounts are resolved by Compose before the container starts. Writing
        /app/src/.env from inside that container cannot change the host mount, so
        Docker callers receive an explicit host-managed response instead.
        """
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        if sh.in_docker():
            return JSONResponse({
                "error": (
                    "容器无法修改宿主机的 Compose 挂载。请在 compose 文件旁的 .env 设置 "
                    "OMBRE_HOST_VAULT_DIR，然后执行 docker compose up -d --force-recreate。"
                ),
                "compose_managed": True,
                "restart_required": True,
                "env_var": "OMBRE_HOST_VAULT_DIR",
            }, status_code=409)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        raw = body.get("value", "")
        if not isinstance(raw, str):
            return JSONResponse({"error": "value must be a string"}, status_code=400)
        value = raw.strip()

        # Reject characters that would break .env / shell parsing
        if "\n" in value or "\r" in value or '"' in value or "'" in value:
            return JSONResponse({"error": "value must not contain quotes or newlines"}, status_code=400)

        try:
            sh._write_env_var("OMBRE_HOST_VAULT_DIR", value)
        except Exception as e:
            return JSONResponse({"error": f"failed to write .env: {e}"}, status_code=500)

        return JSONResponse({
            "ok": True,
            "value": value,
            "env_file": sh._project_env_path(),
            "restart_required": True,
            "message": "已保存 OMBRE_HOST_VAULT_DIR；需要重启容器/服务后挂载才会生效。",
            "note": "已写入 .env；需在宿主机执行 `docker compose down && docker compose up -d` 让新挂载生效。",
        })


    # =============================================================
    # Import API — conversation history import
    # 导入 API — 对话历史导入
    # =============================================================

    @mcp.custom_route("/api/bucket/{bucket_id}/edit", methods=["PATCH", "POST"])
    async def api_bucket_edit(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        bucket_id = request.path_params["bucket_id"]
        bucket = await sh.bucket_mgr.get(bucket_id)
        if not bucket:
            return JSONResponse({"error": "bucket not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "invalid JSON"}, status_code=400)

        updates: dict = {}

        # --- 字符串型 ---
        if isinstance(body.get("name"), str):
            nm = body["name"].strip()[:120]
            if nm:
                updates["name"] = nm

        if isinstance(body.get("tags"), list):
            # 接受 ["a","b"]
            tags = [str(t).strip() for t in body["tags"] if str(t).strip()]
            updates["tags"] = tags
        elif isinstance(body.get("tags"), str):
            # 也接受 "a, b"
            tags = [t.strip() for t in body["tags"].split(",") if t.strip()]
            updates["tags"] = tags

        if isinstance(body.get("domain"), list):
            doms = [str(d).strip() for d in body["domain"] if str(d).strip()]
            updates["domain"] = doms
        elif isinstance(body.get("domain"), str) and body["domain"].strip():
            updates["domain"] = [d.strip() for d in body["domain"].split(",") if d.strip()]

        # --- 数值/布尔型 ---
        if "importance" in body:
            try:
                imp = int(body["importance"])
                if 1 <= imp <= 10:
                    updates["importance"] = imp
            except (TypeError, ValueError):
                pass

        for flag in ("resolved", "digested"):
            if flag in body:
                try:
                    updates[flag] = parse_bool(body[flag])
                except ValueError as e:
                    return JSONResponse({"error": str(e)}, status_code=400)

        # pinned 需要走配额检查
        if "pinned" in body:
            try:
                new_pinned = parse_bool(body["pinned"])
            except ValueError as e:
                return JSONResponse({"error": str(e)}, status_code=400)
            cur_pinned = bool(bucket["metadata"].get("pinned", False))
            if new_pinned and not cur_pinned:
                quota_err = await _check_pinned_quota()
                if quota_err:
                    return JSONResponse({"error": quota_err}, status_code=400)
                updates["pinned"] = True
                updates["importance"] = 10
                updates["type"] = "permanent"
            elif (not new_pinned) and cur_pinned:
                updates["pinned"] = False
                if bucket["metadata"].get("type") == "permanent":
                    updates["type"] = "dynamic"

        # content 替换 —— 走 §5 大小校验
        new_content = body.get("content")
        if isinstance(new_content, str) and new_content.strip() and new_content != bucket.get("content", ""):
            size_err = _check_content_size(new_content)
            if size_err:
                return JSONResponse({"error": size_err}, status_code=400)
            updates["content"] = new_content

        # type 字段直接改（不经 pinned 联动，调用方自己负责一致性）
        _valid_types = {"dynamic", "permanent", "feel", "plan", "letter", "i"}
        if isinstance(body.get("type"), str) and body["type"] in _valid_types:
            if body["type"] != bucket["metadata"].get("type"):
                updates["type"] = body["type"]

        if not updates:
            return JSONResponse({"error": "nothing to update"}, status_code=400)

        try:
            ok = await sh.bucket_mgr.update(bucket_id, **updates)
            if not ok:
                return JSONResponse({"error": "update failed"}, status_code=500)
            if "content" in updates:
                try:
                    sh.dehydrator.invalidate_cache(bucket["content"])
                except Exception:
                    pass
            return JSONResponse({"ok": True, "id": bucket_id, "updated": list(updates.keys())})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)


    # =============================================================
    # /api/export  — 完整记忆打包导出
    # 导出内容：所有 bucket markdown + SQLite 一致性快照 + meta + SHA-256 清单
    # 不导出 config（避免 api_key 等密钥泄露）
    # export_meta.json 中的 embedding 字段供导入端检查模型一致性。
    # =============================================================
    @mcp.custom_route("/api/export", methods=["GET"])
    async def api_export(request: Request) -> Response:
        from starlette.responses import StreamingResponse, JSONResponse
        err = sh._require_auth(request)
        if err:
            return err

        buckets_dir = sh.config.get("buckets_dir", "")
        if not buckets_dir or not os.path.isdir(buckets_dir):
            return JSONResponse({"error": f"buckets_dir not found: {buckets_dir}"}, status_code=500)

        try:
            emb_backend = getattr(sh.embedding_engine, "_backend", None)
            try:
                emb_dim = int(emb_backend.vector_dim()) if emb_backend else 0
            except Exception:
                emb_dim = 0
            meta: dict = {
                "exported_at": _dt.now().isoformat(timespec="seconds"),
                "version": sh.version,
                "embedding": {
                    "model": str(getattr(sh.embedding_engine, "model", "") or ""),
                    "dim": emb_dim,
                    "backend": str(getattr(sh.embedding_engine, "backend", "") or ""),
                },
            }
            try:
                meta["stats"] = await sh.bucket_mgr.get_stats()
            except Exception as exc:
                logger.warning("export: stats unavailable: %s", exc)

            emb_path = str(getattr(sh.embedding_engine, "db_path", "") or "")
            payload, manifest = await asyncio.to_thread(
                build_export_archive,
                buckets_dir,
                emb_path,
                meta,
            )
        except BackupArchiveError as e:
            return JSONResponse({"error": f"export failed: {e}"}, status_code=500)
        except Exception as e:
            logger.error("export failed", exc_info=True)
            return JSONResponse({"error": f"export failed: {e}"}, status_code=500)

        fname = f"ombre_export_{int(time.time())}.zip"
        return StreamingResponse(
            iter([payload]),
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{fname}"',
                "X-Ombre-Backup-Verified": "true",
                "X-Ombre-Backup-Files": str(manifest["file_count"]),
            },
        )


    # =============================================================
    # /api/migrate/* — 完整记忆包（zip）导入
    # 流程：POST /upload → GET /status（含冲突列表） → POST /apply（带决策）→ 轮询 GET /status
    # =============================================================

