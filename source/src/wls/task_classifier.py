from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any

from .schemas import RiskLevel, digest_json


class TaskKind(StrEnum):
    DETERMINISTIC = "deterministic"
    CODING = "coding"
    RESEARCH = "research"
    COMPUTER_USE = "computer_use"
    COMMUNICATION = "communication"
    SYSTEM_OPERATION = "system_operation"
    MIXED = "mixed"


class SideEffectClass(StrEnum):
    NONE = "none"
    REVERSIBLE = "reversible"
    EXTERNAL = "external"
    IRREVERSIBLE = "irreversible"
    UNKNOWN = "unknown"


class VerificationOracle(StrEnum):
    NONE = "none"
    DETERMINISTIC_TEST = "deterministic_test"
    SCHEMA = "schema"
    ARTIFACT_DIGEST = "artifact_digest"
    EXTERNAL_SOURCE = "external_source"
    HUMAN_REVIEW = "human_review"
    MULTI_MODEL_REVIEW = "multi_model_review"


class OrchestrationMode(StrEnum):
    TOOL_ONLY = "tool_only"
    DIRECT = "direct"
    PLANNER_EXECUTOR_REVIEWER = "planner_executor_reviewer"
    FAN_OUT_SYNTHESIZE = "fan_out_synthesize"
    DAG_READY_FRONTIER = "dag_ready_frontier"
    HUMAN_GATED = "human_gated"


_KIND_TAGS: dict[TaskKind, set[str]] = {
    TaskKind.CODING: {"code", "coding", "repo", "github", "test", "refactor", "bug"},
    TaskKind.RESEARCH: {"research", "web", "sources", "paper", "compare", "investigate"},
    TaskKind.COMPUTER_USE: {"browser", "computer-use", "gui", "desktop", "click"},
    TaskKind.COMMUNICATION: {"email", "message", "publish", "post", "reply"},
    TaskKind.SYSTEM_OPERATION: {"system", "deploy", "install", "process", "service", "backup"},
    TaskKind.DETERMINISTIC: {"calculate", "convert", "format", "validate-schema", "hash"},
}

_RISK_ORDER = {
    RiskLevel.READ: 0,
    RiskLevel.REVERSIBLE_WRITE: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.IRREVERSIBLE: 3,
}

_SIDE_EFFECT_RISK = {
    SideEffectClass.NONE: RiskLevel.READ,
    SideEffectClass.REVERSIBLE: RiskLevel.REVERSIBLE_WRITE,
    SideEffectClass.EXTERNAL: RiskLevel.HIGH,
    SideEffectClass.UNKNOWN: RiskLevel.HIGH,
    SideEffectClass.IRREVERSIBLE: RiskLevel.IRREVERSIBLE,
}


