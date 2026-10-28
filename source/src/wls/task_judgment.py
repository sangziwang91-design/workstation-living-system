"""Read-only task judgment on top of the canonical WLS admission and route organs.

This layer decides whether a task has enough capability and verification evidence
to enter planning. It does not authorize tools, change policy, or grade itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any

from .schemas import RiskLevel, digest_json
from .task_admission import TaskAdmissionClassifier
from .task_classifier import (
    OrchestrationMode, SideEffectClass, TaskClassifier,
    TaskKind, TaskRouteDescriptor, VerificationOracle,
)

RANK = {RiskLevel.READ: 0, RiskLevel.REVERSIBLE_WRITE: 1,
        RiskLevel.HIGH: 2, RiskLevel.IRREVERSIBLE: 3}
DEFAULT_CAPABILITIES: dict[TaskKind, frozenset[str]] = {
    TaskKind.CODING: frozenset({"code_workspace", "test_runner"}),
    TaskKind.RESEARCH: frozenset({"source_lookup"}),
    TaskKind.COMPUTER_USE: frozenset({"desktop"}),
    TaskKind.COMMUNICATION: frozenset({"messaging"}),
    TaskKind.SYSTEM_OPERATION: frozenset({"system_tools"}),
    TaskKind.DETERMINISTIC: frozenset({"deterministic_tool"}),
    TaskKind.MIXED: frozenset(),
}
# Narrow multilingual action cues supplement the older English-centric
# admission parser. They provide floors, never permission to act.
RISK_CUES = {
    RiskLevel.IRREVERSIBLE: ("删除生产数据库", "永久删除", "正式发布", "公开发布", "部署生产环境",
                             "转账付款", "delete production", "wire payment"),
    RiskLevel.HIGH: ("发送外部邮件", "批量发送邮件", "上传客户数据",
                     "读取密钥", "查看密钥", "production secret"),
    RiskLevel.REVERSIBLE_WRITE: ("修改仓库", "提交补丁", "修复代码", "创建文件", "更新配置"),
}


class JudgmentStatus(StrEnum):
    READY_TO_PLAN = "READY_TO_PLAN"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    CAPABILITY_MISSING = "CAPABILITY_MISSING"
    EVIDENCE_MISSING = "EVIDENCE_MISSING"
    TASK_UNDERSPECIFIED = "TASK_UNDERSPECIFIED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


@dataclass(slots=True)
class TaskJudgmentInput:
    task_id: str
    request: str
    tags: frozenset[str] = frozenset()
    estimated_steps: int = 1
    declared_risk: RiskLevel = RiskLevel.READ
    side_effect_class: SideEffectClass = SideEffectClass.NONE
    verification_oracles: tuple[VerificationOracle, ...] = ()
    required_capabilities: frozenset[str] = frozenset()
    available_capabilities: frozenset[str] = frozenset()
    required_evidence: frozenset[str] = frozenset()
    available_evidence: frozenset[str] = frozenset()
    allow_parallel: bool = False
    owner_authorization_required: bool = False
    acceptance_defined: bool = True
    model_kind_hint: str = ""
    max_cost_usd: float = 0.0
    estimated_cost_usd: float = 0.0
    max_wall_seconds: int = 300
    estimated_wall_seconds: int = 1


@dataclass(frozen=True, slots=True)
class TaskJudgment:
    status: JudgmentStatus
    task_id: str
    task_kind: TaskKind
    route_mode: OrchestrationMode
    effective_risk: RiskLevel
    owner_gate: bool
    missing_capabilities: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    authority: str = "judgment_candidate_only"

    def to_dict(self) -> dict[str, Any]:
        result = {
            "task_id": self.task_id, "status": self.status.value,
            "task_kind": self.task_kind.value, "route_mode": self.route_mode.value,
            "effective_risk": self.effective_risk.value,
            "owner_gate": self.owner_gate,
            "missing_capabilities": list(self.missing_capabilities),
            "missing_evidence": list(self.missing_evidence),
            "blockers": list(self.blockers),
            "authority": self.authority,
            "claim_ceiling": "read_only_route_and_readiness_not_execution_or_model_intelligence",
        }
        result["judgment_sha256"] = digest_json(result)
        return result


class TaskJudgmentEngine:
    """Deterministic readiness gate; decisions require external task outcomes."""

    def judge(self, task: TaskJudgmentInput) -> TaskJudgment:
        if not task.task_id.strip() or not task.request.strip():
            raise ValueError("task identity and request are required")
        if (not isfinite(task.estimated_cost_usd) or not isfinite(task.max_cost_usd)
                or task.estimated_cost_usd < 0 or task.max_cost_usd < 0
                or task.estimated_wall_seconds < 1):
            raise ValueError("invalid cost or runtime estimate")
        intent = TaskAdmissionClassifier().admit(task.request)
        floor = max((task.declared_risk, intent.risk_floor),
                    key=lambda risk: RANK[risk])
        lower = task.request.casefold()
        for level, phrases in RISK_CUES.items():
            if any(phrase in lower for phrase in phrases) and RANK[level] > RANK[floor]:
                floor = level
        tag_kinds = {
            kind for kind, words in {
                TaskKind.CODING: {"coding", "code", "repo", "github", "test", "bug"},
                TaskKind.RESEARCH: {"research", "sources", "paper", "web"},
                TaskKind.COMPUTER_USE: {"browser", "gui", "desktop"},
                TaskKind.COMMUNICATION: {"email", "message", "publish"},
                TaskKind.SYSTEM_OPERATION: {"system", "deploy", "install", "backup"},
                TaskKind.DETERMINISTIC: {"calculate", "hash", "format"},
            }.items() if words & set(task.tags)
        }
        hint_conflict = bool(task.model_kind_hint and (
            not tag_kinds or task.model_kind_hint not in {kind.value for kind in tag_kinds}
        ))
        descriptor = TaskRouteDescriptor(
            task_id=task.task_id, title=task.request[:120], goal=task.request,
            tags=task.tags, estimated_steps=task.estimated_steps,
            declared_risk=floor, side_effect_class=task.side_effect_class,
            verification_oracles=task.verification_oracles,
            allow_parallel=task.allow_parallel,
            owner_authorization_required=task.owner_authorization_required,
            max_cost_usd=task.max_cost_usd, max_wall_seconds=task.max_wall_seconds,
            metadata={"kind_hint": "" if hint_conflict else task.model_kind_hint},
        )
        route = TaskClassifier().classify(descriptor)
        inferred = frozenset().union(
            *(DEFAULT_CAPABILITIES[k] for k in tag_kinds)
        ) if tag_kinds else DEFAULT_CAPABILITIES[route.task_kind]
        required = task.required_capabilities | inferred
        missing_caps = tuple(sorted(required - task.available_capabilities))
        missing_evidence = tuple(sorted(task.required_evidence - task.available_evidence))
        blockers: list[str] = []
        if hint_conflict:
            blockers.append("model_hint_conflicts_with_task_tags")
        if route.task_kind is TaskKind.MIXED and not tag_kinds:
            blockers.append("no_independently_classified_task_kind")
        if not task.acceptance_defined:
            blockers.append("acceptance_criteria_not_defined")
        if not task.verification_oracles or task.verification_oracles == (VerificationOracle.NONE,):
            blockers.append("no_verification_oracle")
        if missing_caps:
            blockers.append("required_capabilities_unavailable")
        if (task.estimated_cost_usd > task.max_cost_usd
                or task.estimated_wall_seconds > task.max_wall_seconds):
            blockers.append("task_budget_exceeded")
        if missing_evidence:
            blockers.append("required_evidence_missing")
        if route.requires_owner_gate:
            blockers.append("owner_approval_required")

        if route.requires_owner_gate:
            status = JudgmentStatus.APPROVAL_REQUIRED
        elif (hint_conflict or not task.acceptance_defined
              or "no_verification_oracle" in blockers
              or "no_independently_classified_task_kind" in blockers):
            status = JudgmentStatus.TASK_UNDERSPECIFIED
        elif "task_budget_exceeded" in blockers:
            status = JudgmentStatus.BUDGET_EXCEEDED
        elif missing_caps:
            status = JudgmentStatus.CAPABILITY_MISSING
        elif missing_evidence:
            status = JudgmentStatus.EVIDENCE_MISSING
        else:
            status = JudgmentStatus.READY_TO_PLAN

        return TaskJudgment(status, task.task_id, route.task_kind, route.mode,
                            route.effective_risk, route.requires_owner_gate,
                            missing_caps, missing_evidence, tuple(blockers))
