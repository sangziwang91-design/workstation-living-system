from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
import json

from .db import Database
from .context_manifest import ContextManifestBuilder
from .evidence import EvidenceLedger
from .schemas import RiskLevel, TaskNodeStatus, new_id, utc_now
from .task_admission import TaskAdmissionClassifier, TaskIntent
from .task_graph import TaskGraph, TaskGraphCompiler, TaskNode
from .worker_registry import WorkerRegistry


@dataclass(frozen=True, slots=True)
class NodeLease:
    lease_id: str
    graph_id: str
    node_id: str
    conflict_domain: str
    worker_id: str
    acquired_at: str
    expires_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "lease_id": self.lease_id,
            "graph_id": self.graph_id,
            "node_id": self.node_id,
            "conflict_domain": self.conflict_domain,
            "worker_id": self.worker_id,
            "acquired_at": self.acquired_at,
            "expires_at": self.expires_at,
        }


class AgenticHarness:
    """Complex task harness attached to the canonical WLS database and ledger."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        *,
        classifier: TaskAdmissionClassifier | None = None,
        compiler: TaskGraphCompiler | None = None,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.classifier = classifier or TaskAdmissionClassifier()
        self.compiler = compiler or TaskGraphCompiler()
        self.context_manifest = ContextManifestBuilder(db)
        self.worker_registry = WorkerRegistry(db, ledger)

    def admit_and_compile(
        self,
        raw_request: str,
        *,
        acceptance: list[str] | None = None,
        evidence_required: list[str] | None = None,
        rollback: list[str] | None = None,
        model_hints: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        intent = self.classifier.admit(
            raw_request,
            acceptance=acceptance,
            evidence_required=evidence_required,
            rollback=rollback,
            model_hints=model_hints,
        )
        graph = self.compiler.compile(intent)
        manifest = self.context_manifest.build(intent, graph)
        route = self._route(intent, graph, manifest=manifest)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO agentic_task_intents(
                    intent_id,raw_request,intent_json,route_json,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    intent.intent_id,
                    intent.raw_request,
                    json.dumps(intent.to_dict(), ensure_ascii=False, sort_keys=True),
                    json.dumps(route, ensure_ascii=False, sort_keys=True),
                    "COMPILED",
                    intent.created_at,
                    utc_now(),
                ),
            )
            connection.execute(
                """
                INSERT INTO agentic_task_graphs(
                    graph_id,intent_id,graph_json,status,created_at,updated_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    graph.graph_id,
                    intent.intent_id,
                    json.dumps(graph.snapshot(), ensure_ascii=False, sort_keys=True),
                    "READY",
                    graph.created_at,
                    graph.updated_at,
                ),
            )
            self.context_manifest.persist(manifest, connection=connection)
            self.worker_registry.ensure_defaults(connection=connection)
            self.ledger.append(
                "agentic_task_graph_compiled",
                {
                    "intent_id": intent.intent_id,
                    "graph_id": graph.graph_id,
                    "risk_floor": intent.risk_floor.value,
                    "owner_gate_required": intent.owner_gate_required,
                    "node_count": len(graph.nodes),
                    "graph_digest": graph.snapshot()["graph_digest"],
                    "claim_ceiling": "compiled task graph only; no worker execution or external action",
                    "context_manifest_id": manifest["manifest_id"],
                    "context_manifest_digest": manifest["manifest_digest"],
                },
                connection,
            )
        return {
            "intent": intent.to_dict(),
            "route": route,
            "graph": graph.snapshot(),
            "context_manifest": manifest,
        }

    def load_graph(self, graph_id: str) -> TaskGraph:
        row = self.db.query_one(
            "SELECT graph_json FROM agentic_task_graphs WHERE graph_id=?",
            (graph_id,),
        )
        if row is None:
            raise KeyError(f"unknown task graph: {graph_id}")
        return TaskGraph.from_snapshot(json.loads(str(row["graph_json"])))

    def acquire_ready_leases(
        self,
        graph_id: str,
        *,
        worker_id: str,
        limit: int = 1,
        ttl_seconds: int = 300,
        owner_authorized_node_ids: set[str] | None = None,
    ) -> list[NodeLease]:
        if limit < 1:
            raise ValueError("limit must be >= 1")
        if ttl_seconds < 1:
            raise ValueError("ttl_seconds must be >= 1")
        authorized = owner_authorized_node_ids or set()
        now = utc_now()
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=ttl_seconds)
        ).isoformat()
        leases: list[NodeLease] = []
        with self.db.transaction() as connection:
            self._expire_leases(connection, now)
            graph = self._load_graph_for_update(graph_id, connection)
            active_domains = {
                str(row["conflict_domain"])
                for row in connection.execute(
                    """
                    SELECT conflict_domain FROM agentic_node_leases
                    WHERE graph_id=? AND status='ACTIVE'
                    """,
                    (graph_id,),
                ).fetchall()
            }
            for node in graph.ready_frontier():
                if len(leases) >= limit:
                    break
                if node.conflict_domain in active_domains:
                    continue
                if node.risk in {RiskLevel.HIGH, RiskLevel.IRREVERSIBLE} and (
                    node.node_id not in authorized
                ):
                    graph.transition(node.node_id, TaskNodeStatus.WAITING_APPROVAL)
                    self.ledger.append(
                        "agentic_task_node_waiting_approval",
                        {
                            "graph_id": graph_id,
                            "node_id": node.node_id,
                            "risk": node.risk.value,
                            "conflict_domain": node.conflict_domain,
                        },
                        connection,
                    )
                    continue
                lease = self._lease_node(
                    graph,
                    node,
                    worker_id=worker_id,
                    acquired_at=now,
                    expires_at=expires_at,
                    connection=connection,
                )
                active_domains.add(node.conflict_domain)
                leases.append(lease)
            self._persist_graph(graph, connection)
        return leases

    def complete_node(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self.worker_registry.ensure_defaults(connection=connection)
            graph = self._load_graph_for_update(graph_id, connection)
            lease = self._active_lease(graph_id, node_id, lease_id, connection)
            node = graph.complete(node_id, result)
            connection.execute(
                """
                UPDATE agentic_node_leases
                SET status='RELEASED',released_at=?
                WHERE lease_id=?
                """,
                (utc_now(), lease_id),
            )
            self._persist_graph(graph, connection)
            payload = {
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "worker_id": lease["worker_id"],
                "result_digest": node.result_digest,
                "terminal": graph.terminal(),
            }
            self.ledger.append("agentic_task_node_completed", payload, connection)
            return payload

    def fail_node(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        error: str,
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self.worker_registry.ensure_defaults(connection=connection)
            graph = self._load_graph_for_update(graph_id, connection)
            lease = self._active_lease(graph_id, node_id, lease_id, connection)
            node = graph.fail(node_id, error)
            connection.execute(
                """
                UPDATE agentic_node_leases
                SET status='RELEASED',released_at=?
                WHERE lease_id=?
                """,
                (utc_now(), lease_id),
            )
            self._persist_graph(graph, connection)
            payload = {
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "worker_id": lease["worker_id"],
                "status": node.status.value,
                "error": error,
                "terminal": graph.terminal(),
            }
            self.ledger.append("agentic_task_node_failed", payload, connection)
            return payload

    @staticmethod
    def _route(
        intent: TaskIntent, graph: TaskGraph, *, manifest: dict[str, Any]
    ) -> dict[str, Any]:
        side_effect_nodes = [
            node.node_id
            for node in graph.nodes.values()
            if node.risk is not RiskLevel.READ
        ]
        return {
            "mode": "TASK_GRAPH",
            "operation": intent.operation.value,
            "domain": intent.domain.value,
            "parallelism": intent.parallelism.value,
            "risk_floor": intent.risk_floor.value,
            "owner_gate_required": intent.owner_gate_required,
            "max_workers": 1
            if intent.owner_gate_required
            else max(1, min(4, len(graph.nodes))),
            "side_effect_node_ids": side_effect_nodes,
            "policy_path": "canonical PolicyEngine/ApprovalManager before execution",
            "context_manifest": {
                "manifest_id": manifest["manifest_id"],
                "manifest_digest": manifest["manifest_digest"],
                "record_count": len(manifest["records"]),
            },
            "worker_registry": "LOCAL_PROFILE_REGISTRY_V1",
        }

    def _lease_node(
        self,
        graph: TaskGraph,
        node: TaskNode,
        *,
        worker_id: str,
        acquired_at: str,
        expires_at: str,
        connection: Any,
    ) -> NodeLease:
        lease_id = new_id("lease")
        assignment = self.worker_registry.validate_for_node(
            worker_id, node, graph=graph, connection=connection
        )
        graph.transition(node.node_id, TaskNodeStatus.LEASED)
        graph.nodes[node.node_id].lease_id = lease_id
        graph.nodes[node.node_id].worker_id = worker_id
        lease = NodeLease(
            lease_id=lease_id,
            graph_id=graph.graph_id,
            node_id=node.node_id,
            conflict_domain=node.conflict_domain,
            worker_id=worker_id,
            acquired_at=acquired_at,
            expires_at=expires_at,
        )
        connection.execute(
            """
            INSERT INTO agentic_node_leases(
                lease_id,graph_id,node_id,conflict_domain,worker_id,status,
                acquired_at,expires_at,released_at
            ) VALUES (?,?,?,?,?,?,?,?,NULL)
            """,
            (
                lease.lease_id,
                lease.graph_id,
                lease.node_id,
                lease.conflict_domain,
                lease.worker_id,
                "ACTIVE",
                lease.acquired_at,
                lease.expires_at,
            ),
        )
        self.ledger.append(
            "agentic_task_node_leased",
            {
                **lease.to_dict(),
                "claim_ceiling": "lease only; node execution remains external to this receipt",
                "worker_assignment_id": assignment["assignment_id"],
            },
            connection,
        )
        return lease

    def _load_graph_for_update(self, graph_id: str, connection: Any) -> TaskGraph:
        row = connection.execute(
            "SELECT graph_json FROM agentic_task_graphs WHERE graph_id=?",
            (graph_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"unknown task graph: {graph_id}")
        return TaskGraph.from_snapshot(json.loads(str(row["graph_json"])))

    def _persist_graph(self, graph: TaskGraph, connection: Any) -> None:
        status = "TERMINAL" if graph.terminal() else "READY"
        connection.execute(
            """
            UPDATE agentic_task_graphs
            SET graph_json=?,status=?,updated_at=?
            WHERE graph_id=?
            """,
            (
                json.dumps(graph.snapshot(), ensure_ascii=False, sort_keys=True),
                status,
                utc_now(),
                graph.graph_id,
            ),
        )

    @staticmethod
    def _active_lease(
        graph_id: str, node_id: str, lease_id: str, connection: Any
    ) -> Any:
        row = connection.execute(
            """
            SELECT * FROM agentic_node_leases
            WHERE graph_id=? AND node_id=? AND lease_id=? AND status='ACTIVE'
            """,
            (graph_id, node_id, lease_id),
        ).fetchone()
        if row is None:
            raise PermissionError("active node lease is required")
        return row

    @staticmethod
    def _expire_leases(connection: Any, now: str) -> None:
        connection.execute(
            """
            UPDATE agentic_node_leases
            SET status='EXPIRED',released_at=?
            WHERE status='ACTIVE' AND expires_at <= ?
            """,
            (now, now),
        )
