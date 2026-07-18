"""Sterling journal import and mood-summary endpoints.

The Garden deliberately receives an exported JSON file instead of attempting to
reach into Sterling's browser storage.  Imported entries stay regular Markdown
memos, marked ``dont_surface``, so they are available to the dedicated journal
tool without becoming unsolicited conversational context.
"""

import base64
import hashlib
import json
import os
import secrets
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
import yaml

from starlette.requests import Request
from starlette.responses import Response

from . import _shared as sh

_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_MAX_ENTRIES = 500
_GITHUB_API = "https://api.github.com"


def _entries_from_export(payload: Any) -> list[dict[str, Any]]:
    """Accept Sterling's current export shape and its vault-only variant."""
    if not isinstance(payload, dict):
        raise ValueError("Sterling 导出文件必须是 JSON 对象")
    vault = payload.get("vault") if isinstance(payload.get("vault"), dict) else payload
    entries = vault.get("entries") if isinstance(vault, dict) else None
    if not isinstance(entries, list):
        raise ValueError("没有找到 Sterling entries；请从 Sterling 的导出功能生成 JSON")
    if len(entries) > _MAX_ENTRIES:
        raise ValueError(f"一次最多导入 {_MAX_ENTRIES} 条日记")
    return [entry for entry in entries if isinstance(entry, dict)]


