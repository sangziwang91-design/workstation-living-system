from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .schemas import TaskNodeStatus, digest_json, new_id, utc_now


class RecoveryAction(StrEnum):
    REPLAY = "REPLAY"
    QUARANTINE = "QUARANTINE"
    ESCALATE = "ESCALATE"
    SKIP_RECOVERED = "SKIP_RECOVERED"


@dataclass(slots=True)
class GraphCheckpoint:
    checkpoint_id: str
    graph_id: str
    node_states: dict[str, str]
    active_leases: dict[str, str]
    evidence_digests: list[str]
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checkpoint_id": self.checkpoint_id,
            "graph_id": self.graph_id,
            "node_states": self.node_states,
            "active_leases": self.active_leases,
        }


@dataclass(slots=True)
class RecoveryDecision:
    node_id: str
    action: RecoveryAction
    reason: str
    evidence_ref: str = ""


@dataclass(slots=True)
class SideEffectRecord:
    record_id: str
    graph_id: str
    node_id: str
    side_effect_class: str
    idempotency_key: str
    status: str
    dispatched_at: str
    settled_at: str | None = None

    @property
    def is_unknown(self) -> bool:
        return self.status == "UNKNOWN"

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "graph_id": self.graph_id,
            "node_id": self.node_id,
            "side_effect_class": self.side_effect_class,
            "status": self.status,
        }


class GraphRecoveryEngine:
    """Durable graph recovery: reconstructs graph state after crash without
    repeating unknown side effects or losing accepted artifacts.
    """

    def assess_node(
        self,
        node_id: str,
        node_status: str,
        *,
        has_side_effect: bool = False,
        side_effect_status: str = "UNKNOWN",
    ) -> RecoveryDecision:
        if node_status == TaskNodeStatus.SUCCEEDED.value:
            return RecoveryDecision(
                node_id=node_id,
                action=RecoveryAction.SKIP_RECOVERED,
                reason="node already succeeded",
            )

        if node_status == TaskNodeStatus.LEASED.value and has_side_effect:
            if side_effect_status == "CONFIRMED":
                return RecoveryDecision(
                    node_id=node_id,
                    action=RecoveryAction.REPLAY,
                    reason="side effect confirmed, safe to replay lease",
                )
            return RecoveryDecision(
                node_id=node_id,
                action=RecoveryAction.QUARANTINE,
                reason="unknown side effect must be resolved before replay",
            )

        if node_status == TaskNodeStatus.LEASED.value:
            return RecoveryDecision(
                node_id=node_id,
                action=RecoveryAction.REPLAY,
                reason="no side effect, safe to re-acquire lease",
            )

        if node_status in (TaskNodeStatus.FAILED.value, TaskNodeStatus.BLOCKED.value):
            return RecoveryDecision(
                node_id=node_id,
                action=RecoveryAction.ESCALATE,
                reason=f"node in terminal state: {node_status}",
            )

        return RecoveryDecision(
            node_id=node_id,
            action=RecoveryAction.REPLAY,
            reason=f"node in pending state: {node_status}",
        )

    def recover_graph(
        self,
        graph_id: str,
        node_states: dict[str, str],
        side_effects: dict[str, SideEffectRecord] | None = None,
    ) -> list[RecoveryDecision]:
        effects = side_effects or {}
        decisions: list[RecoveryDecision] = []
        for node_id, status in node_states.items():
            se = effects.get(node_id)
            has_se = se is not None
            se_status = se.status if se else "UNKNOWN"
            decision = self.assess_node(
                node_id, status,
                has_side_effect=has_se,
                side_effect_status=se_status,
            )
            decisions.append(decision)
        return decisions
