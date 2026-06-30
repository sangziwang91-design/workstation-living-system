from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .capabilities import baseline_registry
from .channel_gateway import ChannelMessage
from .config import default_config
from .runtime import LivingSystem
from .scheduler import ScheduledEvent


ALLOWED_VERDICTS = {
    "ADMIT",
    "ADMIT_SHADOW_ONLY",
    "ADMIT_DESIGN_ONLY",
    "REJECT",
    "BLOCKED",
}


@dataclass(slots=True)
class ArchitecturePassResult:
    validation_pass_id: str
    verdict: str
    evidence: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.verdict not in ALLOWED_VERDICTS:
            raise ValueError(f"invalid verdict: {self.verdict}")

    @property
    def pass_id(self) -> str:
        return self.validation_pass_id

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["pass_id"] = data.pop("validation_pass_id")
        return data


def validate_p01_registry() -> ArchitecturePassResult:
    registry = baseline_registry()
    registry.assert_no_duplicate_authority()
    return ArchitecturePassResult(
        "P01",
        "ADMIT",
        [item["capability_id"] for item in registry.list()],
        ["registry is an adapter/projection map; canonical owners remain existing WLS classes"],
    )


def validate_runtime_event_ingress(home: Path) -> list[ArchitecturePassResult]:
    runtime = LivingSystem(default_config(home))
    channel_id, channel_inserted = runtime.ingest_channel_message(
        ChannelMessage("owner_console", "owner", "status?", "validation-channel-1")
    )
    scheduled_id, scheduled_inserted = runtime.emit_scheduled_event(
        ScheduledEvent(
            schedule_id="validation-schedule-1",
            event_type="scheduled.read_only_check",
            payload={"target": "status"},
            due_at="2026-06-30T00:00:00+00:00",
        )
    )
    rows = runtime.db.query_all(
        "SELECT event_id,event_type,source FROM events WHERE event_id IN (?,?)",
        (channel_id, scheduled_id),
    )
    if not channel_inserted or not scheduled_inserted or len(rows) != 2:
        return [
            ArchitecturePassResult(
                "P02",
                "BLOCKED",
                [],
                ["runtime EventStore integration did not persist both events"],
            ),
            ArchitecturePassResult(
                "P09",
                "BLOCKED",
                [],
                ["runtime channel ingress did not persist through EventStore"],
            ),
        ]
    return [
        ArchitecturePassResult(
            "P02",
            "ADMIT_SHADOW_ONLY",
            [scheduled_id],
            ["scheduler is admitted as runtime Event source; durable restart experiment remains future gate"],
        ),
        ArchitecturePassResult(
            "P09",
            "ADMIT_SHADOW_ONLY",
            [channel_id],
            ["channel ingress reaches canonical EventStore through LivingSystem"],
        ),
    ]


def validate_runtime_provider_route(home: Path) -> ArchitecturePassResult:
    runtime = LivingSystem(default_config(home))
    route = runtime.planner.route_summary()
    if route.get("provider_id") != runtime.planner.provider_type:
        return ArchitecturePassResult(
            "P08",
            "BLOCKED",
            [],
            ["Planner provider route does not match configured planner provider"],
        )
    evidence = route.get("evidence", {})
    provider = evidence.get("provider", {}) if isinstance(evidence, dict) else {}
    if provider.get("remote") or provider.get("paid"):
        return ArchitecturePassResult(
            "P08",
            "BLOCKED",
            [],
            ["Default planner route is not local/free"],
        )
    return ArchitecturePassResult(
        "P08",
        "ADMIT_SHADOW_ONLY",
        [str(route.get("provider_id", "UNKNOWN"))],
        ["Planner owns provider routing evidence; route is local-first and free by default"],
    )
