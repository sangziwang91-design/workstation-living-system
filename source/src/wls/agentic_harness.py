from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
import json
import re

from .db import Database
from .agentic_mailbox import AgenticFileMailbox, TaskEnvelope
from .context_manifest import ContextManifestBuilder
from .evidence import EvidenceLedger
from .failure_attribution import FailureAttributor
from .schemas import RiskLevel, TaskNodeStatus, digest_json, new_id, utc_now
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
        self.failure_attributor = FailureAttributor(db)

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

    def complete_node_with_acceptance(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        result: dict[str, Any],
        acceptance_checks: list[dict[str, Any]],
        artifact_root: Path | str | None = None,
    ) -> dict[str, Any]:
        report = self._evaluate_acceptance(
            result,
            acceptance_checks,
            artifact_root=artifact_root,
        )
        trace = self._trace_event(
            graph_id=graph_id,
            node_id=node_id,
            lease_id=lease_id,
            kind="agentic_node_acceptance",
            payload={
                "result_digest": digest_json(result),
                "acceptance_digest": digest_json(report),
                "passed": report["passed"],
            },
        )
        if not report["passed"]:
            failure = self.fail_node(
                graph_id,
                node_id,
                lease_id=lease_id,
                error="acceptance checks failed",
            )
            payload = {
                **failure,
                "acceptance_report": report,
                "trace_event": trace,
                "claim_ceiling": (
                    "acceptance failure only; no retry, repair, external worker "
                    "execution, or owner approval is inferred"
                ),
            }
            self.ledger.append("agentic_task_node_acceptance_evaluated", payload)
            return payload

        completion = self.complete_node(
            graph_id,
            node_id,
            lease_id=lease_id,
            result={**result, "acceptance_report": report, "trace_event": trace},
        )
        payload = {
            **completion,
            "acceptance_report": report,
            "trace_event": trace,
            "claim_ceiling": (
                "machine acceptance and trace receipt only; does not prove "
                "external worker quality, deployment readiness, or goal completion"
            ),
        }
        self.ledger.append("agentic_task_node_acceptance_evaluated", payload)
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
            attribution = self.failure_attributor.classify(
                graph_id=graph_id, node=node, lease_id=lease_id, error=error
            )
            connection.execute(
                """
                UPDATE agentic_node_leases
                SET status='RELEASED',released_at=?
                WHERE lease_id=?
                """,
                (utc_now(), lease_id),
            )
            self._persist_graph(graph, connection)
            self.failure_attributor.persist(attribution, connection=connection)
            payload = {
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "worker_id": lease["worker_id"],
                "status": node.status.value,
                "error": error,
                "terminal": graph.terminal(),
                "failure_attribution": attribution.to_dict(),
            }
            self.ledger.append("agentic_task_node_failed", payload, connection)
            return payload

    def acceptance_trace_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_task_node_acceptance_evaluated'
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

    def mailbox_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT event_type,payload_json,created_at FROM evidence
            WHERE event_type IN (
                'agentic_task_envelope_exported',
                'agentic_result_envelope_imported',
                'agentic_result_envelope_quarantined',
                'agentic_mailbox_artifact_finalized'
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

    def repair_candidate_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_repair_candidate_proposed'
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

    def budget_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT event_type,payload_json,created_at FROM evidence
            WHERE event_type IN (
                'agentic_node_budget_reserved',
                'agentic_node_budget_blocked'
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

    def checkpoint_resume_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT event_type,payload_json,created_at FROM evidence
            WHERE event_type IN (
                'agentic_graph_checkpoint_recorded',
                'agentic_graph_resume_recorded'
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

    def context_packet_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_context_packet_rendered'
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

    def context_epoch_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_context_epoch_recorded'
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

    def worker_lease_recovery_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT event_type,payload_json,created_at FROM evidence
            WHERE event_type IN (
                'agentic_lease_heartbeat_recorded',
                'agentic_stale_worker_leases_recovered'
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

    def render_context_packet(
        self,
        graph_id: str,
        *,
        role: str,
        token_budget: int = 1200,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("context packet reason is required")
        manifest = self.context_manifest.load_latest_for_graph(graph_id)
        packet = self.context_manifest.build_role_packet(
            manifest,
            role=role,
            token_budget=token_budget,
        )
        receipt = {
            "receipt_type": "AGENTIC_CONTEXT_PACKET_RENDERED",
            "graph_id": graph_id,
            "manifest_id": packet["manifest_id"],
            "packet_id": packet["packet_id"],
            "packet_digest": packet["packet_digest"],
            "role": packet["role"],
            "reason": reason,
            "token_budget": packet["token_budget"],
            "estimated_tokens": packet["estimated_tokens"],
            "included_record_ids": packet["included_record_ids"],
            "suppressed_records": packet["suppressed_records"],
            "secret_material_present": packet["secret_material_present"],
            "raw_database_export": packet["raw_database_export"],
            "worker_execution": False,
            "packet": packet,
            "claim_ceiling": (
                "context packet render receipt only; no worker execution, "
                "provider call, memory write, secret access, or external source "
                "access is inferred"
            ),
        }
        self.ledger.append("agentic_context_packet_rendered", receipt)
        return receipt

    def record_context_epoch(
        self,
        graph_id: str,
        *,
        reason: str,
        max_recent_evidence: int = 20,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("context epoch reason is required")
        if max_recent_evidence < 1:
            raise ValueError("max_recent_evidence must be >= 1")
        graph = self.load_graph(graph_id)
        manifest = self.context_manifest.load_latest_for_graph(graph_id)
        packet_refs = self._context_packet_refs(graph_id)
        evidence_refs = self._recent_graph_evidence_refs(
            graph_id,
            limit=max_recent_evidence,
        )
        node_status_counts: dict[str, int] = {}
        for node in graph.nodes.values():
            node_status_counts[node.status.value] = (
                node_status_counts.get(node.status.value, 0) + 1
            )
        payload = {
            "epoch_id": new_id("ctxepoch"),
            "graph_id": graph_id,
            "intent_id": graph.intent_id,
            "reason": reason,
            "created_at": utc_now(),
            "manifest_ref": {
                "manifest_id": str(manifest["manifest_id"]),
                "manifest_digest": str(manifest["manifest_digest"]),
            },
            "graph_ref": {
                "graph_digest": graph.snapshot()["graph_digest"],
                "node_status_counts": node_status_counts,
            },
            "packet_refs": packet_refs,
            "recent_evidence_refs": evidence_refs,
            "safe_boundary": True,
            "raw_transcript_replaced": False,
            "original_receipts_preserved": True,
            "critical_evidence_ref_count": len(evidence_refs),
            "packet_ref_count": len(packet_refs),
            "worker_execution": False,
            "memory_write": False,
            "claim_ceiling": (
                "context epoch checkpoint receipt only; preserves references to "
                "manifest, role packets, graph digest, and evidence rows without "
                "replacing original receipts or executing workers"
            ),
        }
        payload["epoch_digest"] = digest_json(
            {
                "graph_id": payload["graph_id"],
                "manifest_ref": payload["manifest_ref"],
                "graph_ref": payload["graph_ref"],
                "packet_refs": payload["packet_refs"],
                "recent_evidence_refs": payload["recent_evidence_refs"],
            }
        )
        self.ledger.append("agentic_context_epoch_recorded", payload)
        return payload

    def worker_arbitration_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        return self.worker_registry.arbitration_receipts(limit=limit)

    def retry_gate_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_node_retry_prepared'
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

    def replan_candidate_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_replan_candidate_proposed'
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

    def process_audit_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_process_audit_recorded'
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

    def audit_graph_process(self, graph_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("process audit reason is required")
        graph = self.load_graph(graph_id)
        leases = self.db.query_all(
            """
            SELECT lease_id,node_id,worker_id,status,acquired_at,expires_at,released_at
            FROM agentic_node_leases
            WHERE graph_id=?
            ORDER BY acquired_at ASC
            """,
            (graph_id,),
        )
        evidence = self._node_process_evidence(graph_id)
        active_lease_node_ids = {
            str(row["node_id"])
            for row in leases
            if str(row["status"]) == "ACTIVE"
        }
        issues: list[dict[str, Any]] = []
        for node in graph.nodes.values():
            node_evidence = evidence.get(node.node_id, {})
            if node.status is TaskNodeStatus.SUCCEEDED:
                if not node.result_digest:
                    issues.append(
                        self._process_issue(
                            node.node_id,
                            "succeeded_without_result_digest",
                            "CRITICAL",
                        )
                    )
                if not node_evidence.get("completion"):
                    issues.append(
                        self._process_issue(
                            node.node_id,
                            "succeeded_without_completion_receipt",
                            "CRITICAL",
                        )
                    )
                if not node_evidence.get("acceptance"):
                    issues.append(
                        self._process_issue(
                            node.node_id,
                            "succeeded_without_acceptance_trace",
                            "WARNING",
                        )
                    )
            if node.status is TaskNodeStatus.FAILED and not node_evidence.get("failure"):
                issues.append(
                    self._process_issue(
                        node.node_id,
                        "failed_without_failure_receipt",
                        "CRITICAL",
                    )
                )
            if node.status is TaskNodeStatus.LEASED and node.node_id not in active_lease_node_ids:
                issues.append(
                    self._process_issue(
                        node.node_id,
                        "leased_without_active_lease",
                        "CRITICAL",
                    )
                )
            if (
                node.status is not TaskNodeStatus.LEASED
                and node.node_id in active_lease_node_ids
            ):
                issues.append(
                    self._process_issue(
                        node.node_id,
                        "active_lease_on_nonleased_node",
                        "CRITICAL",
                    )
                )
        critical_count = sum(1 for issue in issues if issue["severity"] == "CRITICAL")
        status = "PASS"
        if critical_count:
            status = "FAIL"
        elif issues:
            status = "REVIEW_REQUIRED"
        node_status_counts: dict[str, int] = {}
        for node in graph.nodes.values():
            node_status_counts[node.status.value] = (
                node_status_counts.get(node.status.value, 0) + 1
            )
        payload = {
            "audit_id": new_id("process_audit"),
            "graph_id": graph_id,
            "reason": reason,
            "status": status,
            "critical_count": critical_count,
            "warning_count": len(issues) - critical_count,
            "issues": issues,
            "node_status_counts": node_status_counts,
            "graph_digest": graph.snapshot()["graph_digest"],
            "active_lease_count": len(active_lease_node_ids),
            "completion_inferred": False,
            "repair_inferred": False,
            "worker_execution": False,
            "claim_ceiling": (
                "process audit receipt only; detects missing process evidence "
                "without completing nodes, retrying work, accepting worker claims, "
                "or mutating live systems"
            ),
        }
        self.ledger.append("agentic_process_audit_recorded", payload)
        return payload

    def propose_replan_candidate(
        self,
        graph_id: str,
        *,
        reason: str,
        trigger_node_id: str | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("replan candidate reason is required")
        graph = self.load_graph(graph_id)
        if trigger_node_id is not None and trigger_node_id not in graph.nodes:
            raise KeyError(f"unknown task node: {trigger_node_id}")
        problem_nodes = [
            node
            for node in graph.nodes.values()
            if node.status in {TaskNodeStatus.FAILED, TaskNodeStatus.BLOCKED}
        ]
        if not problem_nodes:
            raise ValueError("replan candidate requires failed or blocked graph state")
        if trigger_node_id is not None:
            problem_nodes = [
                node for node in problem_nodes if node.node_id == trigger_node_id
            ] or problem_nodes
        can_revise = graph.revision_count < graph.max_revisions
        proposed_changes = [
            {
                "node_id": node.node_id,
                "current_status": node.status.value,
                "proposal": "prepare_alternative_readonly_node_or_contract_revision",
                "executes_now": False,
            }
            for node in problem_nodes
        ]
        payload = {
            "replan_id": new_id("replan"),
            "graph_id": graph_id,
            "reason": reason,
            "trigger_node_id": trigger_node_id,
            "graph_revision": {
                "revision_count": graph.revision_count,
                "max_revisions": graph.max_revisions,
                "can_revise": can_revise,
            },
            "problem_nodes": [
                {
                    "node_id": node.node_id,
                    "status": node.status.value,
                    "error": node.error,
                    "attempts": node.attempts,
                    "max_attempts": node.max_attempts,
                }
                for node in problem_nodes
            ],
            "proposed_changes": proposed_changes,
            "state_mutated": False,
            "graph_changed": False,
            "direct_execution": False,
            "claim_ceiling": (
                "replan candidate receipt only; no graph mutation, retry execution, "
                "worker result, downstream unblock, or task completion is inferred"
            ),
        }
        self.ledger.append("agentic_replan_candidate_proposed", payload)
        return payload

    def prepare_node_retry(
        self,
        graph_id: str,
        node_id: str,
        *,
        reason: str,
        repair_id: str | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("retry preparation reason is required")
        repair = self._latest_repair_candidate(graph_id, node_id, repair_id=repair_id)
        if repair is None:
            raise ValueError("node retry preparation requires a repair candidate")
        with self.db.transaction() as connection:
            graph = self._load_graph_for_update(graph_id, connection)
            if node_id not in graph.nodes:
                raise KeyError(f"unknown task node: {node_id}")
            node = graph.nodes[node_id]
            if node.status is not TaskNodeStatus.FAILED:
                raise ValueError("retry preparation requires a FAILED node")
            if node.attempts >= node.max_attempts:
                raise ValueError("retry preparation blocked by attempt budget")
            previous = {
                "status": node.status.value,
                "error": node.error,
                "attempts": node.attempts,
                "max_attempts": node.max_attempts,
                "lease_id": node.lease_id,
            }
            graph.transition(node_id, TaskNodeStatus.READY)
            graph.nodes[node_id].lease_id = None
            graph.nodes[node_id].worker_id = None
            graph.nodes[node_id].error = None
            self._persist_graph(graph, connection)
            payload = {
                "retry_id": new_id("retry"),
                "graph_id": graph_id,
                "node_id": node_id,
                "reason": reason,
                "repair_id": repair["repair_id"],
                "previous": previous,
                "new_status": graph.nodes[node_id].status.value,
                "attempts_remaining": graph.nodes[node_id].max_attempts
                - graph.nodes[node_id].attempts,
                "downstream_unblocked": False,
                "retry_executed": False,
                "direct_execution": False,
                "claim_ceiling": (
                    "retry preparation receipt only; no worker retry, downstream "
                    "unblock, repair success, acceptance pass, or task completion "
                    "is inferred"
                ),
            }
            self.ledger.append("agentic_node_retry_prepared", payload, connection)
            return payload

    def record_graph_checkpoint(self, graph_id: str, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("checkpoint reason is required")
        graph = self.load_graph(graph_id)
        lease_rows = self.db.query_all(
            """
            SELECT lease_id,node_id,worker_id,status,acquired_at,expires_at,released_at
            FROM agentic_node_leases
            WHERE graph_id=?
            ORDER BY acquired_at DESC
            """,
            (graph_id,),
        )
        node_status_counts: dict[str, int] = {}
        for node in graph.nodes.values():
            node_status_counts[node.status.value] = (
                node_status_counts.get(node.status.value, 0) + 1
            )
        payload = {
            "checkpoint_id": new_id("checkpoint"),
            "graph_id": graph_id,
            "reason": reason,
            "graph_digest": graph.snapshot()["graph_digest"],
            "node_status_counts": node_status_counts,
            "lease_count": len(lease_rows),
            "active_lease_count": sum(
                1 for row in lease_rows if str(row["status"]) == "ACTIVE"
            ),
            "leases": [dict(row) for row in lease_rows[:20]],
            "direct_execution": False,
            "claim_ceiling": (
                "agentic graph checkpoint receipt only; no worker execution, "
                "retry, repair, owner approval, or task completion is inferred"
            ),
        }
        self.ledger.append("agentic_graph_checkpoint_recorded", payload)
        return payload

    def resume_expired_leases(
        self,
        graph_id: str,
        *,
        reason: str,
        now: str | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("resume reason is required")
        resume_at = now or utc_now()
        with self.db.transaction() as connection:
            self._expire_leases(connection, resume_at)
            graph = self._load_graph_for_update(graph_id, connection)
            active_lease_ids = {
                str(row["lease_id"])
                for row in connection.execute(
                    """
                    SELECT lease_id FROM agentic_node_leases
                    WHERE graph_id=? AND status='ACTIVE'
                    """,
                    (graph_id,),
                ).fetchall()
            }
            expired_rows = connection.execute(
                """
                SELECT lease_id,node_id,worker_id,expires_at,released_at
                FROM agentic_node_leases
                WHERE graph_id=? AND status='EXPIRED'
                ORDER BY expires_at DESC
                """,
                (graph_id,),
            ).fetchall()
            expired_lease_ids = {str(row["lease_id"]) for row in expired_rows}
            resumed_nodes: list[dict[str, Any]] = []
            for node in graph.nodes.values():
                if node.status is not TaskNodeStatus.LEASED:
                    continue
                if not node.lease_id or node.lease_id in active_lease_ids:
                    continue
                if node.lease_id not in expired_lease_ids:
                    continue
                previous = {
                    "node_id": node.node_id,
                    "expired_lease_id": node.lease_id,
                    "worker_id": node.worker_id,
                    "attempts": node.attempts,
                }
                graph.transition(node.node_id, TaskNodeStatus.READY)
                graph.nodes[node.node_id].lease_id = None
                graph.nodes[node.node_id].worker_id = None
                graph.nodes[node.node_id].error = None
                resumed_nodes.append(previous)
            self._persist_graph(graph, connection)
            payload = {
                "resume_id": new_id("resume"),
                "graph_id": graph_id,
                "reason": reason,
                "resume_at": resume_at,
                "expired_lease_count": len(expired_rows),
                "resumed_node_count": len(resumed_nodes),
                "resumed_nodes": resumed_nodes,
                "graph_digest": graph.snapshot()["graph_digest"],
                "direct_execution": False,
                "retry_executed": False,
                "claim_ceiling": (
                    "expired lease resume receipt only; nodes may return to READY, "
                    "but no worker result, retry execution, task success, or repair "
                    "is inferred"
                ),
            }
            self.ledger.append("agentic_graph_resume_recorded", payload, connection)
            return payload

    def record_lease_heartbeat(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        extend_seconds: int = 300,
        reason: str,
    ) -> dict[str, Any]:
        if extend_seconds < 1:
            raise ValueError("lease heartbeat extension must be >= 1 second")
        if not reason.strip():
            raise ValueError("lease heartbeat reason is required")
        with self.db.transaction() as connection:
            lease = self._active_lease(graph_id, node_id, lease_id, connection)
            previous_expires_at = str(lease["expires_at"])
            heartbeat_at = utc_now()
            expires_at = (
                datetime.now(UTC) + timedelta(seconds=extend_seconds)
            ).isoformat()
            connection.execute(
                """
                UPDATE agentic_node_leases
                SET expires_at=?
                WHERE lease_id=?
                """,
                (expires_at, lease_id),
            )
            payload = {
                "heartbeat_id": new_id("lease_heartbeat"),
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "worker_id": lease["worker_id"],
                "heartbeat_at": heartbeat_at,
                "previous_expires_at": previous_expires_at,
                "expires_at": expires_at,
                "reason": reason,
                "direct_execution": False,
                "claim_ceiling": (
                    "lease heartbeat receipt only; no worker result, node "
                    "completion, retry execution, or external authority is inferred"
                ),
            }
            self.ledger.append("agentic_lease_heartbeat_recorded", payload, connection)
            return payload

    def recover_stale_worker_leases(
        self,
        graph_id: str,
        *,
        worker_id: str,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("stale worker lease recovery reason is required")
        with self.db.transaction() as connection:
            worker = connection.execute(
                """
                SELECT status FROM agentic_worker_profiles
                WHERE worker_id=?
                """,
                (worker_id,),
            ).fetchone()
            if worker is None:
                raise PermissionError(f"unknown worker profile: {worker_id}")
            if str(worker["status"]) != "STALE":
                raise ValueError("stale worker lease recovery requires STALE worker")
            graph = self._load_graph_for_update(graph_id, connection)
            rows = connection.execute(
                """
                SELECT lease_id,node_id,worker_id,expires_at
                FROM agentic_node_leases
                WHERE graph_id=? AND worker_id=? AND status='ACTIVE'
                ORDER BY acquired_at ASC
                """,
                (graph_id, worker_id),
            ).fetchall()
            recovered_nodes: list[dict[str, Any]] = []
            recovered_at = utc_now()
            for row in rows:
                node_id = str(row["node_id"])
                connection.execute(
                    """
                    UPDATE agentic_node_leases
                    SET status='EXPIRED',released_at=?
                    WHERE lease_id=?
                    """,
                    (recovered_at, row["lease_id"]),
                )
                if node_id in graph.nodes:
                    node = graph.nodes[node_id]
                    if (
                        node.status is TaskNodeStatus.LEASED
                        and node.lease_id == row["lease_id"]
                    ):
                        graph.transition(node_id, TaskNodeStatus.READY)
                        graph.nodes[node_id].lease_id = None
                        graph.nodes[node_id].worker_id = None
                        graph.nodes[node_id].error = None
                recovered_nodes.append(
                    {
                        "node_id": node_id,
                        "expired_lease_id": row["lease_id"],
                        "worker_id": row["worker_id"],
                        "previous_expires_at": row["expires_at"],
                    }
                )
            self._persist_graph(graph, connection)
            payload = {
                "recovery_id": new_id("worker_recovery"),
                "graph_id": graph_id,
                "worker_id": worker_id,
                "reason": reason,
                "recovered_at": recovered_at,
                "recovered_lease_count": len(rows),
                "recovered_nodes": recovered_nodes,
                "graph_digest": graph.snapshot()["graph_digest"],
                "direct_execution": False,
                "retry_executed": False,
                "claim_ceiling": (
                    "stale worker lease recovery receipt only; leased nodes may "
                    "return to READY, but no retry, worker result, repair success, "
                    "or task completion is inferred"
                ),
            }
            self.ledger.append(
                "agentic_stale_worker_leases_recovered",
                payload,
                connection,
            )
            return payload

    def propose_worker_candidates(
        self,
        graph_id: str,
        node_id: str,
        *,
        reason: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        with self.db.transaction() as connection:
            self.worker_registry.ensure_defaults(connection=connection)
            graph = self._load_graph_for_update(graph_id, connection)
            if node_id not in graph.nodes:
                raise KeyError(f"unknown task node: {node_id}")
            node = graph.nodes[node_id]
            if node.status is not TaskNodeStatus.READY:
                raise ValueError("worker candidate arbitration requires a READY node")
            return self.worker_registry.propose_candidates_for_node(
                graph=graph,
                node=node,
                reason=reason,
                connection=connection,
                limit=limit,
            )

    def reserve_node_budget(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        request: dict[str, int | float],
        limit: dict[str, int | float],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("budget reservation reason is required")
        requested = self._normalize_budget_usage(request)
        caps = self._normalize_budget_limit(limit)
        with self.db.transaction() as connection:
            self._active_lease(graph_id, node_id, lease_id, connection)
            prior = self._budget_usage_for_graph(graph_id, connection)
            proposed = {
                key: prior[key] + requested[key]
                for key in ("cost_usd", "tokens", "seconds", "calls")
            }
            exceeded = [
                key
                for key, cap_key in (
                    ("cost_usd", "max_cost_usd"),
                    ("tokens", "max_tokens"),
                    ("seconds", "max_seconds"),
                    ("calls", "max_calls"),
                )
                if caps[cap_key] >= 0 and proposed[key] > caps[cap_key]
            ]
            payload = {
                "budget_id": new_id("budget"),
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "reason": reason,
                "request": requested,
                "limit": caps,
                "prior_usage": prior,
                "proposed_usage": proposed,
                "status": "BLOCKED" if exceeded else "RESERVED",
                "exceeded": exceeded,
                "direct_execution": False,
                "node_state_mutated": False,
                "claim_ceiling": (
                    "agentic node budget gate only; no provider call, tool "
                    "execution, retry, approval, or task completion is inferred"
                ),
            }
            event_type = (
                "agentic_node_budget_blocked"
                if exceeded
                else "agentic_node_budget_reserved"
            )
            self.ledger.append(event_type, payload, connection)
            return payload

    def propose_repair_candidate(
        self,
        graph_id: str,
        node_id: str,
        *,
        reason: str,
        max_steps: int = 3,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("repair candidate reason is required")
        graph = self.load_graph(graph_id)
        if node_id not in graph.nodes:
            raise KeyError(f"unknown task node: {node_id}")
        node = graph.nodes[node_id]
        if node.status not in {TaskNodeStatus.FAILED, TaskNodeStatus.BLOCKED}:
            raise ValueError("repair candidate requires a failed or blocked node")
        attribution = self._latest_failure_attribution(graph_id, node_id)
        acceptance_trace = self._latest_acceptance_trace(graph_id, node_id)
        failure_class = str(
            (attribution or {}).get("failure_class")
            or (attribution or {}).get("failure_attribution", {}).get("failure_class")
            or "UNKNOWN"
        )
        candidate_steps = self._repair_candidate_steps(
            failure_class=failure_class,
            node_status=node.status.value,
            max_steps=max_steps,
        )
        payload = {
            "repair_id": new_id("repair"),
            "graph_id": graph_id,
            "node_id": node_id,
            "trigger": {
                "reason": reason,
                "node_status": node.status.value,
                "node_error": node.error,
                "failure_class": failure_class,
            },
            "provenance": {
                "failure_attribution": attribution,
                "acceptance_trace": acceptance_trace,
                "source": "LivingSystem.AgenticHarness",
                "created_from_real_node_state": True,
            },
            "candidate_steps": candidate_steps,
            "policy_decision": {
                "decision": "REPAIR_CANDIDATE_ONLY",
                "execution_authorized": False,
                "requires_owner_or_explicit_runner_gate": True,
                "second_planner_created": False,
            },
            "state_mutated": False,
            "node_reset": False,
            "direct_execution": False,
            "claim_ceiling": (
                "repair candidate receipt only; no retry, node reset, "
                "external worker execution, owner approval, or fix success is inferred"
            ),
        }
        self.ledger.append("agentic_repair_candidate_proposed", payload)
        return payload

    def export_node_task_envelope(
        self,
        graph_id: str,
        node_id: str,
        *,
        lease_id: str,
        mailbox_root: Path | str,
        recipient: str,
    ) -> dict[str, Any]:
        mailbox = AgenticFileMailbox(mailbox_root)
        with self.db.transaction() as connection:
            graph = self._load_graph_for_update(graph_id, connection)
            lease = self._active_lease(graph_id, node_id, lease_id, connection)
            node = graph.nodes[node_id]
            envelope = TaskEnvelope.create(
                message_id=new_id("msg"),
                graph_id=graph_id,
                node_id=node_id,
                lease_id=lease_id,
                sender="LivingSystem.AgenticHarness",
                recipient=recipient,
                payload={
                    "title": node.title,
                    "role": node.role,
                    "acceptance": list(node.acceptance),
                    "risk": node.risk.value,
                    "conflict_domain": node.conflict_domain,
                    "worker_id": lease["worker_id"],
                    "lease_fencing_token": self._lease_fencing_token(
                        graph_id=graph_id,
                        node_id=node_id,
                        lease_id=lease_id,
                    ),
                    "lease_generation": lease_id,
                    "canonical_completion_authority": "LivingSystem.AgenticHarness",
                },
            )
            path = mailbox.write_task(envelope)
            receipt: dict[str, Any] = {
                "receipt_type": "AGENTIC_TASK_ENVELOPE_EXPORTED",
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "message_id": envelope.message_id,
                "recipient": recipient,
                "path": str(path),
                "payload_digest": envelope.payload_digest,
                "direct_execution": False,
                "canonical_completion_authority": "LivingSystem.AgenticHarness",
                "claim_ceiling": (
                    "local task envelope transport only; no external worker "
                    "execution or node completion is claimed"
                ),
            }
            self.ledger.append("agentic_task_envelope_exported", receipt, connection)
        return receipt

    def import_node_result_envelope(
        self,
        *,
        mailbox_root: Path | str,
        message_id: str,
        acceptance_checks: list[dict[str, Any]] | None = None,
        artifact_root: Path | str | None = None,
    ) -> dict[str, Any]:
        mailbox = AgenticFileMailbox(mailbox_root)
        envelope = mailbox.read_result(message_id)
        previous = self._previous_result_envelope_receipt(envelope.message_id)
        if previous is not None:
            reason = "duplicate_result_replay"
            previous_digest = str(previous.get("payload_digest") or "")
            if previous_digest and previous_digest != envelope.payload_digest:
                reason = "message_id_digest_conflict"
            rejected_path = mailbox.reject(message_id, result=True)
            receipt: dict[str, Any] = {
                "receipt_type": "AGENTIC_RESULT_ENVELOPE_QUARANTINED",
                "graph_id": envelope.graph_id,
                "node_id": envelope.node_id,
                "lease_id": envelope.lease_id,
                "message_id": envelope.message_id,
                "in_reply_to": envelope.in_reply_to,
                "status": envelope.status.upper(),
                "payload_digest": envelope.payload_digest,
                "previous_payload_digest": previous_digest or None,
                "rejected_path": str(rejected_path),
                "reason": reason,
                "completion_attempted": False,
                "canonical_completion_authority": "LivingSystem.AgenticHarness",
                "claim_ceiling": (
                    "duplicate or conflicting result envelope quarantined only; "
                    "no node completion, failure transition, retry, or worker "
                    "authority is inferred"
                ),
            }
            self.ledger.append("agentic_result_envelope_quarantined", receipt)
            return receipt
        fencing = self._result_fencing_violation(envelope)
        if fencing is not None:
            rejected_path = mailbox.reject(message_id, result=True)
            receipt = {
                "receipt_type": "AGENTIC_RESULT_ENVELOPE_QUARANTINED",
                "graph_id": envelope.graph_id,
                "node_id": envelope.node_id,
                "lease_id": envelope.lease_id,
                "message_id": envelope.message_id,
                "in_reply_to": envelope.in_reply_to,
                "status": envelope.status.upper(),
                "payload_digest": envelope.payload_digest,
                "rejected_path": str(rejected_path),
                "reason": fencing["reason"],
                "fencing": fencing,
                "completion_attempted": False,
                "canonical_completion_authority": "LivingSystem.AgenticHarness",
                "claim_ceiling": (
                    "late or stale worker result quarantined by lease fencing only; "
                    "no node completion, failure transition, retry, or worker "
                    "authority is inferred"
                ),
            }
            self.ledger.append("agentic_result_envelope_quarantined", receipt)
            return receipt
        if envelope.status.upper() == "SUCCEEDED":
            if acceptance_checks:
                completion = self.complete_node_with_acceptance(
                    envelope.graph_id,
                    envelope.node_id,
                    lease_id=envelope.lease_id,
                    result={
                        "status": envelope.status.upper(),
                        "payload": envelope.payload,
                    },
                    acceptance_checks=acceptance_checks,
                    artifact_root=artifact_root,
                )
            else:
                completion = self.complete_node(
                    envelope.graph_id,
                    envelope.node_id,
                    lease_id=envelope.lease_id,
                    result={
                        "status": envelope.status.upper(),
                        "payload": envelope.payload,
                    },
                )
        else:
            completion = self.fail_node(
                envelope.graph_id,
                envelope.node_id,
                lease_id=envelope.lease_id,
                error=str(envelope.payload.get("error") or envelope.status),
            )
        processed_path = mailbox.mark_processed(message_id, result=True)
        imported_receipt: dict[str, Any] = {
            "receipt_type": "AGENTIC_RESULT_ENVELOPE_IMPORTED",
            "graph_id": envelope.graph_id,
            "node_id": envelope.node_id,
            "lease_id": envelope.lease_id,
            "message_id": envelope.message_id,
            "in_reply_to": envelope.in_reply_to,
            "status": envelope.status.upper(),
            "payload_digest": envelope.payload_digest,
            "processed_path": str(processed_path),
            "completion": completion,
            "canonical_completion_authority": "LivingSystem.AgenticHarness",
            "claim_ceiling": (
                "local result envelope imported through canonical harness only; "
                "no external worker authority, deployment, or goal completion is claimed"
            ),
        }
        self.ledger.append("agentic_result_envelope_imported", imported_receipt)
        return imported_receipt

    def finalize_mailbox_artifact(
        self,
        *,
        mailbox_root: Path | str,
        artifact_id: str,
        chunk_count: int,
        expected_sha256: str,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("artifact finalization reason is required")
        mailbox = AgenticFileMailbox(mailbox_root)
        manifest = mailbox.finalize_artifact(
            artifact_id,
            chunk_count=chunk_count,
            expected_sha256=expected_sha256,
        )
        receipt = {
            "receipt_type": "AGENTIC_MAILBOX_ARTIFACT_FINALIZED",
            "artifact_id": artifact_id,
            "chunk_count": chunk_count,
            "sha256": manifest["sha256"],
            "path": manifest["path"],
            "manifest_path": manifest["manifest_path"],
            "reason": reason,
            "completion_attempted": False,
            "canonical_completion_authority": "LivingSystem.AgenticHarness",
            "claim_ceiling": (
                "mailbox artifact finalization receipt only; no node completion, "
                "worker result acceptance, tool execution, or external authority "
                "is inferred"
            ),
        }
        self.ledger.append("agentic_mailbox_artifact_finalized", receipt)
        return receipt

    def _previous_result_envelope_receipt(
        self, message_id: str
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            """
            SELECT payload_json FROM evidence
            WHERE event_type IN (
                'agentic_result_envelope_imported',
                'agentic_result_envelope_quarantined'
            )
            AND json_extract(payload_json, '$.message_id')=?
            ORDER BY seq ASC
            LIMIT 1
            """,
            (message_id,),
        )
        if row is None:
            return None
        payload = json.loads(str(row["payload_json"]))
        return payload if isinstance(payload, dict) else None

    def _result_fencing_violation(self, envelope: Any) -> dict[str, Any] | None:
        lease = self.db.query_one(
            """
            SELECT lease_id,graph_id,node_id,worker_id,status,acquired_at,expires_at,released_at
            FROM agentic_node_leases
            WHERE graph_id=? AND node_id=? AND lease_id=?
            LIMIT 1
            """,
            (envelope.graph_id, envelope.node_id, envelope.lease_id),
        )
        if lease is None:
            return {
                "reason": "unknown_lease",
                "expected_fencing_token": self._lease_fencing_token(
                    graph_id=envelope.graph_id,
                    node_id=envelope.node_id,
                    lease_id=envelope.lease_id,
                ),
                "received_fencing_token": envelope.payload.get("lease_fencing_token"),
            }
        graph = self.load_graph(envelope.graph_id)
        node = graph.nodes.get(envelope.node_id)
        expected_token = self._lease_fencing_token(
            graph_id=envelope.graph_id,
            node_id=envelope.node_id,
            lease_id=envelope.lease_id,
        )
        received_token = envelope.payload.get("lease_fencing_token")
        if received_token is not None and received_token != expected_token:
            return {
                "reason": "fencing_token_mismatch",
                "lease_status": lease["status"],
                "worker_id": lease["worker_id"],
                "expected_fencing_token": expected_token,
                "received_fencing_token": received_token,
            }
        if str(lease["status"]) != "ACTIVE":
            return {
                "reason": "inactive_lease",
                "lease_status": lease["status"],
                "worker_id": lease["worker_id"],
                "released_at": lease["released_at"],
                "expected_fencing_token": expected_token,
                "received_fencing_token": received_token,
            }
        if node is None:
            return {
                "reason": "unknown_node",
                "lease_status": lease["status"],
                "worker_id": lease["worker_id"],
                "expected_fencing_token": expected_token,
                "received_fencing_token": received_token,
            }
        if node.status is not TaskNodeStatus.LEASED or node.lease_id != envelope.lease_id:
            return {
                "reason": "node_not_owned_by_lease",
                "lease_status": lease["status"],
                "worker_id": lease["worker_id"],
                "node_status": node.status.value,
                "node_lease_id": node.lease_id,
                "expected_fencing_token": expected_token,
                "received_fencing_token": received_token,
            }
        return None

    @staticmethod
    def _lease_fencing_token(
        *, graph_id: str, node_id: str, lease_id: str
    ) -> str:
        return digest_json(
            {
                "graph_id": graph_id,
                "node_id": node_id,
                "lease_id": lease_id,
                "authority": "LivingSystem.AgenticHarness",
            }
        )

    def _latest_failure_attribution(
        self, graph_id: str, node_id: str
    ) -> dict[str, Any] | None:
        row = self.db.query_one(
            """
            SELECT attribution_json,created_at FROM agentic_failure_attributions
            WHERE graph_id=? AND node_id=?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (graph_id, node_id),
        )
        if row is None:
            return None
        payload = json.loads(str(row["attribution_json"]))
        if isinstance(payload, dict):
            return {**payload, "created_at": row["created_at"]}
        return None

    def _latest_acceptance_trace(
        self, graph_id: str, node_id: str
    ) -> dict[str, Any] | None:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_task_node_acceptance_evaluated'
            ORDER BY seq DESC
            LIMIT 50
            """
        )
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if (
                isinstance(payload, dict)
                and payload.get("graph_id") == graph_id
                and payload.get("node_id") == node_id
            ):
                return {**payload, "created_at": row["created_at"]}
        return None

    def _latest_repair_candidate(
        self, graph_id: str, node_id: str, *, repair_id: str | None = None
    ) -> dict[str, Any] | None:
        rows = self.db.query_all(
            """
            SELECT payload_json,created_at FROM evidence
            WHERE event_type='agentic_repair_candidate_proposed'
            ORDER BY seq DESC
            LIMIT 100
            """
        )
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                continue
            if payload.get("graph_id") != graph_id or payload.get("node_id") != node_id:
                continue
            if repair_id is not None and payload.get("repair_id") != repair_id:
                continue
            return {**payload, "created_at": row["created_at"]}
        return None

    @staticmethod
    def _repair_candidate_steps(
        *, failure_class: str, node_status: str, max_steps: int
    ) -> list[dict[str, Any]]:
        library: dict[str, list[dict[str, str]]] = {
            "LOCAL": [
                {
                    "step": "inspect_node_output",
                    "purpose": "compare result payload with declared acceptance checks",
                },
                {
                    "step": "revise_candidate_result_or_worker_instruction",
                    "purpose": "prepare a corrected candidate without resetting node state",
                },
                {
                    "step": "rerun_focused_acceptance_checks",
                    "purpose": "verify the candidate before any resume gate",
                },
            ],
            "POLICY": [
                {
                    "step": "preserve_policy_denial",
                    "purpose": "keep the approval boundary as evidence",
                },
                {
                    "step": "request_explicit_owner_gate",
                    "purpose": "avoid bypassing canonical Policy authority",
                },
            ],
            "ENVIRONMENT": [
                {
                    "step": "capture_environment_condition",
                    "purpose": "record path, lock, timeout, or resource evidence",
                },
                {
                    "step": "rerun_after_environment_is_stable",
                    "purpose": "resume only after the disposable condition is fixed",
                },
            ],
            "STRUCTURAL": [
                {
                    "step": "revise_graph_contract_candidate",
                    "purpose": "prepare a graph or acceptance-contract patch for review",
                },
                {
                    "step": "run_graph_invariant_tests",
                    "purpose": "prove the patch does not split planner authority",
                },
            ],
            "UPSTREAM": [
                {
                    "step": "quarantine_upstream_receipt",
                    "purpose": "avoid replaying untrusted external output",
                },
                {
                    "step": "retry_with_fresh_upstream_evidence",
                    "purpose": "resume only with a new receipt and unchanged policy gate",
                },
            ],
            "UNKNOWN": [
                {
                    "step": "preserve_failure_packet",
                    "purpose": "retain raw evidence for later diagnosis",
                },
                {
                    "step": "perform_focused_root_cause_review",
                    "purpose": "classify before any state transition",
                },
            ],
        }
        steps = library.get(failure_class, library["UNKNOWN"])
        return [
            {
                **step,
                "order": index + 1,
                "node_status_at_proposal": node_status,
                "executes_now": False,
            }
            for index, step in enumerate(steps[: max(1, int(max_steps))])
        ]

    @staticmethod
    def _normalize_budget_usage(payload: dict[str, int | float]) -> dict[str, float]:
        usage = {
            "cost_usd": float(payload.get("cost_usd", 0.0)),
            "tokens": float(payload.get("tokens", 0)),
            "seconds": float(payload.get("seconds", 0.0)),
            "calls": float(payload.get("calls", 1)),
        }
        if any(value < 0 for value in usage.values()):
            raise ValueError("budget usage values must be non-negative")
        return usage

    @staticmethod
    def _normalize_budget_limit(payload: dict[str, int | float]) -> dict[str, float]:
        caps = {
            "max_cost_usd": float(payload.get("max_cost_usd", -1)),
            "max_tokens": float(payload.get("max_tokens", -1)),
            "max_seconds": float(payload.get("max_seconds", -1)),
            "max_calls": float(payload.get("max_calls", -1)),
        }
        if any(value < -1 for value in caps.values()):
            raise ValueError("budget limits must be -1 for unbounded or non-negative")
        return caps

    @staticmethod
    def _budget_usage_for_graph(graph_id: str, connection: Any) -> dict[str, float]:
        rows = connection.execute(
            """
            SELECT payload_json FROM evidence
            WHERE event_type='agentic_node_budget_reserved'
            ORDER BY seq ASC
            """
        ).fetchall()
        usage = {"cost_usd": 0.0, "tokens": 0.0, "seconds": 0.0, "calls": 0.0}
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict) or payload.get("graph_id") != graph_id:
                continue
            request = payload.get("request")
            if not isinstance(request, dict):
                continue
            normalized = AgenticHarness._normalize_budget_usage(request)
            for key, value in normalized.items():
                usage[key] += value
        return usage

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

    def _evaluate_acceptance(
        self,
        result: dict[str, Any],
        checks: list[dict[str, Any]],
        *,
        artifact_root: Path | str | None,
    ) -> dict[str, Any]:
        root = Path(artifact_root).expanduser().resolve() if artifact_root else None
        outcomes = [
            self._run_acceptance_check(result, check, artifact_root=root)
            for check in checks
        ]
        passed = all(item["passed"] or not item["critical"] for item in outcomes)
        return {
            "passed": passed,
            "checks": outcomes,
            "check_count": len(outcomes),
            "critical_failures": [
                item["check_id"]
                for item in outcomes
                if not item["passed"] and item["critical"]
            ],
            "claim_ceiling": "local deterministic acceptance checks only",
        }

    def _run_acceptance_check(
        self,
        result: dict[str, Any],
        check: dict[str, Any],
        *,
        artifact_root: Path | None,
    ) -> dict[str, Any]:
        check_id = str(check.get("check_id") or check.get("type") or "check")
        check_type = str(check.get("type") or "json_required_keys")
        critical = bool(check.get("critical", True))
        try:
            passed, detail = self._dispatch_acceptance_check(
                result,
                check_type=check_type,
                config=dict(check.get("config", {})),
                artifact_root=artifact_root,
            )
        except Exception as exc:
            passed, detail = False, f"{type(exc).__name__}: {exc}"
        return {
            "check_id": check_id,
            "type": check_type,
            "passed": passed,
            "detail": detail,
            "critical": critical,
        }

    def _dispatch_acceptance_check(
        self,
        result: dict[str, Any],
        *,
        check_type: str,
        config: dict[str, Any],
        artifact_root: Path | None,
    ) -> tuple[bool, str]:
        if check_type == "result_status":
            expected = str(config.get("expected", "SUCCEEDED")).upper()
            actual = str(result.get("status", "SUCCEEDED")).upper()
            return actual == expected, f"actual={actual} expected={expected}"
        if check_type == "evidence_min":
            minimum = int(config.get("minimum", 1))
            count = len(result.get("evidence", []))
            return count >= minimum, f"evidence={count} minimum={minimum}"
        if check_type == "json_required_keys":
            payload = result.get("payload")
            if not isinstance(payload, dict):
                payload = result
            keys = [str(item) for item in config.get("keys", [])]
            missing = [key for key in keys if key not in payload]
            return not missing, f"missing={missing}"
        if check_type == "metric_range":
            metrics = result.get("metrics")
            if not isinstance(metrics, dict):
                raise ValueError("metric_range requires result.metrics")
            name = str(config["name"])
            value = float(metrics[name])
            lower = float(config.get("min", float("-inf")))
            upper = float(config.get("max", float("inf")))
            return lower <= value <= upper, f"{name}={value} range=[{lower},{upper}]"
        if check_type == "regex":
            pattern = str(config["pattern"])
            field = str(config.get("field", "summary"))
            target = str(result.get(field, ""))
            if field == "payload":
                target = json.dumps(result.get("payload", {}), sort_keys=True)
            matched = re.search(pattern, target) is not None
            return matched, f"pattern={pattern!r} matched={matched}"
        if check_type in {"artifact_exists", "artifact_sha256"}:
            if artifact_root is None:
                raise ValueError("artifact_root is required")
            relative = str(config["path"])
            path = (artifact_root / relative).resolve()
            if path != artifact_root and artifact_root not in path.parents:
                raise ValueError("artifact path escapes artifact_root")
            exists = path.is_file()
            if check_type == "artifact_exists":
                return exists, f"path={relative} exists={exists}"
            if not exists:
                return False, f"path={relative} missing"
            expected = str(config["sha256"]).lower()
            actual = self._sha256_file(path)
            return actual == expected, f"actual={actual} expected={expected}"
        raise ValueError(f"unsupported acceptance check type: {check_type}")

    @staticmethod
    def _trace_event(
        *,
        graph_id: str,
        node_id: str,
        lease_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        event = {
            "trace_id": f"trace:{graph_id}",
            "span_id": f"{node_id}:{lease_id}",
            "parent_span_id": graph_id,
            "kind": kind,
            "actor": "LivingSystem.AgenticHarness",
            "payload": payload,
            "timestamp": utc_now(),
        }
        event["payload_digest"] = digest_json(payload)
        event["trace_digest"] = digest_json(
            {
                "trace_id": event["trace_id"],
                "span_id": event["span_id"],
                "kind": kind,
                "payload_digest": event["payload_digest"],
            }
        )
        return event

    def _node_process_evidence(self, graph_id: str) -> dict[str, dict[str, bool]]:
        rows = self.db.query_all(
            """
            SELECT event_type,payload_json FROM evidence
            WHERE json_extract(payload_json, '$.graph_id')=?
            """,
            (graph_id,),
        )
        evidence: dict[str, dict[str, bool]] = {}
        for row in rows:
            event_type = str(row["event_type"])
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                continue
            node_id = str(payload.get("node_id") or "")
            if not node_id:
                completion = payload.get("completion")
                if isinstance(completion, dict):
                    node_id = str(completion.get("node_id") or "")
            if not node_id:
                continue
            flags = evidence.setdefault(
                node_id,
                {
                    "completion": False,
                    "acceptance": False,
                    "failure": False,
                    "import": False,
                },
            )
            if event_type == "agentic_task_node_completed":
                flags["completion"] = True
            elif event_type == "agentic_task_node_acceptance_evaluated":
                flags["acceptance"] = True
                if payload.get("status") == TaskNodeStatus.FAILED.value:
                    flags["failure"] = True
                if payload.get("result_digest"):
                    flags["completion"] = True
            elif event_type == "agentic_task_node_failed":
                flags["failure"] = True
            elif event_type == "agentic_result_envelope_imported":
                flags["import"] = True
                completion = payload.get("completion")
                if isinstance(completion, dict):
                    if completion.get("result_digest"):
                        flags["completion"] = True
                    if completion.get("acceptance_report") is not None:
                        flags["acceptance"] = True
                    if completion.get("status") == TaskNodeStatus.FAILED.value:
                        flags["failure"] = True
        return evidence

    def _context_packet_refs(self, graph_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT evidence_id,payload_json,created_at,record_hash FROM evidence
            WHERE event_type='agentic_context_packet_rendered'
              AND json_extract(payload_json, '$.graph_id')=?
            ORDER BY seq ASC
            """,
            (graph_id,),
        )
        refs: list[dict[str, Any]] = []
        for row in rows:
            payload = json.loads(str(row["payload_json"]))
            if not isinstance(payload, dict):
                continue
            refs.append(
                {
                    "evidence_id": row["evidence_id"],
                    "record_hash": row["record_hash"],
                    "created_at": row["created_at"],
                    "packet_id": payload.get("packet_id"),
                    "packet_digest": payload.get("packet_digest"),
                    "role": payload.get("role"),
                }
            )
        return refs

    def _recent_graph_evidence_refs(
        self,
        graph_id: str,
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT seq,evidence_id,event_type,created_at,record_hash FROM evidence
            WHERE json_extract(payload_json, '$.graph_id')=?
            ORDER BY seq DESC
            LIMIT ?
            """,
            (graph_id, max(1, int(limit))),
        )
        return [
            {
                "seq": row["seq"],
                "evidence_id": row["evidence_id"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "record_hash": row["record_hash"],
            }
            for row in reversed(rows)
        ]

    @staticmethod
    def _process_issue(
        node_id: str, issue_type: str, severity: str
    ) -> dict[str, str]:
        return {
            "node_id": node_id,
            "type": issue_type,
            "severity": severity,
        }

    @staticmethod
    def _sha256_file(path: Path) -> str:
        import hashlib

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

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
