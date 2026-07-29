import asyncio
import os
from types import SimpleNamespace

import pytest

from migration_engine import (
    MigrationConfig,
    _run_migration,
    _write_checkpoint,
    checkpoint_path_for,
    read_status,
    reconcile_migration_files,
    release_migration_reservation,
    reserve_migration,
    reset_for_test,
    reset_stale_migration_state,
    staging_db_path_for,
    status_path_for,
    target_signature,
)


class FakeTargetEngine:
    def __init__(self, db_path):
        self.db_path = db_path
        self.meta = {}

    async def generate_and_store(self, bucket_id, _content):
        with open(self.db_path, "a", encoding="utf-8") as handle:
            handle.write(f"{bucket_id}\n")
        return True

    def _write_meta(self, key, value):
        self.meta[key] = value


def migration_config(tmp_path, engine, fetch_buckets):
    buckets_dir = tmp_path / "buckets"
    buckets_dir.mkdir()
    live_db = tmp_path / "embeddings.db"
    live_db.write_text("OLD-LIVE-CONTENT\n", encoding="utf-8")
    return live_db, MigrationConfig(
        buckets_dir=str(buckets_dir),
        db_path=str(live_db),
        target_backend="api",
        target_model="test-model",
        target_dim=8,
        target_engine=engine(str(live_db) + ".migrating"),
        fetch_buckets=fetch_buckets,
    )


@pytest.mark.asyncio
async def test_successful_migration_atomically_swaps_staging_into_live(tmp_path):
    async def fetch_buckets():
        return [("b1", "first"), ("b2", "second")]

    live_db, config = migration_config(tmp_path, FakeTargetEngine, fetch_buckets)
    await _run_migration(config)

    content = live_db.read_text(encoding="utf-8")
    assert "OLD-LIVE-CONTENT" not in content
    assert "b1" in content and "b2" in content
    assert not os.path.exists(staging_db_path_for(str(live_db)))
    assert config.target_engine.db_path == str(live_db)
    assert read_status(status_path_for(config.buckets_dir))["phase"] == "completed"
    assert not os.path.exists(checkpoint_path_for(config.buckets_dir))


@pytest.mark.asyncio
async def test_failed_migration_never_touches_live_db(tmp_path):
    class FailingTargetEngine(FakeTargetEngine):
        async def generate_and_store(self, bucket_id, content):
            if bucket_id == "b2":
                raise RuntimeError("provider failure")
            return await super().generate_and_store(bucket_id, content)

    async def fetch_buckets():
        return [("b1", "first"), ("b2", "second")]

    live_db, config = migration_config(tmp_path, FailingTargetEngine, fetch_buckets)
    await _run_migration(config)

    assert live_db.read_text(encoding="utf-8") == "OLD-LIVE-CONTENT\n"
    status = read_status(status_path_for(config.buckets_dir))
    assert status["phase"] == "failed"
    assert status["failed_count"] == 1
    assert os.path.exists(checkpoint_path_for(config.buckets_dir))


@pytest.mark.asyncio
async def test_publish_failure_is_visible_after_successful_swap(tmp_path):
    async def fetch_buckets():
        return [("b1", "first")]

    live_db, config = migration_config(tmp_path, FakeTargetEngine, fetch_buckets)

    def fail_publish(success):
        assert success is True
        raise OSError("config publish failure")

    await _run_migration(config, on_complete=fail_publish)
    status = read_status(status_path_for(config.buckets_dir))
    assert status["phase"] == "publish_failed"
    assert "config publish failure" in status["error"]
    assert "b1" in live_db.read_text(encoding="utf-8")


def test_mismatched_checkpoint_discards_staging_database(tmp_path):
    buckets_dir = tmp_path / "buckets"
    buckets_dir.mkdir()
    live_db = tmp_path / "embeddings.db"
    staging = staging_db_path_for(str(live_db))
    with open(staging, "w", encoding="utf-8") as handle:
        handle.write("old target")
    checkpoint = checkpoint_path_for(str(buckets_dir))
    _write_checkpoint(checkpoint, {"b1"}, target_signature("api", "old", 4))

    reset_stale_migration_state(str(buckets_dir), str(live_db), target_signature("api", "new", 8))

    assert not os.path.exists(checkpoint)
    assert not os.path.exists(staging)


@pytest.mark.parametrize("orphan_kind", ["checkpoint", "staging"])
def test_unpaired_migration_state_is_not_resumed(tmp_path, orphan_kind):
    buckets_dir = tmp_path / "buckets"
    buckets_dir.mkdir()
    live_db = tmp_path / "embeddings.db"
    checkpoint = checkpoint_path_for(str(buckets_dir))
    staging = staging_db_path_for(str(live_db))
    if orphan_kind == "checkpoint":
        _write_checkpoint(checkpoint, {"b1"}, target_signature("api", "model", 8))
        orphan = checkpoint
    else:
        with open(staging, "w", encoding="utf-8") as handle:
            handle.write("partial rows without checkpoint")
        orphan = staging

    reconcile_migration_files(str(buckets_dir), str(live_db))

    assert not os.path.exists(orphan)


def test_migration_reservation_rejects_concurrent_owner():
    reset_for_test()
    first = reserve_migration()
    try:
        assert first is not None
        assert reserve_migration() is None
    finally:
        assert first is not None
        assert release_migration_reservation(first)


@pytest.mark.asyncio
async def test_cancelled_worker_releases_reservation_and_runs_cleanup(tmp_path):
    from migration_engine import start_migration

    fetch_started = asyncio.Event()
    never_finish = asyncio.Event()
    completions = []

    async def fetch_buckets():
        fetch_started.set()
        await never_finish.wait()
        return []

    config = MigrationConfig(
        buckets_dir=str(tmp_path / "buckets"),
        db_path=str(tmp_path / "embeddings.db"),
        target_backend="api",
        target_model="model",
        target_dim=3,
        target_engine=SimpleNamespace(),
        fetch_buckets=fetch_buckets,
    )
    task = start_migration(config, on_complete=completions.append)
    assert task is not None
    await asyncio.wait_for(fetch_started.wait(), timeout=2)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert completions == [False]
    next_owner = reserve_migration()
    assert next_owner is not None
    assert release_migration_reservation(next_owner)
