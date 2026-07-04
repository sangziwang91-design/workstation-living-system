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

    def capability_card(self) -> dict[str, Any]:
        payload = self.to_dict()
        return {
            "schema_version": "wls.worker_card.v1",
            "worker_id": self.worker_id,
            "worker_type": self.worker_type,
            "label": self.label,
            "allowed_domains": list(self.allowed_domains),
            "max_risk": self.max_risk.value,
            "external_tools_enabled": self.external_tools_enabled,
            "writes_enabled": self.writes_enabled,
            "auth_scheme": str(self.metadata.get("auth_scheme", "none")),
            "sandbox": dict(self.metadata.get("sandbox", {})),
            "version": str(self.metadata.get("version", "unversioned")),
            "metadata_digest": digest_json(self.metadata),
            "profile_digest": payload["profile_digest"],
            "secret_material_present": False,
            "canonical_completion_authority": "LivingSystem.AgenticHarness",
        }

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

    def register_profile(
        self,
        profile: WorkerProfile,
        *,
        connection: Any | None = None,
        status: str = "ACTIVE",
        reason: str = "agentic worker profile registration",
    ) -> dict[str, Any]:
        if status not in {"ACTIVE", "STALE", "DISABLED"}:
            raise ValueError("worker profile status must be ACTIVE, STALE, or DISABLED")
        if connection is None:
            with self.db.transaction() as active_connection:
                return self.register_profile(
                    profile,
                    connection=active_connection,
                    status=status,
                    reason=reason,
                )
        payload = profile.to_dict()
        now = utc_now()
        connection.execute(
            """
            INSERT INTO agentic_worker_profiles(
                worker_id,profile_json,status,registered_at,updated_at
            ) VALUES (?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (
                profile.worker_id,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                status,
                now,
                now,
            ),
        )
        receipt = {
            "worker_id": profile.worker_id,
            "status": status,
            "profile_digest": payload["profile_digest"],
            "reason": reason,
            "claim_ceiling": (
                "worker profile registration only; no worker execution, "
                "external authority, or delegated memory/goal ownership"
            ),
        }
        self.ledger.append("agentic_worker_profile_registered", receipt, connection)
        return receipt

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

    def propose_candidates_for_node(
        self,
        *,
        graph: TaskGraph,
        node: TaskNode,
        reason: str,
        connection: Any,
        limit: int = 5,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("worker candidate arbitration reason is required")
        if limit < 1:
            raise ValueError("worker candidate arbitration limit must be >= 1")
        domain = str(node.metadata.get("domain", "MIXED")).upper()
        rows = connection.execute(
            """
            SELECT worker_id,profile_json,status,updated_at
            FROM agentic_worker_profiles
            ORDER BY worker_id ASC
            """
        ).fetchall()
        eligible: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for row in rows:
            profile = WorkerProfile.from_dict(json.loads(str(row["profile_json"])))
            status = str(row["status"])
            card = profile.capability_card()
            reasons: list[str] = []
            if status != "ACTIVE":
                reasons.append(f"status={status}")
            if domain not in profile.allowed_domains:
                reasons.append(f"domain_not_allowed={domain}")
            risk_headroom = _RISK_ORDER[profile.max_risk] - _RISK_ORDER[node.risk]
            if risk_headroom < 0:
                reasons.append(
                    f"max_risk={profile.max_risk.value}<node_risk={node.risk.value}"
                )
            candidate = {
                "worker_id": profile.worker_id,
                "worker_type": profile.worker_type,
                "label": profile.label,
                "status": status,
                "card": card,
                "card_digest": digest_json(card),
                "domain": domain,
                "node_risk": node.risk.value,
                "max_risk": profile.max_risk.value,
                "risk_headroom": risk_headroom,
                "external_tools_enabled": profile.external_tools_enabled,
                "writes_enabled": profile.writes_enabled,
                "updated_at": row["updated_at"],
            }
            if reasons:
                rejected.append({**candidate, "reasons": reasons})
            else:
                eligible.append(candidate)
        eligible.sort(
            key=lambda item: (
                int(item["risk_headroom"]),
                bool(item["writes_enabled"]),
                bool(item["external_tools_enabled"]),
                str(item["worker_id"]),
            )
        )
        receipt = {
            "arbitration_id": new_id("worker_arbitration"),
            "graph_id": graph.graph_id,
            "node_id": node.node_id,
            "reason": reason,
            "domain": domain,
            "node_risk": node.risk.value,
            "eligible_count": len(eligible),
            "rejected_count": len(rejected),
            "recommended_worker_id": eligible[0]["worker_id"] if eligible else None,
            "eligible_workers": eligible[:limit],
            "rejected_workers": rejected[:limit],
            "selection_executed": False,
            "lease_created": False,
            "direct_execution": False,
            "claim_ceiling": (
                "worker capability card arbitration receipt only; no lease creation, "
                "worker execution, external authority, or delegated planning is inferred"
            ),
        }
        self.ledger.append(
            "agentic_worker_candidate_arbitrated",
            receipt,
            connection,
        )
        return receipt

    def record_heartbeat(
        self,
        worker_id: str,
        *,
        status: str = "ACTIVE",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if status not in {"ACTIVE", "STALE", "DISABLED"}:
            raise ValueError("worker heartbeat status must be ACTIVE, STALE, or DISABLED")
        with self.db.transaction() as connection:
            row = connection.execute(
                """
                SELECT profile_json,status FROM agentic_worker_profiles
                WHERE worker_id=?
                """,
                (worker_id,),
            ).fetchone()
            if row is None:
                raise PermissionError(f"unknown worker profile: {worker_id}")
            heartbeat_at = utc_now()
            connection.execute(
                """
                UPDATE agentic_worker_profiles
                SET status=?,updated_at=?
                WHERE worker_id=?
                """,
                (status, heartbeat_at, worker_id),
            )
            profile = json.loads(str(row["profile_json"]))
            receipt = {
                "worker_id": worker_id,
                "previous_status": row["status"],
                "status": status,
                "heartbeat_at": heartbeat_at,
                "profile_digest": profile.get("profile_digest"),
                "details": details or {},
                "claim_ceiling": (
                    "worker heartbeat receipt only; no task lease, worker execution, "
                    "or external authority is inferred"
                ),
            }
            self.ledger.append("agentic_worker_heartbeat_recorded", receipt, connection)
            return receipt

    def mark_stale_workers(self, *, stale_before: str, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("stale worker reason is required")
        with self.db.transaction() as connection:
            rows = connection.execute(
                """
                SELECT worker_id,profile_json,updated_at FROM agentic_worker_profiles
                WHERE status='ACTIVE' AND updated_at < ?
                ORDER BY updated_at ASC
                """,
                (stale_before,),
            ).fetchall()
            stale_workers = []
            now = utc_now()
            for row in rows:
                profile = json.loads(str(row["profile_json"]))
                connection.execute(
                    """
                    UPDATE agentic_worker_profiles
                    SET status='STALE',updated_at=?
                    WHERE worker_id=?
                    """,
                    (now, row["worker_id"]),
                )
                stale_workers.append(
                    {
                        "worker_id": row["worker_id"],
                        "previous_updated_at": row["updated_at"],
                        "profile_digest": profile.get("profile_digest"),
                    }
                )
            receipt = {
                "stale_before": stale_before,
                "reason": reason,
                "stale_count": len(stale_workers),
                "stale_workers": stale_workers,
                "direct_execution": False,
                "claim_ceiling": (
                    "worker lifecycle stale marking only; no lease cancellation, "
                    "worker execution, task retry, or authority transfer is inferred"
                ),
            }
            self.ledger.append("agentic_worker_lifecycle_marked_stale", receipt, connection)
            return receipt

    def arbitration_receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_worker_candidate_arbitrated'
            ORDER BY seq DESC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        receipts: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if isinstance(payload, dict):
                receipts.append({**payload, "created_at": row["created_at"]})
        return receipts

    def lifecycle_receipts(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT event_type,payload_json,created_at FROM evidence
            WHERE event_type IN (
                'agentic_worker_profile_registered',
                'agentic_worker_heartbeat_recorded',
                'agentic_worker_lifecycle_marked_stale'
            )
            ORDER BY seq DESC
            LIMIT ?
            """,
            (max(0, int(limit)),),
        )
        receipts: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if isinstance(payload, dict):
                receipts.append(
                    {
                        **payload,
                        "event_type": row["event_type"],
                        "created_at": row["created_at"],
                    }
                )
        return receipts

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
