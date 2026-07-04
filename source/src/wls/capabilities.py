from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class CapabilityMode(StrEnum):
    ADAPTER = "ADAPTER"
    PROJECTION = "PROJECTION"
    WORKBENCH = "WORKBENCH"
    EVENT_SOURCE = "EVENT_SOURCE"


class CapabilityStatus(StrEnum):
    DESIGN_ONLY = "DESIGN_ONLY"
    SHADOW_ONLY = "SHADOW_ONLY"
    PILOT = "PILOT"
    ACTIVE = "ACTIVE"
    DORMANT = "DORMANT"
    RETIRED = "RETIRED"


CANONICAL_AUTHORITIES: dict[str, str] = {
    "subject": "wls.runtime.LivingSystem",
    "events": "wls.stores.EventStore",
    "goals": "wls.stores.GoalStore",
    "memory": "wls.stores.MemoryStore",
    "world": "wls.world.WorldModel",
    "temporal_world": "wls.temporal_world.TemporalCausalWorld",
    "planning": "wls.planner.Planner",
    "policy": "wls.policy.PolicyEngine",
    "approval": "wls.approval.ApprovalManager",
    "tools": "wls.tools.ToolRegistry",
    "evidence": "wls.evidence.EvidenceLedger",
    "skills": "wls.skills.SkillLibrary",
    "evolution": "wls.growth_cycle.GrowthCycleManager",
    "process_lease": "wls.lease.ProcessLease",
}
FORBIDDEN_AUTHORITY_NAMES = {
    "runtime",
    "subject",
    "planner",
    "memory",
    "goals",
    "policy",
    "evidence",
    "skills",
    "world",
    "approval",
}
SIDE_EFFECT_CLASSES = {"none", "reversible", "external", "irreversible"}
RISK_CEILINGS = {"READ", "REVERSIBLE_WRITE", "EXTERNAL", "IRREVERSIBLE"}


@dataclass(slots=True)
class CapabilityManifest:
    capability_id: str
    version: str
    mode: CapabilityMode
    canonical_owner: str
    risk_ceiling: str
    side_effect_class: str
    interfaces: list[str]
    rollback: list[str]
    declares_authority: bool = False
    status: CapabilityStatus = CapabilityStatus.DESIGN_ONLY
    dependencies: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.capability_id or not self.capability_id[0].islower():
            raise ValueError("capability_id must start with a lowercase letter")
        if self.declares_authority:
            raise ValueError("capabilities cannot declare canonical authority")
        if self.canonical_owner not in CANONICAL_AUTHORITIES:
            raise ValueError(f"unknown canonical owner: {self.canonical_owner}")
        if self.risk_ceiling not in RISK_CEILINGS:
            raise ValueError(f"unknown risk ceiling: {self.risk_ceiling}")
        if self.side_effect_class not in SIDE_EFFECT_CLASSES:
            raise ValueError(f"unknown side effect class: {self.side_effect_class}")
        if not self.interfaces:
            raise ValueError("capability must declare at least one interface")
        if not self.rollback:
            raise ValueError("capability must declare rollback")
        if self.status in {CapabilityStatus.PILOT, CapabilityStatus.ACTIVE}:
            if not self.tests:
                raise ValueError("pilot/active capability requires tests")
            if not self.receipts:
                raise ValueError("pilot/active capability requires receipts")
        if self.capability_id in FORBIDDEN_AUTHORITY_NAMES:
            raise ValueError(f"capability id conflicts with authority: {self.capability_id}")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["mode"] = self.mode.value
        data["status"] = self.status.value
        data["canonical_owner_impl"] = CANONICAL_AUTHORITIES[self.canonical_owner]
        return data


class CapabilityRegistry:
    def __init__(self) -> None:
        self._items: dict[str, CapabilityManifest] = {}

    def register(self, manifest: CapabilityManifest) -> None:
        if manifest.capability_id in self._items:
            raise ValueError(f"capability already registered: {manifest.capability_id}")
        self._items[manifest.capability_id] = manifest

    def get(self, capability_id: str) -> CapabilityManifest:
        try:
            return self._items[capability_id]
        except KeyError as exc:
            raise KeyError(f"unknown capability: {capability_id}") from exc

    def list(self) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self._items.values()]

    def summary(self) -> dict[str, Any]:
        by_owner: dict[str, int] = {}
        by_mode: dict[str, int] = {}
        for item in self._items.values():
            by_owner[item.canonical_owner] = by_owner.get(item.canonical_owner, 0) + 1
            by_mode[item.mode.value] = by_mode.get(item.mode.value, 0) + 1
        return {
            "count": len(self._items),
            "by_owner": by_owner,
            "by_mode": by_mode,
            "authority_model": "canonical WLS owners only",
        }

    def assert_no_duplicate_authority(self) -> None:
        for manifest in self._items.values():
            if manifest.declares_authority:
                raise ValueError(f"duplicate authority: {manifest.capability_id}")


def baseline_registry() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    for capability_id, owner, mode in [
        ("channel_ingress", "events", CapabilityMode.ADAPTER),
        ("scheduler", "events", CapabilityMode.EVENT_SOURCE),
        ("provider_routing", "planning", CapabilityMode.ADAPTER),
        ("browser_readonly", "tools", CapabilityMode.ADAPTER),
        ("browser_form_draft", "planning", CapabilityMode.WORKBENCH),
        ("download_quarantine_draft", "tools", CapabilityMode.WORKBENCH),
        ("document_ingress", "events", CapabilityMode.ADAPTER),
        ("computer_sandbox", "tools", CapabilityMode.ADAPTER),
        ("coding_worktree", "evolution", CapabilityMode.ADAPTER),
        ("external_memory_projection", "memory", CapabilityMode.PROJECTION),
        ("mcp_trust", "tools", CapabilityMode.ADAPTER),
        ("a2a_worker", "events", CapabilityMode.ADAPTER),
        ("owner_console", "evidence", CapabilityMode.PROJECTION),
        ("wechat_w0_w1", "events", CapabilityMode.ADAPTER),
        ("voice_transcript_ingress", "events", CapabilityMode.ADAPTER),
        ("screen_snapshot_ingress", "events", CapabilityMode.ADAPTER),
        ("read_only_task_organs", "planning", CapabilityMode.WORKBENCH),
        ("workbench_templates", "planning", CapabilityMode.WORKBENCH),
        ("agentic_task_harness", "planning", CapabilityMode.WORKBENCH),
        ("task_route_classifier", "planning", CapabilityMode.WORKBENCH),
    ]:
        registry.register(
            CapabilityManifest(
                capability_id=capability_id,
                version="0.1.0",
                mode=mode,
                canonical_owner=owner,
                risk_ceiling="READ",
                side_effect_class="none",
                interfaces=[capability_id],
                rollback=[f"disable {capability_id} manifest"],
                tests=[f"test_{capability_id}"],
                receipts=[f"{capability_id}_receipt"],
            )
        )
    return registry
