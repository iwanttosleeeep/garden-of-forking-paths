from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import Any

from ombrebrain.app.execution import ExecutionEnvelope
from ombrebrain.app.profiles import build_default_legacy_profiles
from ombrebrain.app.legacy_runtime import LegacyRuntime
from ombrebrain.architecture import (
    ADRDocument,
    ADRRequirementsContract,
    ArchitectureAuditor,
    ArtifactLanguage,
    ArtifactRole,
    CodeArtifactSpec,
    HighestDifficultyCodeStandards,
    default_architecture,
)
from ombrebrain.cluster.replication import ReplicationContract, ReplicationSegment, ReplicationTopology
from ombrebrain.domain import AdvancedCommandBoundaryContract, BoundaryStage, CommandBoundaryReceipt
from ombrebrain.domain.commands import CommandKind, MemoryCommand, MemoryCommandRouter
from ombrebrain.kernel.errors import PolicyViolation
from ombrebrain.observability import ObservabilityMetricBoundary
from ombrebrain.maintenance.migration_contract import (
    MigrationPhasePlan,
    MigrationPreservationContract,
    MigrationTraceRecord,
)
from ombrebrain.plugins import PluginManifest, PluginRuntime, PluginSandbox
from ombrebrain.policy import RedLineContract, RedLineFeatureSpec, SurfaceDecision
from ombrebrain.policy.engine import PolicyEngine
from ombrebrain.policy.formal_invariants import FormalInvariantChecker
from ombrebrain.protocol import PublicToolDesignContract, PublicToolSpec, ToolExposure
from ombrebrain.resilience import CrashRecoveryContract, CrashRecoveryPlan, PathStep
from ombrebrain.retrieval import (
    MemoryContextCompiler,
    RetrievalCandidate,
    RetrievalFeatures,
)
from ombrebrain.resilience.scanner import V3ResilienceScanner
from ledger_mirror import LedgerMirror
from ledger_property import LedgerReplayPropertyRunner
from ledger_replay import LedgerReplayValidator
from projection_mirror import TraceCatalogProjection
from projection_sqlite import TraceSQLiteProjection
from projection_vector import TraceVectorProjectionManifest


@dataclass(frozen=True)
class V3MaintenanceReportBuilder:
    runtime: LegacyRuntime

    def build(self, *, decision_limit: int = 20) -> dict[str, Any]:
        architecture = ArchitectureAuditor.default().audit(default_architecture()).to_dict()
        resilience = V3ResilienceScanner(self.runtime.fabric).scan().to_dict()
        decisions = self.runtime.debug_decisions(limit=decision_limit)
        report = {
            "ok": bool(architecture.get("ok")) and bool(resilience.get("ok")),
            "runtime": {
                "root": str(self.runtime.root),
                "next_index": _safe_next_index(self.runtime),
                "capability_count": len(self.runtime.capability_names()),
            },
            "architecture": architecture,
            "resilience": resilience,
            "decisions": decisions,
        }
        return _json_safe(report)



def _safe_next_index(runtime: LegacyRuntime) -> int | None:
    try:
        return runtime.fabric.next_index()
    except Exception:
        return None


def _summarize_checks(checks: dict[str, dict[str, Any]]) -> dict[str, int]:
    summary = {"ok": 0, "warning": 0, "error": 0}
    for check in checks.values():
        if not check.get("ok"):
            summary["error"] += 1
        elif check.get("status") == "warning":
            summary["warning"] += 1
        else:
            summary["ok"] += 1
    return summary


def _is_boundary_candidate(event: object) -> bool:
    metadata = dict(getattr(event, "metadata", {}) or {})
    source_chain = tuple(str(part) for part in getattr(event, "source_chain", ()) or ())
    return (
        "command_boundary" in metadata
        or "command_boundary_error" in metadata
        or "command_plan" in metadata
        or source_chain[:1] in {("legacy_execution",), ("legacy_tool",)}
    )


def _event_summary(event: object) -> dict[str, Any]:
    metadata = dict(getattr(event, "metadata", {}) or {})
    command_plan = metadata.get("command_plan") if isinstance(metadata.get("command_plan"), dict) else {}
    return {
        "id": str(getattr(event, "id", "")),
        "source_chain": list(getattr(event, "source_chain", ()) or ()),
        "command_id": str(command_plan.get("command_id", "")),
        "command_kind": str(command_plan.get("command_kind", "")),
    }


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False, default=str))


def _sample_ledger(root: str) -> LedgerMirror:
    ledger = LedgerMirror(f"{root}/events.jsonl")
    ledger.append_event(
        event_type="TraceCreated",
        trace_id="active-ok",
        trace_kind="dynamic",
        payload={"name": "active sample", "tags": ["active"], "domain": ["preflight"]},
        body="body one",
    )
    ledger.append_event(
        event_type="TraceTouched",
        trace_id="active-ok",
        trace_kind="dynamic",
        payload={"activation_count": 1},
        body="body one",
    )
    ledger.append_event(
        event_type="TraceDeletedToArchive",
        trace_id="tombstone-ok",
        trace_kind="dynamic",
        payload={
            "name": "tombstone sample",
            "deleted_at": "2026-07-06T00:00:00+00:00",
            "tombstone": True,
            "tombstoned_at": "2026-07-06T00:00:00+00:00",
            "erasure_mode": "tombstone_only",
        },
        body="body two",
    )
    return ledger


def _write_preflight_embedding_db(path: str, vectors: dict[str, list[float]]) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute(
            """
            CREATE TABLE embeddings (
                bucket_id TEXT PRIMARY KEY,
                embedding TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE embeddings_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute("INSERT INTO embeddings_meta (key, value) VALUES (?, ?)", ("model_name", "preflight"))
        conn.execute("INSERT INTO embeddings_meta (key, value) VALUES (?, ?)", ("vector_dim", "3"))
        for bucket_id, vector in vectors.items():
            conn.execute(
                "INSERT INTO embeddings (bucket_id, embedding, updated_at) VALUES (?, ?, ?)",
                (bucket_id, json.dumps(vector), "2026-07-06T00:00:00+00:00"),
            )
