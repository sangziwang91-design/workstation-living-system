from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
import json

from .db import Database
from .schemas import digest_json, new_id, utc_now
from .task_graph import TaskNode


class FailureClass(StrEnum):
    LOCAL = "LOCAL"
    UPSTREAM = "UPSTREAM"
    STRUCTURAL = "STRUCTURAL"
    POLICY = "POLICY"
    ENVIRONMENT = "ENVIRONMENT"


@dataclass(frozen=True, slots=True)
class FailureAttribution:
    attribution_id: str
    graph_id: str
    node_id: str
    lease_id: str
    failure_class: FailureClass
    error_signature: str
    reason: str
    created_at: str
    claim_ceiling: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failure_class"] = self.failure_class.value
        return data


class FailureAttributor:
    """Deterministic first-pass failure attribution for agentic task nodes."""

    def __init__(self, db: Database) -> None:
        self.db = db

    def classify(
        self,
        *,
        graph_id: str,
        node: TaskNode,
        lease_id: str,
        error: str,
    ) -> FailureAttribution:
        text = error.lower()
        failure_class: FailureClass
        reason: str
        if any(term in text for term in ("permission", "approval", "policy", "unauthorized", "forbidden")):
            failure_class = FailureClass.POLICY
            reason = "policy or approval boundary prevented the node outcome"
        elif any(term in text for term in ("timeout", "locked", "disk", "path", "not found", "environment")):
            failure_class = FailureClass.ENVIRONMENT
            reason = "local environment or resource condition prevented the node outcome"
        elif any(term in text for term in ("http", "api", "provider", "rate limit", "5xx", "upstream")):
            failure_class = FailureClass.UPSTREAM
            reason = "external provider or upstream dependency prevented the node outcome"
        elif any(term in text for term in ("dependency", "cycle", "schema", "contract", "invariant", "validation")):
            failure_class = FailureClass.STRUCTURAL
            reason = "task graph structure or contract prevented the node outcome"
        else:
            failure_class = FailureClass.LOCAL
            reason = "failure appears local to this node attempt"
        return FailureAttribution(
            attribution_id=new_id("failattr"),
            graph_id=graph_id,
            node_id=node.node_id,
            lease_id=lease_id,
            failure_class=failure_class,
            error_signature=digest_json(
                {
                    "graph_id": graph_id,
                    "node_id": node.node_id,
                    "failure_class": failure_class.value,
                    "error": error[:1000],
                }
            ),
            reason=reason,
            created_at=utc_now(),
            claim_ceiling=(
                "deterministic first-pass failure attribution only; not a repair, "
                "root-cause proof, or promotion signal"
            ),
        )

    def persist(self, attribution: FailureAttribution, *, connection: Any) -> None:
        payload = attribution.to_dict()
        connection.execute(
            """
            INSERT INTO agentic_failure_attributions(
                attribution_id,graph_id,node_id,lease_id,failure_class,
                error_signature,attribution_json,created_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                attribution.attribution_id,
                attribution.graph_id,
                attribution.node_id,
                attribution.lease_id,
                attribution.failure_class.value,
                attribution.error_signature,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                attribution.created_at,
            ),
        )
