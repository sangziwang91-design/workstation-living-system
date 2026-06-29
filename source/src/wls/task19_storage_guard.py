from __future__ import annotations

from pathlib import Path
from types import MethodType
from typing import Any


DEFAULT_WAL_CHECKPOINT_BYTES = 64 * 1024 * 1024


def _wal_bytes(runtime: Any) -> int:
    path = Path(str(runtime.db.path) + "-wal")
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _passive_checkpoint(runtime: Any) -> dict[str, int | str]:
    connection = runtime.db.connect()
    try:
        row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        if row is None:
            return {
                "mode": "PASSIVE",
                "busy": -1,
                "log_frames": -1,
                "checkpointed_frames": -1,
            }
        return {
            "mode": "PASSIVE",
            "busy": int(row[0]),
            "log_frames": int(row[1]),
            "checkpointed_frames": int(row[2]),
        }
    finally:
        connection.close()


def register_wls(runtime: Any) -> None:
    if getattr(runtime, "_task19_storage_guard_installed", False):
        return
    runtime._task19_storage_guard_installed = True
    supervisor = runtime.survival
    original_preflight = supervisor.preflight
    original_status = supervisor.status
    threshold = int(
        runtime.config.provider.get(
            "wal_checkpoint_bytes", DEFAULT_WAL_CHECKPOINT_BYTES
        )
    )
    threshold = max(1024 * 1024, min(threshold, 4 * 1024 * 1024 * 1024))
    runtime._task19_last_checkpoint = None

    def preflight(self: Any, run_id: str, cycle_index: int) -> dict[str, Any]:
        before = _wal_bytes(runtime)
        checkpoint = None
        if before >= threshold:
            checkpoint = _passive_checkpoint(runtime)
            checkpoint["wal_bytes_before"] = before
            checkpoint["wal_bytes_after"] = _wal_bytes(runtime)
            runtime._task19_last_checkpoint = checkpoint
            runtime.ledger.append(
                "survival_wal_checkpoint",
                {
                    "run_id": run_id,
                    "cycle_index": cycle_index,
                    **checkpoint,
                },
            )
        result = original_preflight(run_id, cycle_index)
        result["wal_bytes"] = _wal_bytes(runtime)
        result["wal_checkpoint"] = checkpoint
        return result

    def status(self: Any, incident_limit: int = 20) -> dict[str, Any]:
        result = original_status(incident_limit)
        result["wal"] = {
            "bytes": _wal_bytes(runtime),
            "checkpoint_threshold_bytes": threshold,
            "last_checkpoint": runtime._task19_last_checkpoint,
        }
        return result

    supervisor.preflight = MethodType(preflight, supervisor)
    supervisor.status = MethodType(status, supervisor)
    runtime.ledger.append(
        "task19_storage_guard_installed",
        {"wal_checkpoint_threshold_bytes": threshold},
    )