@dataclass(slots=True)
class TaskRouteDescriptor:
    """Planner-owned task facts used to produce an orchestration route candidate."""

    task_id: str
    title: str
    goal: str
    tags: frozenset[str] = frozenset()
    estimated_steps: int = 1
    declared_risk: RiskLevel = RiskLevel.READ
    side_effect_class: SideEffectClass = SideEffectClass.NONE
    verification_oracles: tuple[VerificationOracle, ...] = ()
    allow_parallel: bool = False
    owner_authorization_required: bool = False
    max_cost_usd: float = 0.0
    max_wall_seconds: int = 300
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.task_id.strip():
            raise ValueError("task_id is required")
        if not self.title.strip() or not self.goal.strip():
            raise ValueError("title and goal are required")
        if self.estimated_steps < 1:
            raise ValueError("estimated_steps must be >= 1")
        if self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be >= 0")
        if self.max_wall_seconds < 1:
            raise ValueError("max_wall_seconds must be >= 1")


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Read-only orchestration recommendation; it is not an executable plan."""

    task_kind: TaskKind
    mode: OrchestrationMode
    roles: tuple[str, ...]
    max_workers: int
    requires_owner_gate: bool
    effective_risk: RiskLevel
    rationale: tuple[str, ...]
    uncertainty: str
    authority: str = "route_candidate_only"
    canonical_owner: str = "planning"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["task_kind"] = self.task_kind.value
        data["mode"] = self.mode.value
        data["effective_risk"] = self.effective_risk.value
        data["route_digest"] = digest_json(
            {
                "task_kind": data["task_kind"],
                "mode": data["mode"],
                "roles": data["roles"],
                "max_workers": data["max_workers"],
                "requires_owner_gate": data["requires_owner_gate"],
                "effective_risk": data["effective_risk"],
                "authority": data["authority"],
                "canonical_owner": data["canonical_owner"],
            }
        )
        return data


class TaskClassifier:
    """Deterministic route classifier that cannot authorize execution.

    Model hints may suggest a kind, but this classifier is deliberately
    monotonic on risk: hints and side-effect declarations can raise the route
    risk, never lower the planner-declared floor.
    """

    def classify(self, task: TaskRouteDescriptor) -> RouteDecision:
        task.validate()
        reasons: list[str] = []
        kind = self._kind(task, reasons)
        effective_risk = self._max_risk(
            task.declared_risk, _SIDE_EFFECT_RISK[task.side_effect_class]
        )
        owner_gate = (
            task.owner_authorization_required
            or effective_risk in {RiskLevel.HIGH, RiskLevel.IRREVERSIBLE}
            or task.side_effect_class is SideEffectClass.UNKNOWN
        )
        uncertainty = self._uncertainty(task)
        mode: OrchestrationMode
        roles: tuple[str, ...]
        workers: int

        if owner_gate:
            mode = OrchestrationMode.HUMAN_GATED
            roles = ("planner", "executor", "verifier", "owner")
            workers = 1
            reasons.append("policy-relevant risk or unknown side effects require an owner gate")
        elif kind is TaskKind.DETERMINISTIC and task.estimated_steps <= 3:
            mode = OrchestrationMode.TOOL_ONLY
            roles = ("deterministic_executor", "verifier")
            workers = 1
            reasons.append("deterministic oracle available; orchestration overhead is unnecessary")
        elif task.allow_parallel and kind is TaskKind.RESEARCH:
            mode = OrchestrationMode.FAN_OUT_SYNTHESIZE
            roles = ("planner", "parallel_researchers", "synthesizer", "source_verifier")
            workers = min(8, max(2, task.estimated_steps // 2))
            reasons.append("independent read-only research slices can be processed in parallel")
        elif task.allow_parallel and task.estimated_steps >= 7:
            mode = OrchestrationMode.DAG_READY_FRONTIER
            roles = ("manager", "specialist_workers", "verifier", "integrator")
            workers = min(8, max(2, task.estimated_steps // 3))
            reasons.append("long task with independent branches fits a bounded DAG frontier")
        elif (
            kind in {TaskKind.CODING, TaskKind.SYSTEM_OPERATION, TaskKind.MIXED}
            or task.estimated_steps >= 4
        ):
            mode = OrchestrationMode.PLANNER_EXECUTOR_REVIEWER
            roles = ("planner", "executor", "reviewer")
            workers = 2
            reasons.append("task requires separation between construction and acceptance")
        else:
            mode = OrchestrationMode.DIRECT
            roles = ("executor", "verifier")
            workers = 1
            reasons.append("bounded low-risk task does not justify extra orchestration")

        return RouteDecision(
            task_kind=kind,
            mode=mode,
            roles=roles,
            max_workers=workers,
            requires_owner_gate=owner_gate,
            effective_risk=effective_risk,
            rationale=tuple(reasons),
            uncertainty=uncertainty,
        )

    def _kind(self, task: TaskRouteDescriptor, reasons: list[str]) -> TaskKind:
        hint = str(task.metadata.get("kind_hint", "")).strip().lower()
        for kind in TaskKind:
            if hint == kind.value:
                reasons.append(f"explicit kind_hint={hint}")
                return kind

        tags = {tag.lower() for tag in task.tags}
        matches = [kind for kind, candidates in _KIND_TAGS.items() if tags & candidates]
        if len(matches) == 1:
            reasons.append(f"task tags map to {matches[0].value}")
            return matches[0]
        if len(matches) > 1:
            reasons.append("task tags span multiple capability classes")
            return TaskKind.MIXED
        if task.verification_oracles and all(
            item
            in {
                VerificationOracle.DETERMINISTIC_TEST,
                VerificationOracle.SCHEMA,
                VerificationOracle.ARTIFACT_DIGEST,
            }
            for item in task.verification_oracles
        ):
            reasons.append("all declared oracles are deterministic")
            return TaskKind.DETERMINISTIC
        reasons.append("no decisive kind evidence; route as mixed rather than guess")
        return TaskKind.MIXED

    @staticmethod
    def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
        return left if _RISK_ORDER[left] >= _RISK_ORDER[right] else right

    @staticmethod
    def _uncertainty(task: TaskRouteDescriptor) -> str:
        if not task.verification_oracles:
            return "HIGH"
        strong = {
            VerificationOracle.DETERMINISTIC_TEST,
            VerificationOracle.SCHEMA,
            VerificationOracle.ARTIFACT_DIGEST,
        }
        if all(item in strong for item in task.verification_oracles):
            return "LOW"
        if VerificationOracle.HUMAN_REVIEW in task.verification_oracles:
            return "MEDIUM"
        return "MEDIUM"
