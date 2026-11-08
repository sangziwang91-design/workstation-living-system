from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import re
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
    observed_stage: str = "UNKNOWN"
    stage_receipt_digest: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["failure_class"] = self.failure_class.value
        return data


class FailureAttributor:
    """Deterministic first-pass failure attribution for agentic task nodes."""

    # Each path denotes an alternative, NOT a verified root cause.
    # Only a structured, bounded stage with a trace digest can generate
    # component hypotheses; error text alone never determines edit targets.
    _STAGE_HYPOTHESES = {
        "model_response": (
            ("source/src/wls/rsi_model_port.py",
             "model output transport or structured-response contract",
             "replay identical model bytes through the frozen adapter"),
            ("source/src/wls/context_manifest.py",
             "missing or confusing model-facing task context",
             "compare the context manifest and model prompt under the same model"),
        ),
        "context_assembly": (
            ("source/src/wls/context_manifest.py",
             "task evidence omitted from the assembled context",
             "compare selected record IDs with the trusted task requirement"),
            ("source/src/wls/agentic_harness.py",
             "task-role context routing mismatch",
             "replay the exact role and graph with a frozen context manifest"),
        ),
        "candidate_selection": (
            ("source/src/wls/rsi_evolution.py",
             "candidate search or parent selection bias",
             "compare frozen-champion and archive arms on identical candidates"),
            ("source/src/wls/rsi_model_port.py",
             "proposer did not produce diverse valid candidates",
             "compare raw pinned-model responses and independent candidate digests"),
        ),
        "tool_execution": (
            ("source/src/wls/coding_workers.py",
             "tool invocation or worker routing defect",
             "replay the same tool request with a trusted worker receipt"),
            ("source/src/wls/isolation_runner.py",
             "sandbox runtime or timeout handling defect",
             "replay the exact candidate under the isolated resource limits"),
        ),
        "memory_retrieval": (
            ("source/src/wls/memory_index.py",
             "relevant prior evidence not retrieved",
             "compare fixed queries with memory on and memory disabled"),
            ("source/src/wls/learning.py",
             "retrieved experience failed to affect future decisions",
             "compare paired decisions with frozen and updated experience"),
        ),
    }

    @classmethod
    def code_hypotheses(
        cls, attribution: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Read-only, falsifiable component hypotheses, never causal proof."""
        payload = attribution if isinstance(attribution, dict) else {}
        stage = payload.get("observed_stage")
        digest = payload.get("stage_receipt_digest")
        authenticated_shape = (
            isinstance(stage, str)
            and isinstance(digest, str)
            and re.fullmatch(r"[a-f0-9]{64}", digest) is not None
        )
        candidates = cls._STAGE_HYPOTHESES.get(stage, ()) if authenticated_shape else ()
        return {
            "schema": "wls.failure_component_hypotheses.v1",
            "status": "HYPOTHESES_ONLY" if candidates else "UNLOCALIZED",
            "reported_stage": stage if authenticated_shape else "UNKNOWN",
            "stage_receipt_digest": digest if authenticated_shape else None,
            "component_candidates": [
                {"path": path, "hypothesis": hypothesis,
                 "required_discriminator": check}
                for path, hypothesis, check in candidates
            ],
            "competing_explanation": (
                "task or model capability, external dependency, or benchmark artifact"
            ),
            "root_cause_verified": False,
            "code_change_authorized": False,
            "claim_ceiling": (
                "read-only suspected edit surfaces from a reported stage and "
                "digest; stage evidence is not independently verified here, "
                "and no causal localization, repair or performance gain is proven"
            ),
        }


    def __init__(self, db: Database) -> None:
        self.db = db

    def classify(
        self,
        *,
        graph_id: str,
        node: TaskNode,
        lease_id: str,
        error: str,
        observed_stage: str | None = None,
        stage_receipt_digest: str | None = None,
    ) -> FailureAttribution:
        allowed_stages = {*self._STAGE_HYPOTHESES, "acceptance"}
        if observed_stage is None and stage_receipt_digest is None:
            observed_stage = "UNKNOWN"
        elif (observed_stage not in allowed_stages
              or not isinstance(stage_receipt_digest, str)
              or re.fullmatch(r"[a-f0-9]{64}", stage_receipt_digest) is None):
            raise ValueError("failure stage requires recognized kind and trace digest")
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
            observed_stage=observed_stage,
            stage_receipt_digest=stage_receipt_digest,
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