def _journal_date(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        raw = str(value)
    else:
        raw = str(value or "").strip()
    if not raw:
        return ""
    # Sterling's PWA stores timestamp as Unix milliseconds.  Treating that
    # number as an ISO string previously produced a blank/invalid journal date.
    try:
        numeric = float(raw)
        if numeric >= 1_000_000_000:
            seconds = numeric / 1000 if numeric >= 100_000_000_000 else numeric
            timezone = str(sh.config.get("timezone") or "UTC")
            try:
                zone = ZoneInfo(timezone)
            except ZoneInfoNotFoundError:
                zone = ZoneInfo("UTC")
            return datetime.fromtimestamp(seconds, tz=zone).date().isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return raw[:10]


def _mood(value: Any) -> int | None:
    try:
        mood = int(value)
    except (TypeError, ValueError):
        return None
    return mood if 1 <= mood <= 5 else None


def _entry_content(entry: dict[str, Any], date: str, mood: int | None) -> str:
    note = str(entry.get("note") or "").strip()
    echo = str(entry.get("lyrics") or entry.get("echo") or "").strip()
    stamp = date or "未标注日期"
    mood_text = f"心情 {mood}/5" if mood is not None else "未记录心情"
    parts = [f"Sterling 日记 · {stamp} · {mood_text}"]
    if note:
        parts.append(note)
    if echo:
        parts.append(f"Echo: {echo}")
    # A mood-only record is still useful for the curve.
    return "\n\n".join(parts)


def _summary_from_buckets(buckets: list[dict[str, Any]]) -> dict[str, Any]:
    journals = [
        bucket for bucket in buckets
        if sh.is_sterling_journal(bucket)
        and not (bucket.get("metadata") or {}).get("deleted_at")
    ]
    days: dict[str, list[int]] = defaultdict(list)
    for bucket in journals:
        meta = bucket.get("metadata") or {}
        date = str(meta.get("journal_date") or meta.get("created") or "")[:10]
        mood = _mood(meta.get("journal_mood"))
        if date and mood is not None:
            days[date].append(mood)
    curve = [
        {"date": date, "mood": round(sum(values) / len(values), 2), "count": len(values)}
        for date, values in sorted(days.items())[-30:]
    ]
    all_moods = [point["mood"] for point in curve]
    return {
        "count": len(journals),
        "mood_count": sum(point["count"] for point in curve),
        "average_mood": round(sum(all_moods) / len(all_moods), 2) if all_moods else None,
        "curve": curve,
    }


async def _import_entries(entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Import entries exactly once; shared by file upload and sync endpoints."""
    existing = await sh.bucket_mgr.list_all(include_archive=True)
    existing_by_source_id = {
        str((bucket.get("metadata") or {}).get("journal_source_id")): bucket
        for bucket in existing
        if sh.is_sterling_journal(bucket)
    }
    imported = skipped = refreshed = 0
    for entry in entries:
        source_id = str(entry.get("id") or "").strip()
        if not source_id:
            skipped += 1
            continue
        date = _journal_date(entry.get("timestamp") or entry.get("createdAt"))
        mood = _mood(entry.get("mood"))
        original_tags = entry.get("tags") if isinstance(entry.get("tags"), list) else []
        tags = ["__journal__", "source:sterling"]
        if mood is not None:
            tags.append(f"mood:{mood}")
        tags.extend(str(tag).strip()[:80] for tag in original_tags if str(tag).strip())
        existing_bucket = existing_by_source_id.get(source_id)
        if existing_bucket:
            # Re-import is a safe repair path for older Garden versions: it
            # fills Unix-millisecond dates and changes the old 回声 label to Echo.
            await sh.bucket_mgr.update(
                existing_bucket["id"], content=_entry_content(entry, date, mood), tags=tags,
                name=f"Sterling {date}" if date else "Sterling 日记", journal_date=date,
                journal_mood=mood,
            )
            refreshed += 1
            continue
        bucket_id = await sh.bucket_mgr.create(
            content=_entry_content(entry, date, mood), tags=tags, importance=3,
            domain=["日记"], valence=((mood - 1) / 4) if mood is not None else 0.5,
            arousal=0.3, bucket_type="journal",
            name=f"Sterling {date}" if date else "Sterling 日记", source_tool="sterling",
        )
        await sh.bucket_mgr.update(
            bucket_id, dont_surface=True, journal_date=date,
            journal_source_id=source_id, journal_mood=mood,
        )
        existing_by_source_id[source_id] = {"id": bucket_id, "metadata": {"journal_source_id": source_id}}
        imported += 1
    buckets = await sh.bucket_mgr.list_all(include_archive=False)
    return {"ok": True, "imported": imported, "refreshed": refreshed, "skipped": skipped, **_summary_from_buckets(buckets)}


def _sync_config() -> dict[str, Any]:
    return sh.config.setdefault("sterling_sync", {})


def _save_sync_config() -> None:
    """Persist non-secret sync configuration; the key itself is stored only as a hash."""
    try:
        from utils import config_file_path  # type: ignore
    except ImportError:  # pragma: no cover
        from ..utils import config_file_path  # type: ignore
    path = config_file_path()
    saved: dict[str, Any] = {}
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as handle:
            saved = yaml.safe_load(handle) or {}
    saved["sterling_sync"] = dict(_sync_config())
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(saved, handle, allow_unicode=True, default_flow_style=False)


def _sync_status() -> dict[str, Any]:
    cfg = _sync_config()
    return {
        "configured": bool(cfg.get("repo") and cfg.get("token_hash")),
        "repo": cfg.get("repo", ""),
        "branch": cfg.get("branch", "main"),
        "path": cfg.get("path", "sterling-journal.json"),
        "allowed_origin": cfg.get("allowed_origin", ""),
        "key_set": bool(cfg.get("token_hash")),
        "github_token_set": bool(os.environ.get("OMBRE_STERLING_GITHUB_TOKEN") or cfg.get("github_token")),
    }


def _cors_headers(request: Request) -> dict[str, str]:
    origin = request.headers.get("origin", "")
    allowed = str(_sync_config().get("allowed_origin") or "").rstrip("/")
    if origin.rstrip("/") and origin.rstrip("/") == allowed:
        return {
            "Access-Control-Allow-Origin": origin,
            "Access-Control-Allow-Headers": "Authorization, Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Vary": "Origin",
        }
    return {}


def _sync_token_ok(request: Request) -> bool:
    expected = str(_sync_config().get("token_hash") or "")
    presented = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    digest = hashlib.sha256(presented.encode("utf-8")).hexdigest()
    return bool(expected and presented and secrets.compare_digest(expected, digest))


def _github_token() -> str:
    """Sterling sync never borrows the Garden backup credential."""
    return str(os.environ.get("OMBRE_STERLING_GITHUB_TOKEN") or _sync_config().get("github_token") or "").strip()


def _github_token_source() -> str:
    return "env:OMBRE_STERLING_GITHUB_TOKEN" if os.environ.get("OMBRE_STERLING_GITHUB_TOKEN") else "config:sterling_sync.github_token"


def _github_headers(token: str) -> dict[str, str]:
    """Fine-grained PATs use the Bearer scheme shown in GitHub's REST docs."""
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _validate_github_journal_access() -> dict[str, Any]:
    cfg = _sync_config()
    token = _github_token()
    repo = str(cfg.get("repo") or "").strip()
    if not token or not repo:
        raise ValueError("请先完成 GitHub Token 与日记仓库配置")
    async with httpx.AsyncClient(headers=_github_headers(token), timeout=20.0) as client:
        who = await client.get(f"{_GITHUB_API}/user")
        repo_check = await client.get(f"{_GITHUB_API}/repos/{repo}")
    identity = ""
    if who.status_code == 200:
        identity = str(who.json().get("login") or "")
    return {
        "authenticated": who.status_code == 200,
        "authenticated_as": identity,
        "repo_access": repo_check.status_code == 200,
        "repo_status": repo_check.status_code,
        "token_source": _github_token_source(),
        "repo": repo,
    }


async def _put_github_json(payload: dict[str, Any]) -> None:
    cfg = _sync_config()
    token = _github_token()
    if not token:
        raise ValueError("请先在 Sterling 日记同步配置中设置专用 GitHub Token")
    repo = str(cfg.get("repo") or "").strip()
    branch = str(cfg.get("branch") or "main").strip()
    path = str(cfg.get("path") or "sterling-journal.json").strip().strip("/")
    if not repo or "/" not in repo:
        raise ValueError("Sterling 同步仓库未配置；请在 Garden 设置中填写 owner/repo 并保存")
    if not path or ".." in path:
        raise ValueError("Sterling 同步文件路径无效")
    headers = _github_headers(token)
    url = f"{_GITHUB_API}/repos/{repo}/contents/{path}"
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        repo_check = await client.get(f"{_GITHUB_API}/repos/{repo}")
        if repo_check.status_code == 404:
            raise ValueError(
                "Garden 的 GitHub Token 无权访问日记仓库。请在 Fine-grained Token 的 "
                "Repository access 中加入 iwanttosleeeep/garden-journal-sync，并授予 Contents: Read and write"
            )
        repo_check.raise_for_status()
        current = await client.get(url, params={"ref": branch})
        sha = current.json().get("sha") if current.status_code == 200 else None
        if current.status_code not in (200, 404):
            current.raise_for_status()
        raw = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        body: dict[str, Any] = {
            "message": "Sterling journal sync", "branch": branch,
            "content": base64.b64encode(raw).decode("ascii"),
        }
        if sha:
            body["sha"] = sha
        result = await client.put(url, json=body)
        if result.status_code == 404:
            raise ValueError(f"日记仓库缺少分支 {branch}；请在 GitHub 创建 README 并确认默认分支名称")
        if result.status_code == 409:
            raise ValueError("日记仓库还是空的；请在 GitHub 为它创建 README 后再同步一次")
        result.raise_for_status()


async def _get_github_json() -> dict[str, Any]:
    cfg = _sync_config()
    token = _github_token()
    repo, branch = str(cfg.get("repo") or "").strip(), str(cfg.get("branch") or "main").strip()
    path = str(cfg.get("path") or "sterling-journal.json").strip().strip("/")
    if not token or not repo:
        raise ValueError("请先完成 Sterling 同步与 GitHub Token 配置")
    headers = _github_headers(token)
    async with httpx.AsyncClient(headers=headers, timeout=30.0) as client:
        response = await client.get(f"{_GITHUB_API}/repos/{repo}/contents/{path}", params={"ref": branch})
        if response.status_code == 404:
            raise ValueError("仓库里还没有 Sterling 日记文件")
        response.raise_for_status()
        return json.loads(base64.b64decode(response.json()["content"]).decode("utf-8"))


def register(mcp) -> None:
    @mcp.custom_route("/api/journal/entries", methods=["GET"])
    async def api_journal_entries(request: Request) -> Response:
        """Return imported diary records only; never mix them into normal memo lists."""
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            buckets = await sh.bucket_mgr.list_all(include_archive=False)
            entries = []
            for bucket in buckets:
                meta = bucket.get("metadata") or {}
                if not sh.is_sterling_journal(bucket) or meta.get("deleted_at"):
                    continue
                entries.append({
                    "id": bucket.get("id", ""),
                    "date": str(meta.get("journal_date") or meta.get("created") or "")[:10],
                    "mood": _mood(meta.get("journal_mood")),
                    "tags": [tag for tag in (meta.get("tags") or []) if not str(tag).startswith(("__", "source:", "mood:"))],
                    "content": bucket.get("content", ""),
                })
            entries.sort(key=lambda item: (item["date"], item["id"]), reverse=True)
            return JSONResponse({"ok": True, "entries": entries[:200]})
        except Exception as exc:
            sh.logger.exception("journal entries failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @mcp.custom_route("/api/journal/summary", methods=["GET"])
    async def api_journal_summary(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            buckets = await sh.bucket_mgr.list_all(include_archive=False)
            return JSONResponse({"ok": True, **_summary_from_buckets(buckets)})
        except Exception as exc:
            sh.logger.exception("journal summary failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @mcp.custom_route("/api/journal/entries/{bucket_id}", methods=["DELETE"])
    async def api_journal_delete(request: Request) -> Response:
        """Erase one Sterling entry from Garden without archiving it."""
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        bucket_id = request.path_params["bucket_id"]
        bucket = await sh.bucket_mgr.get(bucket_id)
        if not bucket or not sh.is_sterling_journal(bucket):
            return JSONResponse({"ok": False, "error": "未找到 Sterling 日记"}, status_code=404)
        if not await sh.bucket_mgr.erase(bucket_id):
            return JSONResponse({"ok": False, "error": "删除失败"}, status_code=500)
        return JSONResponse({"ok": True})

    @mcp.custom_route("/api/journal/sterling", methods=["POST"])
    async def api_import_sterling(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is None or not hasattr(upload, "read"):
                raise ValueError("请选择 Sterling 导出的 .json 文件")
            raw = await upload.read()
            if len(raw) > _MAX_UPLOAD_BYTES:
                raise ValueError("日记导出文件不能超过 2 MB")
            entries = _entries_from_export(json.loads(raw.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": f"读取文件失败：{exc}"}, status_code=400)

        try:
            return JSONResponse(await _import_entries(entries))
        except Exception as exc:
            sh.logger.exception("Sterling import failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @mcp.custom_route("/api/journal/sync/status", methods=["GET"])
    async def api_sync_status(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        return err or JSONResponse({"ok": True, **_sync_status()})

    @mcp.custom_route("/api/journal/sync/config", methods=["POST"])
    async def api_sync_config(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            body = await request.json()
            cfg = _sync_config()
            for field, default in (("repo", ""), ("branch", "main"), ("path", "sterling-journal.json"), ("allowed_origin", "")):
                if field in body:
                    cfg[field] = str(body.get(field) or default).strip()
            # Keep the existing secret when the password field is intentionally blank.
            new_github_token = str(body.get("github_token") or "").strip()
            if new_github_token:
                cfg["github_token"] = new_github_token
            if not cfg.get("repo") or "/" not in str(cfg["repo"]):
                raise ValueError("请先填写日记仓库（例如 iwanttosleeeep/garden-journal-sync）")
            if not cfg.get("allowed_origin"):
                raise ValueError("请先填写 Sterling 地址")
            key = ""
            if body.get("rotate_key") or not cfg.get("token_hash"):
                key = secrets.token_urlsafe(32)
                cfg["token_hash"] = hashlib.sha256(key.encode("utf-8")).hexdigest()
            _save_sync_config()
            return JSONResponse({"ok": True, **_sync_status(), "sync_key": key})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/journal/sync/pull", methods=["POST"])
    async def api_sync_pull(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            payload = await _get_github_json()
            return JSONResponse(await _import_entries(_entries_from_export(payload)))
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/journal/sync/validate", methods=["POST"])
    async def api_sync_validate(request: Request) -> Response:
        from starlette.responses import JSONResponse
        err = sh._require_auth(request)
        if err:
            return err
        try:
            result = await _validate_github_journal_access()
            result["ok"] = bool(result["authenticated"] and result["repo_access"])
            return JSONResponse(result)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)

    @mcp.custom_route("/api/journal/sync/push", methods=["OPTIONS", "POST"])
    async def api_sync_push(request: Request) -> Response:
        from starlette.responses import JSONResponse
        headers = _cors_headers(request)
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=headers)
        if not headers or not _sync_token_ok(request):
            return JSONResponse({"ok": False, "error": "Sterling 同步未获授权"}, status_code=401, headers=headers)
        try:
            raw = await request.body()
            if len(raw) > _MAX_UPLOAD_BYTES:
                raise ValueError("日记同步内容不能超过 2 MB")
            payload = json.loads(raw.decode("utf-8"))
            entries = _entries_from_export(payload)
            await _put_github_json(payload)
            return JSONResponse(await _import_entries(entries), headers=headers)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=400, headers=headers)
        except Exception as exc:
            sh.logger.exception("Sterling sync push failed")
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500, headers=headers)
