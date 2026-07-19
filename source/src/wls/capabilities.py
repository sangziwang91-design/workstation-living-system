from __future__ import annotations

import builtins
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


class UserCapabilityLabel(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    INTERNAL = "INTERNAL"
    NOT_PRODUCTIZED = "NOT_PRODUCTIZED"


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

    def list(self) -> builtins.list[dict[str, Any]]:
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

    def detect_duplicate_authorities(self) -> builtins.list[dict[str, Any]]:
        findings: builtins.list[dict[str, Any]] = []
        owner_map: dict[str, list[str]] = {}
        for manifest in self._items.values():
            owner_map.setdefault(manifest.canonical_owner, []).append(manifest.capability_id)
        for owner, caps in owner_map.items():
            if owner not in CANONICAL_AUTHORITIES:
                findings.append({
                    "severity": "critical",
                    "owner": owner,
                    "capabilities": caps,
                    "detail": f"owner {owner} not in CANONICAL_AUTHORITIES",
                })
        return findings

    def validate_authority_model(self) -> dict[str, Any]:
        duplicates = self.detect_duplicate_authorities()
        return {
            "valid": len(duplicates) == 0,
            "duplicate_authorities": duplicates,
            "canonical_count": len(CANONICAL_AUTHORITIES),
            "registered_count": len(self._items),
            "authority_model": "canonical WLS owners only",
        }


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
        ("execution_trace_candidate", "evidence", CapabilityMode.PROJECTION),
        ("loop_control_candidate", "planning", CapabilityMode.WORKBENCH),
        ("experiment_decision_candidate", "evolution", CapabilityMode.WORKBENCH),
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


def ordinary_user_capability_map() -> dict[str, Any]:
    """Return the user-facing truth table for current WLS product capabilities."""
    items = [
        {
            "id": "local_workspace_init",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Initialize a local workspace",
            "user_job": "Create a private local WLS home and config.",
            "entrypoints": ["wls init"],
            "limits": [],
        },
        {
            "id": "health_and_status",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Health and status checks",
            "user_job": "Know whether the runtime is safe, paused, killed, or healthy.",
            "entrypoints": ["wls health", "wls status", "Owner Console Dashboard"],
            "limits": ["status is verbose for casual users"],
        },
        {
            "id": "goal_tracking",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Goal and project tracking",
            "user_job": "Record long-lived work as goals/projects/tasks instead of losing it in chat.",
            "entrypoints": ["wls add-goal", "Owner Console Project / Task"],
            "limits": ["CLI creates goals; polished task editing remains UI-limited"],
        },
        {
            "id": "single_cycle_run",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Run one cycle",
            "user_job": "Run one observe/plan/act cycle and leave evidence.",
            "entrypoints": ["wls once", "Owner Console Run One Cycle"],
            "limits": ["ordinary users still need clearer explanation of what the planner did"],
        },
        {
            "id": "approval_recovery",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Approval and recovery gates",
            "user_job": "Approve, reject, resume, or resolve risky/unknown actions.",
            "entrypoints": ["wls approve", "wls reject", "wls resume-action", "wls resolve-unknown", "Owner Console Approvals"],
            "limits": ["requires an existing pending action to become meaningful"],
        },
        {
            "id": "evidence_export",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Evidence recording and export",
            "user_job": "Record structured evidence and export a portable audit trail.",
            "entrypoints": ["wls add-event", "wls export-evidence", "Owner Console Library"],
            "limits": ["export format is newline JSON records, not a polished report"],
        },
        {
            "id": "integrity_verification",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Evidence integrity verification",
            "user_job": "Check whether evidence and database integrity can be trusted.",
            "entrypoints": ["wls verify", "wls self-check"],
            "limits": [],
        },
        {
            "id": "runtime_stop_controls",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Pause, resume, kill, reset-kill",
            "user_job": "Keep owner authority over automation.",
            "entrypoints": ["wls pause", "wls resume", "wls kill", "wls reset-kill"],
            "limits": ["powerful controls; UI surface is still minimal"],
        },
        {
            "id": "garbage_review",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Garbage review and quarantine",
            "user_job": "Find cleanup candidates, quarantine with approval, and clear with second approval.",
            "entrypoints": ["wls garbage-audit", "wls garbage-clear-quarantine", "Owner Console Failures"],
            "limits": ["not a full disk cleaner"],
        },
        {
            "id": "performance_retention_soak",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Performance, retention, and soak audits",
            "user_job": "Check whether runtime operations remain within local budgets.",
            "entrypoints": ["wls performance-audit", "wls retention-audit", "wls soak-audit"],
            "limits": ["mostly operator-facing, not a casual workflow"],
        },
        {
            "id": "owner_console",
            "label": UserCapabilityLabel.AVAILABLE.value,
            "name": "Owner Console",
            "user_job": "Use a loopback UI for dashboard, projects, runs, approvals, evidence, and failures.",
            "entrypoints": ["wls ui"],
            "limits": ["single-user local UI; not a SaaS dashboard"],
        },
        {
            "id": "longitudinal_measurement",
            "label": UserCapabilityLabel.PARTIAL.value,
            "name": "Longitudinal owner-task measurement",
            "user_job": "Track real task quality over time.",
            "entrypoints": ["wls longitudinal-start", "wls longitudinal-record", "wls longitudinal-report"],
            "limits": ["useful only after repeated real measurements"],
        },
        {
            "id": "memory_inspection",
            "label": UserCapabilityLabel.PARTIAL.value,
            "name": "Memory inspection",
            "user_job": "Inspect durable local memories.",
            "entrypoints": ["wls memories"],
            "limits": ["not yet a polished personal memory assistant"],
        },
        {
            "id": "world_model_inspection",
            "label": UserCapabilityLabel.PARTIAL.value,
            "name": "World/context fact inspection",
            "user_job": "Inspect durable facts WLS has inferred or observed.",
            "entrypoints": ["wls world"],
            "limits": ["readout is technical and not yet guided for ordinary users"],
        },
        {
            "id": "learned_skills",
            "label": UserCapabilityLabel.PARTIAL.value,
            "name": "Learned skills lifecycle",
            "user_job": "List and transition learned skills through gated states.",
            "entrypoints": ["wls skills", "wls skill"],
            "limits": ["no ordinary-user skill catalog or marketplace"],
        },
        {
            "id": "growth_cycle",
            "label": UserCapabilityLabel.INTERNAL.value,
            "name": "Growth/recovery experiments",
            "user_job": "Turn repeated failures into validated reusable improvements.",
            "entrypoints": ["wls growth-*"],
            "limits": ["operator/research workflow, not safe to market as a user feature yet"],
        },
        {
            "id": "cognition_dashboard",
            "label": UserCapabilityLabel.INTERNAL.value,
            "name": "Cognition/calibration dashboard",
            "user_job": "Inspect predictions, hypotheses, and calibration state.",
            "entrypoints": ["wls cognition"],
            "limits": ["diagnostic metrics; not evidence of subjective cognition"],
        },
        {
            "id": "browser_computer_control",
            "label": UserCapabilityLabel.NOT_PRODUCTIZED.value,
            "name": "Browser/computer control",
            "user_job": "Automate browser or desktop workflows.",
            "entrypoints": ["internal adapters and tests"],
            "limits": ["not exposed as a safe ordinary-user workflow"],
        },
        {
            "id": "multi_worker_agent_os",
            "label": UserCapabilityLabel.NOT_PRODUCTIZED.value,
            "name": "Multi-worker Agent OS",
            "user_job": "Lease work to multiple workers with trust and recovery.",
            "entrypoints": ["agentic harness tests"],
            "limits": ["architecture and tests exist; ordinary user product path is missing"],
        },
        {
            "id": "commercial_m7_readiness",
            "label": UserCapabilityLabel.INTERNAL.value,
            "name": "Commercial/M7 readiness audits",
            "user_job": "Gate local single-user release claims against evidence.",
            "entrypoints": ["wls commercial-readiness-audit", "wls external-product-audit", "wls m7-self-check"],
            "limits": ["internal/local readiness only; not third-party certification"],
        },
    ]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["label"]] = counts.get(item["label"], 0) + 1
    return {
        "schema_version": 1,
        "claim_ceiling": (
            "ordinary-user capability labels for local single-user WLS; labels "
            "must not be read as multi-user SaaS or third-party certification"
        ),
        "labels": {
            UserCapabilityLabel.AVAILABLE.value: "works as a real user/operator workflow",
            UserCapabilityLabel.PARTIAL.value: "implemented, but value or UX is incomplete",
            UserCapabilityLabel.INTERNAL.value: "real internal/operator capability, not a daily user feature",
            UserCapabilityLabel.NOT_PRODUCTIZED.value: "architecture/tests may exist, but no safe ordinary-user product path",
        },
        "counts": counts,
        "capabilities": items,
    }
