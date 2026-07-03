from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import RiskLevel, TaskDomain, digest_json, new_id, utc_now
from .task_graph import TaskGraph, TaskNode


_RISK_ORDER = {
    RiskLevel.READ: 0,
    RiskLevel.REVERSIBLE_WRITE: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.IRREVERSIBLE: 3,
}


@dataclass(frozen=True, slots=True)
class WorkerProfile:
    worker_id: str
    worker_type: str
    label: str
    allowed_domains: list[str]
    max_risk: RiskLevel = RiskLevel.READ
    external_tools_enabled: bool = False
    writes_enabled: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["max_risk"] = self.max_risk.value
        data["profile_digest"] = digest_json(
            {
                "worker_id": self.worker_id,
                "worker_type": self.worker_type,
                "allowed_domains": self.allowed_domains,
                "max_risk": self.max_risk.value,
                "external_tools_enabled": self.external_tools_enabled,
                "writes_enabled": self.writes_enabled,
            }
        )
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkerProfile:
        data = dict(payload)
        data.pop("profile_digest", None)
        data["max_risk"] = RiskLevel(str(data.get("max_risk", RiskLevel.READ.value)))
        return cls(**data)


class WorkerRegistry:
    """Local deterministic worker profile registry under WLS authority."""

    def __init__(self, db: Database, ledger: EvidenceLedger) -> None:
        self.db = db
        self.ledger = ledger

    @staticmethod
    def default_profiles() -> list[WorkerProfile]:
        return [
            WorkerProfile(
                worker_id="readonly-inspector",
                worker_type="LOCAL_SHADOW",
                label="Read-only inspector",
                allowed_domains=[domain.value for domain in TaskDomain],
                max_risk=RiskLevel.READ,
                metadata={"execution_mode": "shadow_receipt_only"},
            ),
            WorkerProfile(
                worker_id="planner-shadow",
                worker_type="LOCAL_SHADOW",
                label="Planner shadow compiler",
                allowed_domains=[TaskDomain.CODE.value, TaskDomain.MIXED.value],
                max_risk=RiskLevel.REVERSIBLE_WRITE,
                metadata={"execution_mode": "plan_only_no_write"},
            ),
            WorkerProfile(
                worker_id="owner-gated-executor-shadow",
                worker_type="LOCAL_SHADOW",
                label="Owner gated executor shadow",
                allowed_domains=[domain.value for domain in TaskDomain],
                max_risk=RiskLevel.IRREVERSIBLE,
                metadata={"execution_mode": "requires_owner_approval_before_action"},
            ),
        ]

    def ensure_defaults(self, *, connection: Any) -> list[dict[str, Any]]:
        inserted: list[dict[str, Any]] = []
        for profile in self.default_profiles():
            payload = profile.to_dict()
            now = utc_now()
            connection.execute(
                """
                INSERT INTO agentic_worker_profiles(
                    worker_id,profile_json,status,registered_at,updated_at
                ) VALUES (?,?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET
                    profile_json=excluded.profile_json,
                    status='ACTIVE',
                    updated_at=excluded.updated_at
                """,
                (
                    profile.worker_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    "ACTIVE",
                    now,
                    now,
                ),
            )
            inserted.append(payload)
        self.ledger.append(
            "agentic_worker_registry_ready",
            {
                "worker_ids": [item["worker_id"] for item in inserted],
                "claim_ceiling": (
                    "local worker profiles only; no external worker, model call, "
                    "tool execution, or delegated authority"
                ),
            },
            connection,
        )
        return inserted

    def profile(self, worker_id: str, *, connection: Any | None = None) -> WorkerProfile:
        query = (
            "SELECT profile_json FROM agentic_worker_profiles "
            "WHERE worker_id=? AND status='ACTIVE'"
        )
        row = (
            connection.execute(query, (worker_id,)).fetchone()
            if connection is not None
            else self.db.query_one(query, (worker_id,))
        )
        if row is None:
            raise PermissionError(f"unknown or inactive worker profile: {worker_id}")
        return WorkerProfile.from_dict(json.loads(str(row["profile_json"])))

    def validate_for_node(
        self,
        worker_id: str,
        node: TaskNode,
        *,
        graph: TaskGraph,
        connection: Any,
    ) -> dict[str, Any]:
        profile = self.profile(worker_id, connection=connection)
        domain = str(node.metadata.get("domain", "MIXED")).upper()
        if domain not in profile.allowed_domains:
            raise PermissionError(f"worker {worker_id} is not allowed for domain {domain}")
        if _RISK_ORDER[node.risk] > _RISK_ORDER[profile.max_risk]:
            raise PermissionError(
                f"worker {worker_id} max risk {profile.max_risk.value} below node risk {node.risk.value}"
            )
        assignment = {
            "assignment_id": new_id("assign"),
            "graph_id": graph.graph_id,
            "node_id": node.node_id,
            "worker_id": worker_id,
            "worker_type": profile.worker_type,
            "worker_profile_digest": profile.to_dict()["profile_digest"],
            "assigned_at": utc_now(),
            "claim_ceiling": "profile assignment only; no worker execution",
        }
        connection.execute(
            """
            INSERT INTO agentic_worker_assignments(
                assignment_id,graph_id,node_id,worker_id,assignment_json,created_at
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                assignment["assignment_id"],
                graph.graph_id,
                node.node_id,
                worker_id,
                json.dumps(assignment, ensure_ascii=False, sort_keys=True),
                assignment["assigned_at"],
            ),
        )
        self.ledger.append("agentic_worker_profile_assigned", assignment, connection)
        return assignment

    def latest_receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT worker_id,profile_json,status,updated_at
            FROM agentic_worker_profiles
            ORDER BY updated_at DESC, worker_id ASC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        return [dict(row) for row in rows]
