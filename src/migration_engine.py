"""
========================================
migration_engine.py — embedding 迁移引擎（2.0.3 新增）
========================================

切 embedding 后端（local ↔ api）时，需要把 embeddings.db 里所有 bucket 的向量
用新后端重算一遍。这个模块负责后台跑这件事：

- 备份 embeddings.db → embeddings.db.backup（只在第一次启动时）
- 把新向量先写入 embeddings.db.migrating，避免半截状态污染主表
- 全部跑完后 atomically swap：主 db 替成 .migrating 文件
- 单条失败跳过 + 记录到 failed_items[:50]，不中断整体
- 进度文件 _pending_migration_status.json，前端 3s 轮询
- 断点续传：_migration_checkpoint.json 记录已完成 id 集合
- 限速：每批 10 条，间隔 0.5s（避免本地推理打爆 CPU 或 API 限流）
- 失败时附最近 15 行 errors.jsonl，提示她/他「这是本地环境相关问题」

不做：
- 不做 bucket 迁移、桶文件重写
- 不切换 global embedding_engine —— 那是 server.py 调用方的事
- 不做配置写盘
========================================
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable

logger = logging.getLogger("ombre_brain.migration_engine")


# ---- 常量 ----

_STATUS_FILE_NAME = "_pending_migration_status.json"
_CHECKPOINT_FILE_NAME = "_migration_checkpoint.json"

# 每批 10 条，间隔 0.5s
BATCH_SIZE = 10
BATCH_INTERVAL_SEC = 0.5

# failed_items 上限（避免 status JSON 无限膨胀）
MAX_FAILED_ITEMS = 50

# 失败时附带的 errors.jsonl 末尾行数
TAIL_LOG_LINES = 15

# 进程级锁：同一时刻只允许一个迁移任务
_migration_lock = threading.Lock()
_migration_owner_guard = threading.Lock()
_migration_owner: "MigrationReservation | None" = None
_migration_task: asyncio.Task | None = None
_v3_runtime: Any = None


@dataclass(frozen=True)
class MigrationReservation:
    """Ownership token for one embedding migration attempt."""

    job_id: str


def reserve_migration() -> MigrationReservation | None:
    """Reserve the migration slot before staging files or providers are touched."""

    global _migration_owner
    if not _migration_lock.acquire(blocking=False):
        logger.info("[migration] another migration already in progress; skip")
        return None
    reservation = MigrationReservation(job_id=uuid.uuid4().hex)
    with _migration_owner_guard:
        _migration_owner = reservation
    return reservation


def owns_migration_reservation(reservation: MigrationReservation) -> bool:
    with _migration_owner_guard:
        return _migration_owner is reservation


def release_migration_reservation(reservation: MigrationReservation) -> bool:
    """Release only the reservation that currently owns the migration slot."""

    global _migration_owner
    with _migration_owner_guard:
        if _migration_owner is not reservation:
            return False
        _migration_owner = None
        _migration_lock.release()
    return True


def attach_v3_runtime(runtime) -> None:
    global _v3_runtime
    _v3_runtime = runtime


def get_v3_runtime():
    return _v3_runtime


# ============================================================
# 路径与状态
# ============================================================


def status_path_for(buckets_dir: str) -> str:
    log_dir = os.path.join(buckets_dir, ".logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, _STATUS_FILE_NAME)


def checkpoint_path_for(buckets_dir: str) -> str:
    log_dir = os.path.join(buckets_dir, ".logs")
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, _CHECKPOINT_FILE_NAME)


def _empty_status() -> dict[str, Any]:
    return {
        "phase": "idle",  # idle | running | completed | failed | publish_failed
        "total": 0,
        "done": 0,
        "failed_count": 0,
        "current_id": "",
        "failed_items": [],
        "started_at": "",
        "finished_at": "",
        "target_backend": "",
        "target_model": "",
        "target_dim": 0,
        "message": "",
        "error": "",
        "tail_log": [],
    }


def read_status(status_path: str) -> dict[str, Any]:
    if not os.path.exists(status_path):
        return _empty_status()
    try:
        with open(status_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _empty_status()
        return data
    except (OSError, json.JSONDecodeError):
        return _empty_status()


def write_status(status_path: str, status: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(status_path), exist_ok=True)
        tmp = status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)
        os.replace(tmp, status_path)
    except OSError as e:
        logger.warning(f"[migration] failed to write status: {e}")


def target_signature(target_backend: str, target_model: str, target_dim: int) -> str:
    """Return the identity of the embedding target used by a checkpoint."""

    return f"{target_backend}:{target_model}:{target_dim}"


def _read_checkpoint(path: str, signature: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("target_signature") != signature:
            return set()
        done = data.get("done_ids", [])
        return set(done) if isinstance(done, list) else set()
    except (OSError, json.JSONDecodeError):
        return set()


def _write_checkpoint(path: str, done_ids: Iterable[str], signature: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                {"done_ids": sorted(done_ids), "target_signature": signature},
                f,
                ensure_ascii=False,
            )
        os.replace(tmp, path)
    except OSError as e:
        logger.warning(f"[migration] failed to write checkpoint: {e}")


def staging_db_path_for(db_path: str) -> str:
    """Return the isolated database used until migration succeeds completely."""

    return f"{db_path}.migrating"


def reconcile_migration_files(buckets_dir: str, db_path: str) -> None:
    """Remove an unpaired staging DB or checkpoint left by an early crash.

    A checkpoint without staging data would skip rows that no longer exist;
    staging data without a checkpoint may contain orphaned rows. Either side
    alone is unsafe to resume, so the remaining file is discarded.
    """

    ckpt_path = checkpoint_path_for(buckets_dir)
    staging_path = staging_db_path_for(db_path)
    checkpoint_exists = os.path.exists(ckpt_path)
    staging_exists = os.path.exists(staging_path)
    if checkpoint_exists == staging_exists:
        return
    orphan = ckpt_path if checkpoint_exists else staging_path
    try:
        os.remove(orphan)
    except OSError as exc:
        logger.warning("[migration] failed to remove unpaired state %s: %s", orphan, exc)


def reset_stale_migration_state(buckets_dir: str, db_path: str, signature: str) -> None:
    """Discard a checkpoint and staging DB produced for a different target."""

    ckpt_path = checkpoint_path_for(buckets_dir)
    if not os.path.exists(ckpt_path):
        return
    try:
        with open(ckpt_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        stale = not isinstance(data, dict) or data.get("target_signature") != signature
    except (OSError, json.JSONDecodeError):
        stale = True
    if not stale:
        return
    try:
        os.remove(ckpt_path)
    except OSError:
        pass
    staging_path = staging_db_path_for(db_path)
    try:
        if os.path.exists(staging_path):
            os.remove(staging_path)
    except OSError as exc:
        logger.warning("[migration] failed to remove stale staging db %s: %s", staging_path, exc)


def _tail_errors_log(buckets_dir: str, n: int = TAIL_LOG_LINES) -> list[str]:
    """读 errors.jsonl 末尾 n 行。失败返回空列表。"""
    candidates = [
        os.path.join(buckets_dir, ".logs", "errors.jsonl"),
        os.path.join(buckets_dir, "errors.jsonl"),
    ]
    for p in candidates:
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r", encoding="utf-8") as f:
                lines = f.readlines()
            return [ln.rstrip("\n") for ln in lines[-n:]]
        except OSError:
            continue
    return []


# ============================================================
# 备份与提交
# ============================================================


def backup_db_once(db_path: str) -> str:
    """如果 .backup 不存在则备份 db_path，返回备份文件路径。

    已存在 .backup 则不重复备份（避免覆盖更早版本）。
    """
    backup = db_path + ".backup"
    if os.path.exists(backup):
        return backup
    if not os.path.exists(db_path):
        return backup
    shutil.copy2(db_path, backup)
    return backup


# ============================================================
# 迁移核心
# ============================================================


@dataclass
class MigrationConfig:
    """迁移参数。"""

    buckets_dir: str
    db_path: str
    target_backend: str  # 'local' | 'api'
    target_model: str
    target_dim: int
    # source/target engine 都已由调用方实例化好
    target_engine: Any  # EmbeddingEngine 实例（迁移目标）
    # bucket 内容来源：返回 list[(bucket_id, content)] 的 awaitable
    fetch_buckets: Callable[[], Awaitable[list[tuple[str, str]]]]


async def _run_migration(
    cfg: MigrationConfig,
    on_complete: Callable[[bool], None] | None = None,
) -> None:
    """实际跑迁移的协程。"""
    status_path = status_path_for(cfg.buckets_dir)
    ckpt_path = checkpoint_path_for(cfg.buckets_dir)

    # 1) 备份原 db
    try:
        backup_db_once(cfg.db_path)
    except Exception as e:
        write_status(
            status_path,
            {
                **_empty_status(),
                "phase": "failed",
                "error": f"backup failed: {type(e).__name__}: {e}",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "message": "迁移未启动：备份 embeddings.db 失败",
                "tail_log": _tail_errors_log(cfg.buckets_dir),
            },
        )
        if on_complete:
            on_complete(False)
        return

    # 2) 拉所有 bucket
    try:
        buckets = await cfg.fetch_buckets()
    except Exception as e:
        write_status(
            status_path,
            {
                **_empty_status(),
                "phase": "failed",
                "error": f"fetch buckets failed: {type(e).__name__}: {e}",
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "message": "迁移未启动：列出桶失败",
                "tail_log": _tail_errors_log(cfg.buckets_dir),
            },
        )
        if on_complete:
            on_complete(False)
        return

    total = len(buckets)
    signature = target_signature(cfg.target_backend, cfg.target_model, cfg.target_dim)
    done_ids = _read_checkpoint(ckpt_path, signature)
    failed_items: list[dict[str, str]] = []
    failed_count = 0

    write_status(
        status_path,
        {
            **_empty_status(),
            "phase": "running",
            "total": total,
            "done": len(done_ids),
            "failed_count": 0,
            "current_id": "",
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "target_backend": cfg.target_backend,
            "target_model": cfg.target_model,
            "target_dim": cfg.target_dim,
            "message": f"开始迁移 {total} 个 bucket（已完成 {len(done_ids)}）",
        },
    )

    # 3) 分批跑
    pending = [(bid, content) for bid, content in buckets if bid not in done_ids]
    for i in range(0, len(pending), BATCH_SIZE):
        batch = pending[i : i + BATCH_SIZE]
        for bucket_id, content in batch:
            cur = read_status(status_path)
            cur["current_id"] = bucket_id
            write_status(status_path, cur)

            try:
                ok = await cfg.target_engine.generate_and_store(bucket_id, content)
                if not ok:
                    failed_count += 1
                    if len(failed_items) < MAX_FAILED_ITEMS:
                        failed_items.append(
                            {
                                "bucket_id": bucket_id,
                                "error": "generate_and_store returned False",
                            }
                        )
                else:
                    done_ids.add(bucket_id)
            except Exception as e:
                failed_count += 1
                if len(failed_items) < MAX_FAILED_ITEMS:
                    failed_items.append(
                        {
                            "bucket_id": bucket_id,
                            "error": f"{type(e).__name__}: {e}",
                        }
                    )

        # 每批写一次 checkpoint + status
        _write_checkpoint(ckpt_path, done_ids, signature)
        cur = read_status(status_path)
        cur["done"] = len(done_ids)
        cur["failed_count"] = failed_count
        cur["failed_items"] = failed_items
        cur["message"] = f"已完成 {len(done_ids)} / {total}（失败 {failed_count}）"
        write_status(status_path, cur)

        # 限速
        if i + BATCH_SIZE < len(pending):
            await asyncio.sleep(BATCH_INTERVAL_SEC)

    # Publish only a complete staging database. The live database remains
    # untouched after any provider failure or interruption.
    all_done = failed_count == 0 and len(done_ids) >= total
    swap_error = ""
    if all_done:
        staged_path = getattr(cfg.target_engine, "db_path", "")
        if staged_path and os.path.abspath(staged_path) != os.path.abspath(cfg.db_path):
            try:
                os.replace(staged_path, cfg.db_path)
                cfg.target_engine.db_path = cfg.db_path
            except OSError as exc:
                swap_error = f"{type(exc).__name__}: {exc}"
                logger.error("[migration] atomic swap staging→live failed: %s", swap_error)

    finished_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    success = all_done and not swap_error
    publish_errors: list[str] = []

    if success:
        try:
            cfg.target_engine._write_meta("model_name", cfg.target_model or "")
            cfg.target_engine._write_meta("vector_dim", str(cfg.target_dim or 0))
        except Exception as exc:
            logger.warning("[migration] update meta failed: %s", exc)
            publish_errors.append(f"元数据发布失败: {type(exc).__name__}: {exc}")

    # 完成后清掉 checkpoint（下次切换从头开始）
    if success:
        try:
            if os.path.exists(ckpt_path):
                os.remove(ckpt_path)
        except OSError:
            pass

    if on_complete:
        try:
            on_complete(success)
        except Exception as exc:
            logger.warning("[migration] on_complete callback failed: %s", exc)
            if success:
                publish_errors.append(f"运行态/配置发布失败: {type(exc).__name__}: {exc}")

    final_phase = "completed" if success else "failed"
    if swap_error:
        final_msg = f"迁移全部完成但原子替换主库失败，向量仍留在暂存文件：{swap_error}"
        final_error = swap_error
    elif success and publish_errors:
        final_phase = "publish_failed"
        final_error = "; ".join(publish_errors)
        final_msg = f"向量主库已替换，但迁移结果发布未完整完成：{final_error}"
    else:
        final_msg = f"迁移完成：{len(done_ids)} 成功 / {failed_count} 失败"
        final_error = ""

    cur = read_status(status_path)
    cur.update(
        {
            "phase": final_phase,
            "current_id": "",
            "done": len(done_ids),
            "failed_count": failed_count if not swap_error else max(failed_count, 1),
            "failed_items": failed_items,
            "finished_at": finished_at,
            "message": final_msg,
            "error": final_error,
            "tail_log": _tail_errors_log(cfg.buckets_dir) if failed_count or swap_error or publish_errors else [],
        }
    )
    write_status(status_path, cur)


def start_migration(
    cfg: MigrationConfig,
    loop: asyncio.AbstractEventLoop | None = None,
    on_complete: Callable[[bool], None] | None = None,
    *,
    reservation: MigrationReservation | None = None,
) -> asyncio.Task | None:
    """在指定 event loop 上启动后台迁移任务。

    同一时刻只允许一个迁移任务，重复调用返回 None。
    """
    global _migration_task
    active_reservation = reservation or reserve_migration()
    if active_reservation is None:
        return None
    if not owns_migration_reservation(active_reservation):
        logger.warning("[migration] rejected stale or foreign reservation")
        return None

    target_loop = loop or asyncio.get_event_loop()
    callback_called = False

    def _complete_once(success: bool) -> None:
        nonlocal callback_called
        if callback_called:
            return
        callback_called = True
        if on_complete:
            on_complete(success)

    async def _wrap():
        try:
            await _run_migration(cfg, on_complete=_complete_once)
        except BaseException:
            _complete_once(False)
            raise
        finally:
            release_migration_reservation(active_reservation)

    try:
        task = target_loop.create_task(_wrap())
    except BaseException:
        release_migration_reservation(active_reservation)
        raise
    _migration_task = task
    return task


def is_running() -> bool:
    return _migration_lock.locked()


def reset_for_test() -> None:
    """测试用：强制释放锁。"""
    global _migration_owner, _migration_task
    with _migration_owner_guard:
        _migration_owner = None
        if _migration_lock.locked():
            try:
                _migration_lock.release()
            except RuntimeError:
                pass
    _migration_task = None


__all__ = [
    "MigrationConfig",
    "status_path_for",
    "checkpoint_path_for",
    "read_status",
    "write_status",
    "backup_db_once",
    "MigrationReservation",
    "reserve_migration",
    "owns_migration_reservation",
    "release_migration_reservation",
    "staging_db_path_for",
    "reconcile_migration_files",
    "reset_stale_migration_state",
    "target_signature",
    "start_migration",
    "is_running",
    "reset_for_test",
    "attach_v3_runtime",
    "get_v3_runtime",
    "BATCH_SIZE",
    "BATCH_INTERVAL_SEC",
]
