from __future__ import annotations

from types import MethodType
from typing import Any

from .durable_commands import DurableCommandQueue
from .replay_manifest import ReplayManifestStore
from .runtime_trace import RuntimeTraceStore


PLUGIN_VERSION = "durable-spine-v1"


def register_wls(runtime: Any) -> None:
    """Attach subordinate durable-control stores to the canonical LivingSystem.

    This plugin is intentionally opt-in. It does not execute queued commands,
    replace planning, attach providers, or create another runtime loop.
    """

    if getattr(runtime, "_durable_spine_installed", False):
        return
    runtime._durable_spine_installed = True
    runtime.command_queue = DurableCommandQueue(runtime.db, runtime.ledger)
    runtime.trace_store = RuntimeTraceStore(runtime.db, runtime.ledger)
    runtime.replay_manifests = ReplayManifestStore(runtime.db, runtime.ledger)

    original_verify = runtime.verify_integrity

    def verify_integrity(self: Any, full: bool = True) -> dict[str, Any]:
        result = original_verify(full=full)
        queue_ok, queue_counts = self.command_queue.integrity()
        trace_ok, trace_counts = self.trace_store.integrity()
        replay_ok, replay_counts = self.replay_manifests.integrity()
        spine_ok = queue_ok and trace_ok and replay_ok
        result["durable_spine"] = {
            "ok": spine_ok,
            "command_queue": queue_counts,
            "runtime_trace": trace_counts,
            "replay_manifest": replay_counts,
        }
        result["ok"] = bool(result.get("ok")) and spine_ok
        return result

    runtime.verify_integrity = MethodType(verify_integrity, runtime)
    runtime.ledger.append(
        "durable_spine_installed",
        {
            "version": PLUGIN_VERSION,
            "opt_in": True,
            "command_execution_attached": False,
            "planner_replaced": False,
            "provider_attached": False,
        },
    )
