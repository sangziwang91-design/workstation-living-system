from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import json

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .schemas import digest_json, new_id, utc_now


@dataclass(frozen=True, slots=True)
class OffspringBirthContract:
    offspring_id: str
    parent_id: str
    parent_head: str
    mission: str
    child_home: str
    budget: dict[str, Any]
    inheritance_manifest: dict[str, Any]
    termination_conditions: list[str]
    permission_scope: dict[str, Any] = field(
        default_factory=lambda: {
            "read_only": True,
            "parent_write_allowed": False,
            "child_runtime_start_allowed": False,
            "merge_allowed": False,
            "deployment_allowed": False,
            "skill_promotion_allowed": False,
            "external_system_access_allowed": False,
            "secret_access_allowed": False,
        }
    )
    canonical_authority: str = "LivingSystem"
    child_authority: str = "candidate_only"
    created_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["contract_digest"] = digest_json(payload)
        return payload


class OffspringRegistry:
    """Drafts bounded offspring birth contracts without creating a second authority."""

    def __init__(
        self, config: RuntimeConfig, db: Database, ledger: EvidenceLedger
    ) -> None:
        self.config = config
        self.db = db
        self.ledger = ledger

    def latest_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_birth_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def state_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_state_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def retirement_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_retirement_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def retirement_cleanup_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_retirement_cleanup_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def budget_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_budget_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def checkpoint_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_checkpoint_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def mailbox_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_mailbox_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def ecology_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_ecology_receipts", [])
        if not isinstance(receipts, list):
            return []
        return receipts[: max(0, int(limit))]

    def draft_birth_contract(
        self,
        *,
        parent_head: str,
        mission: str,
        budget: dict[str, Any],
        inheritance_manifest: dict[str, Any],
        termination_conditions: list[str],
        parent_id: str = "WLS-PRIME",
        reason: str,
    ) -> dict[str, Any]:
        self._validate_inputs(
            parent_head=parent_head,
            mission=mission,
            budget=budget,
            inheritance_manifest=inheritance_manifest,
            termination_conditions=termination_conditions,
            reason=reason,
        )
        offspring_id = new_id("offspring")
        child_home = (
            self.config.sandbox_path / "offspring" / offspring_id
        ).expanduser().resolve()
        contract = OffspringBirthContract(
            offspring_id=offspring_id,
            parent_id=parent_id,
            parent_head=parent_head,
            mission=mission,
            child_home=str(child_home),
            budget=dict(budget),
            inheritance_manifest=dict(inheritance_manifest),
            termination_conditions=list(termination_conditions),
        )
        contract_payload = contract.to_dict()
        identity_boundary = {
            "canonical_authority": contract.canonical_authority,
            "child_authority": contract.child_authority,
            "parent_write_allowed": False,
            "child_runtime_started": False,
            "birth_executed": False,
            "candidate_only": True,
            "no_second_living_system": True,
        }
        child_home.mkdir(parents=True, exist_ok=True)
        self._write_json(child_home / "birth_contract.json", contract_payload)
        self._write_json(child_home / "identity_boundary.json", identity_boundary)
        receipt = {
            "receipt_type": "OFFSPRING_BIRTH_CONTRACT_DRAFTED",
            "status": "BIRTH_CONTRACT_DRAFTED",
            "offspring_id": offspring_id,
            "parent_id": parent_id,
            "parent_head": parent_head,
            "reason": reason,
            "mission": mission,
            "child_home": str(child_home),
            "contract": contract_payload,
            "identity_boundary": identity_boundary,
            "contract_path": str(child_home / "birth_contract.json"),
            "identity_path": str(child_home / "identity_boundary.json"),
            "merge_allowed": False,
            "deployment_allowed": False,
            "skill_promotion_allowed": False,
            "external_system_access_allowed": False,
            "secret_access_allowed": False,
            "claim_ceiling": (
                "offspring birth contract drafted in an isolated candidate home; "
                "no child runtime start, parent write, merge, deployment, skill "
                "promotion, external access, or second authority is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.latest_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_birth_receipts", updated, connection)
            self.ledger.append("offspring_birth_contract_drafted", receipt, connection)
        return receipt

    def initialize_isolated_state(
        self, *, offspring_id: str, reason: str
    ) -> dict[str, Any]:
        if not offspring_id.strip():
            raise ValueError("offspring_id is required")
        if not reason.strip():
            raise ValueError("offspring isolated state reason is required")
        birth = self._find_birth_receipt(offspring_id)
        if birth is None:
            raise KeyError(f"unknown offspring birth contract: {offspring_id}")
        contract = birth["contract"]
        child_home = Path(str(birth["child_home"])).expanduser().resolve()
        state_root = child_home / "state"
        state_root.mkdir(parents=True, exist_ok=True)
        budget = dict(contract["budget"])
        budget_ledger = {
            "offspring_id": offspring_id,
            "budget": budget,
            "used": {key: 0 for key in budget},
            "remaining": budget,
            "parent_write_allowed": False,
            "created_at": utc_now(),
        }
        state_manifest = {
            "offspring_id": offspring_id,
            "parent_id": birth["parent_id"],
            "parent_head": birth["parent_head"],
            "state_root": str(state_root),
            "birth_contract_digest": contract["contract_digest"],
            "canonical_authority": "LivingSystem",
            "child_authority": "candidate_only",
            "runtime_started": False,
            "parent_db_mount": False,
            "read_only": True,
            "created_at": utc_now(),
        }
        checkpoint = {
            "checkpoint_id": new_id("offspring_checkpoint"),
            "offspring_id": offspring_id,
            "state": "CREATED_NOT_RUNNING",
            "last_completed_task": None,
            "resume_allowed": False,
            "retirement_required_before_absorption": True,
            "created_at": utc_now(),
        }
        self._write_json(state_root / "state_manifest.json", state_manifest)
        self._write_json(state_root / "budget_ledger.json", budget_ledger)
        self._write_json(state_root / "checkpoint.json", checkpoint)
        receipt = {
            "receipt_type": "OFFSPRING_ISOLATED_STATE_INITIALIZED",
            "status": "ISOLATED_STATE_READY",
            "offspring_id": offspring_id,
            "parent_id": birth["parent_id"],
            "reason": reason,
            "child_home": str(child_home),
            "state_root": str(state_root),
            "state_manifest": state_manifest,
            "budget_ledger": budget_ledger,
            "checkpoint": checkpoint,
            "state_manifest_path": str(state_root / "state_manifest.json"),
            "budget_ledger_path": str(state_root / "budget_ledger.json"),
            "checkpoint_path": str(state_root / "checkpoint.json"),
            "runtime_started": False,
            "parent_db_mount": False,
            "parent_write_allowed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring isolated state and budget ledger initialized only; "
                "no child runtime start, task execution, parent database mount, "
                "parent write, absorption, or second authority is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.state_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_state_receipts", updated, connection)
            self.ledger.append("offspring_isolated_state_initialized", receipt, connection)
        return receipt

    def retire_candidate(
        self,
        *,
        offspring_id: str,
        reason: str,
        outcome_summary: dict[str, Any],
        absorption_requested: bool = False,
    ) -> dict[str, Any]:
        if not offspring_id.strip():
            raise ValueError("offspring_id is required")
        if not reason.strip():
            raise ValueError("offspring retirement reason is required")
        if not isinstance(outcome_summary, dict) or not outcome_summary:
            raise ValueError("outcome_summary is required")
        state_receipt = self._find_state_receipt(offspring_id)
        if state_receipt is None:
            raise KeyError(f"unknown offspring isolated state: {offspring_id}")
        if absorption_requested:
            raise PermissionError(
                "P61 retirement cannot request absorption or capability import"
            )
        child_home = Path(str(state_receipt["child_home"])).expanduser().resolve()
        retirement = {
            "retirement_id": new_id("offspring_retirement"),
            "offspring_id": offspring_id,
            "parent_id": state_receipt["parent_id"],
            "reason": reason,
            "outcome_summary": outcome_summary,
            "terminal_state": "RETIRED_CANDIDATE",
            "runtime_started": False,
            "absorption_requested": False,
            "absorption_allowed": False,
            "promotion_allowed": False,
            "merge_allowed": False,
            "deployment_allowed": False,
            "candidate_state_frozen": True,
            "created_at": utc_now(),
        }
        tombstone_path = child_home / "state" / "retirement_tombstone.json"
        self._write_json(tombstone_path, retirement)
        receipt = {
            "receipt_type": "OFFSPRING_CANDIDATE_RETIRED",
            "status": "RETIRED_CANDIDATE",
            "offspring_id": offspring_id,
            "parent_id": state_receipt["parent_id"],
            "reason": reason,
            "child_home": str(child_home),
            "retirement": retirement,
            "tombstone_path": str(tombstone_path),
            "runtime_started": False,
            "absorption_requested": False,
            "absorption_allowed": False,
            "promotion_allowed": False,
            "merge_allowed": False,
            "deployment_allowed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring candidate retirement tombstone recorded only; no "
                "capability absorption, merge, promotion, deployment, child "
                "runtime execution, or second authority is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.retirement_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_retirement_receipts", updated, connection)
            self.ledger.append("offspring_candidate_retired", receipt, connection)
        return receipt

    def verify_retirement_cleanup(
        self,
        *,
        offspring_id: str,
        reason: str,
        retention_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring retirement cleanup reason is required")
        state_receipt = self._require_state_receipt(offspring_id)
        retirement_receipt = self._find_retirement_receipt(offspring_id)
        if retirement_receipt is None:
            raise PermissionError("offspring must be retired before cleanup verification")
        child_home = Path(str(state_receipt["child_home"])).expanduser().resolve()
        cleanup = self._cleanup_child_resources(child_home)
        residual = self._scan_child_residuals(child_home)
        evidence_bundle = {
            "offspring_id": offspring_id,
            "lineage": self._lineage_for(offspring_id),
            "budget": self._budget_for(offspring_id),
            "results": self._mailbox_results_for(offspring_id),
            "rejections": self._mailbox_rejections_for(offspring_id),
            "retirement": retirement_receipt["retirement"],
            "created_at": utc_now(),
        }
        evidence_bundle["evidence_bundle_digest"] = digest_json(evidence_bundle)
        retention = {
            "retention_id": new_id("offspring_retention"),
            "offspring_id": offspring_id,
            "state_machine": {
                "terminal_states": [
                    "RETIRED_CANDIDATE",
                    "FAILED_CANDIDATE",
                    "ARCHIVED_CANDIDATE",
                    "DESTROYED_RESOURCES",
                ],
                "current_state": "DESTROYED_RESOURCES",
                "evidence_retained": True,
                "task_assignment_allowed": False,
                "budget_reservation_allowed": False,
            },
            "policy": retention_policy
            or {
                "retain_lineage": True,
                "retain_budget": True,
                "retain_results": True,
                "retain_rejections": True,
                "delete_runtime_resources": True,
            },
            "evidence_bundle_digest": evidence_bundle["evidence_bundle_digest"],
            "created_at": utc_now(),
        }
        state_dir = child_home / "state"
        bundle_path = state_dir / "retirement_evidence_bundle.json"
        retention_path = state_dir / "retention_manifest.json"
        cleanup_path = state_dir / "gc_verification.json"
        self._write_json(bundle_path, evidence_bundle)
        self._write_json(retention_path, retention)
        verification = {
            "cleanup": cleanup,
            "residual": residual,
            "process_residual": False,
            "secret_residual": bool(residual["secret_paths"]),
            "mount_residual": bool(residual["mount_paths"]),
            "lease_residual": bool(residual["lease_paths"]),
            "owner_review_required": bool(
                residual["secret_paths"]
                or residual["mount_paths"]
                or residual["lease_paths"]
            ),
        }
        self._write_json(cleanup_path, verification)
        receipt = {
            "receipt_type": "OFFSPRING_RETIREMENT_CLEANUP_VERIFIED",
            "status": "CLEANUP_VERIFIED"
            if not verification["owner_review_required"]
            else "OWNER_REVIEW_REQUIRED",
            "offspring_id": offspring_id,
            "reason": reason,
            "child_home": str(child_home),
            "retention_manifest_path": str(retention_path),
            "evidence_bundle_path": str(bundle_path),
            "gc_verification_path": str(cleanup_path),
            "retention_manifest": retention,
            "evidence_bundle_digest": evidence_bundle["evidence_bundle_digest"],
            "cleanup_verification": verification,
            "task_assignment_allowed": False,
            "budget_reservation_allowed": False,
            "parent_db_write_allowed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring retirement cleanup verification only; evidence is "
                "retained and runtime resources are reclaimed without absorption, "
                "promotion, merge, deployment, or second authority"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.retirement_cleanup_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime(
                "offspring_retirement_cleanup_receipts", updated, connection
            )
            self.ledger.append("offspring_retirement_cleanup_verified", receipt, connection)
        return receipt

    def reserve_budget(
        self,
        *,
        offspring_id: str,
        request: dict[str, int | float],
        reason: str,
        worker_id: str | None = None,
        node_id: str | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring budget reservation reason is required")
        if self._find_retirement_receipt(offspring_id) is not None:
            raise PermissionError("retired offspring cannot reserve budget")
        state_receipt = self._require_state_receipt(offspring_id)
        requested = self._normalize_budget(request)
        ledger_path = Path(str(state_receipt["budget_ledger_path"]))
        budget_ledger = self._read_json(ledger_path)
        remaining = self._normalize_budget(budget_ledger.get("remaining", {}))
        used = self._normalize_budget(budget_ledger.get("used", {}))
        blocked = {
            key: {"requested": value, "remaining": remaining.get(key, 0.0)}
            for key, value in requested.items()
            if remaining.get(key, 0.0) >= 0 and value > remaining.get(key, 0.0)
        }
        status = "BLOCKED" if blocked else "RESERVED"
        if not blocked:
            for key, value in requested.items():
                used[key] = used.get(key, 0.0) + value
                if remaining.get(key, -1.0) >= 0:
                    remaining[key] = remaining.get(key, 0.0) - value
            budget_ledger["used"] = used
            budget_ledger["remaining"] = remaining
            budget_ledger["updated_at"] = utc_now()
            self._write_json(ledger_path, budget_ledger)
        receipt = {
            "receipt_type": "OFFSPRING_BUDGET_RESERVATION",
            "status": status,
            "budget_id": new_id("offspring_budget"),
            "offspring_id": offspring_id,
            "parent_id": state_receipt["parent_id"],
            "worker_id": worker_id,
            "node_id": node_id,
            "reason": reason,
            "requested": requested,
            "remaining_before": remaining if blocked else self._remaining_before(remaining, requested),
            "remaining_after": remaining,
            "blocked_dimensions": blocked,
            "provider_call_executed": False,
            "tool_call_executed": False,
            "parent_write_allowed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring budget gate receipt only; no provider call, tool "
                "execution, child runtime execution, parent write, or task "
                "completion is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        event_type = (
            "offspring_budget_blocked"
            if status == "BLOCKED"
            else "offspring_budget_reserved"
        )
        self._record_budget_receipt(receipt, event_type)
        return receipt

    def review_no_gain_stop(
        self,
        *,
        offspring_id: str,
        evidence_delta: int,
        improvement_delta: float,
        consecutive_no_evidence_rounds: int,
        consecutive_no_improvement_rounds: int,
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring no-gain review reason is required")
        state_receipt = self._require_state_receipt(offspring_id)
        should_stop = (
            evidence_delta <= 0
            and consecutive_no_evidence_rounds >= 2
        ) or (
            improvement_delta <= 0
            and consecutive_no_improvement_rounds >= 3
        )
        receipt = {
            "receipt_type": "OFFSPRING_NO_GAIN_STOP_REVIEW",
            "status": "HARD_STOP_RECORDED" if should_stop else "CONTINUE_ALLOWED",
            "review_id": new_id("offspring_stop"),
            "offspring_id": offspring_id,
            "parent_id": state_receipt["parent_id"],
            "reason": reason,
            "evidence_delta": evidence_delta,
            "improvement_delta": improvement_delta,
            "consecutive_no_evidence_rounds": consecutive_no_evidence_rounds,
            "consecutive_no_improvement_rounds": consecutive_no_improvement_rounds,
            "hard_stop": should_stop,
            "provider_call_executed": False,
            "tool_call_executed": False,
            "parent_write_allowed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring no-gain stop review only; records a hard stop "
                "decision without provider calls, tool calls, absorption, "
                "promotion, or second authority"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_budget_receipt(receipt, "offspring_no_gain_stop_reviewed")
        return receipt

    def audit_ecology(
        self,
        *,
        population: list[dict[str, Any]],
        selection_policy: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring ecology audit reason is required")
        if not population:
            raise ValueError("offspring ecology audit requires population entries")
        normalized = [self._normalize_ecology_entry(item) for item in population]
        total_cost = sum(float(item["cost"]) for item in normalized)
        total_score = sum(float(item["score"]) for item in normalized)
        productive = [
            item
            for item in normalized
            if float(item["score"]) > 0 and str(item["status"]) != "FAILED"
        ]
        niches = sorted({str(item["niche"]) for item in normalized if item["niche"]})
        failure_count = sum(1 for item in normalized if str(item["status"]) == "FAILED")
        productivity = total_score / total_cost if total_cost > 0 else 0.0
        max_population = int(selection_policy.get("max_population", len(normalized)))
        max_depth = int(selection_policy.get("max_depth", 1))
        selection_axes = [
            str(item) for item in selection_policy.get("selection_axes", [])
        ]
        retire_failed = bool(selection_policy.get("retire_failed", True))
        policy = {
            "max_population": max_population,
            "max_depth": max_depth,
            "selection_axes": selection_axes,
            "retire_failed": retire_failed,
            "owner_review_required_for_absorption": True,
        }
        blocked_reasons = []
        if len(normalized) > max_population:
            blocked_reasons.append("population_exceeds_limit")
        if len(niches) < min(2, len(normalized)):
            blocked_reasons.append("insufficient_niche_diversity")
        if failure_count == len(normalized):
            blocked_reasons.append("all_candidates_failed")
        receipt = {
            "receipt_type": "OFFSPRING_ECOLOGY_AUDIT",
            "status": "ECOLOGY_REVIEW_READY"
            if not blocked_reasons
            else "ECOLOGY_REVIEW_BLOCKED",
            "audit_id": new_id("offspring_ecology"),
            "reason": reason,
            "population": normalized,
            "population_count": len(normalized),
            "productive_count": len(productive),
            "failure_count": failure_count,
            "niches": niches,
            "total_cost": total_cost,
            "total_score": total_score,
            "productivity": productivity,
            "selection_policy": policy,
            "blocked_reasons": blocked_reasons,
            "archive_search_executed": False,
            "child_runtime_started": False,
            "parent_write_allowed": False,
            "promotion_executed": False,
            "absorption_executed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring ecology audit receipt only; compares candidate "
                "population evidence and selection constraints without spawning "
                "children, absorbing capabilities, promotion, merge, deployment, "
                "or second authority"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        current = self.ecology_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_ecology_receipts", updated, connection)
            self.ledger.append("offspring_ecology_audit_recorded", receipt, connection)
        return receipt

    def record_checkpoint(
        self,
        *,
        offspring_id: str,
        reason: str,
        artifact_manifest: dict[str, Any] | None = None,
        parent_checkpoint_id: str | None = None,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring checkpoint reason is required")
        state_receipt = self._require_state_receipt(offspring_id)
        state_root = Path(str(state_receipt["state_root"]))
        checkpoint_id = new_id("offspring_checkpoint")
        tracked_paths = [
            Path(str(state_receipt["state_manifest_path"])),
            Path(str(state_receipt["budget_ledger_path"])),
            Path(str(state_receipt["checkpoint_path"])),
        ]
        file_digests = {
            path.name: self._file_sha256(path)
            for path in tracked_paths
        }
        checkpoint = {
            "checkpoint_id": checkpoint_id,
            "offspring_id": offspring_id,
            "parent_id": state_receipt["parent_id"],
            "parent_checkpoint_id": parent_checkpoint_id,
            "reason": reason,
            "state_root": str(state_root),
            "file_digests": file_digests,
            "artifact_manifest": artifact_manifest or {},
            "budget_ledger": self._read_json(Path(str(state_receipt["budget_ledger_path"]))),
            "resume_allowed": False,
            "lease_replay_allowed": False,
            "created_at": utc_now(),
        }
        checkpoint["checkpoint_digest"] = digest_json(checkpoint)
        checkpoint_path = state_root / "checkpoints" / f"{checkpoint_id}.json"
        checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(checkpoint_path, checkpoint)
        receipt = {
            "receipt_type": "OFFSPRING_CHECKPOINT_RECORDED",
            "status": "CHECKPOINT_RECORDED",
            "offspring_id": offspring_id,
            "checkpoint_id": checkpoint_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "checkpoint_path": str(checkpoint_path),
            "checkpoint": checkpoint,
            "runtime_started": False,
            "lease_replay_allowed": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring checkpoint manifest only; no resume, fork execution, "
                "lease replay, child runtime execution, or second authority is inferred"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_checkpoint_receipt(receipt, "offspring_checkpoint_recorded")
        return receipt

    def verify_checkpoint(
        self, *, offspring_id: str, checkpoint_id: str, reason: str
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring checkpoint verification reason is required")
        checkpoint_receipt = self._find_checkpoint_receipt(offspring_id, checkpoint_id)
        if checkpoint_receipt is None:
            raise KeyError(f"unknown offspring checkpoint: {checkpoint_id}")
        checkpoint = checkpoint_receipt["checkpoint"]
        mismatches = {}
        state_root = Path(str(checkpoint["state_root"]))
        for filename, expected in checkpoint["file_digests"].items():
            actual = self._file_sha256(state_root / filename)
            if actual != expected:
                mismatches[filename] = {"expected": expected, "actual": actual}
        receipt = {
            "receipt_type": "OFFSPRING_CHECKPOINT_VERIFIED",
            "status": "TAMPERED" if mismatches else "VERIFIED",
            "offspring_id": offspring_id,
            "checkpoint_id": checkpoint_id,
            "reason": reason,
            "mismatches": mismatches,
            "resume_allowed": False,
            "lease_replay_allowed": False,
            "second_authority_created": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_checkpoint_receipt(receipt, "offspring_checkpoint_verified")
        return receipt

    def fork_candidate(
        self,
        *,
        parent_offspring_id: str,
        parent_checkpoint_id: str,
        mutation_reason: str,
        reason: str,
    ) -> dict[str, Any]:
        if not mutation_reason.strip() or not reason.strip():
            raise ValueError("fork mutation reason and reason are required")
        checkpoint_receipt = self._find_checkpoint_receipt(
            parent_offspring_id, parent_checkpoint_id
        )
        if checkpoint_receipt is None:
            raise KeyError(f"unknown parent checkpoint: {parent_checkpoint_id}")
        parent_birth = self._find_birth_receipt(parent_offspring_id)
        if parent_birth is None:
            raise KeyError(f"unknown parent offspring: {parent_offspring_id}")
        checkpoint = checkpoint_receipt["checkpoint"]
        child_birth = self.draft_birth_contract(
            parent_id=parent_offspring_id,
            parent_head=parent_checkpoint_id,
            mission=f"fork candidate: {mutation_reason}",
            budget=dict(checkpoint["budget_ledger"]["budget"]),
            inheritance_manifest=dict(parent_birth["contract"]["inheritance_manifest"]),
            termination_conditions=list(parent_birth["contract"]["termination_conditions"]),
            reason=reason,
        )
        child_state = self.initialize_isolated_state(
            offspring_id=str(child_birth["offspring_id"]),
            reason=f"fork isolated state from {parent_checkpoint_id}",
        )
        receipt = {
            "receipt_type": "OFFSPRING_FORK_DRAFTED",
            "status": "FORK_DRAFTED",
            "fork_id": new_id("offspring_fork"),
            "parent_offspring_id": parent_offspring_id,
            "parent_checkpoint_id": parent_checkpoint_id,
            "child_offspring_id": child_birth["offspring_id"],
            "mutation_reason": mutation_reason,
            "reason": reason,
            "lineage_edge": {
                "from": parent_offspring_id,
                "to": child_birth["offspring_id"],
                "checkpoint_id": parent_checkpoint_id,
            },
            "child_budget_ledger_path": child_state["budget_ledger_path"],
            "runtime_started": False,
            "parent_db_mount": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring fork draft only; records lineage and independent "
                "budget state without child runtime execution, parent DB mount, "
                "promotion, or second authority"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_checkpoint_receipt(receipt, "offspring_fork_drafted")
        return receipt

    def draft_mailbox_envelope(
        self,
        *,
        offspring_id: str,
        task_id: str,
        attempt_id: str,
        kind: str,
        parts: list[dict[str, Any]],
        artifact_refs: list[dict[str, Any]],
        child_evidence: list[dict[str, Any]],
        sender: str,
        recipient: str,
        reason: str,
        schema_version: str = "offspring-mailbox-v1",
    ) -> dict[str, Any]:
        if schema_version != "offspring-mailbox-v1":
            raise ValueError("unsupported offspring mailbox schema version")
        if kind not in {"Card", "Task", "Message", "Artifact"}:
            raise ValueError("unsupported offspring mailbox kind")
        if not task_id.strip() or not attempt_id.strip():
            raise ValueError("task_id and attempt_id are required")
        if not sender.strip() or not recipient.strip() or not reason.strip():
            raise ValueError("sender, recipient, and reason are required")
        if self._find_retirement_receipt(offspring_id) is not None:
            raise PermissionError("retired offspring cannot receive mailbox tasks")
        state_receipt = self._require_state_receipt(offspring_id)
        trace = self._mailbox_artifact_trace(artifact_refs, child_evidence)
        envelope = {
            "schema_version": schema_version,
            "envelope_id": new_id("offspring_envelope"),
            "offspring_id": offspring_id,
            "sender": sender,
            "recipient": recipient,
            "task_id": task_id,
            "attempt_id": attempt_id,
            "kind": kind,
            "parts": parts,
            "artifact_refs": artifact_refs,
            "child_evidence": child_evidence,
            "candidate_only": True,
            "canonical_completion_claim": False,
            "created_at": utc_now(),
        }
        envelope["digests"] = {
            "parts_digest": digest_json(parts),
            "artifact_refs_digest": digest_json(artifact_refs),
            "child_evidence_digest": digest_json(child_evidence),
        }
        envelope["envelope_digest"] = digest_json(envelope)
        mailbox_root = Path(str(state_receipt["state_root"])) / "mailbox"
        envelope_path = mailbox_root / "outbox" / f"{envelope['envelope_id']}.json"
        envelope_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(envelope_path, envelope)
        receipt = {
            "receipt_type": "OFFSPRING_MAILBOX_ENVELOPE_DRAFTED",
            "status": "DRAFTED",
            "offspring_id": offspring_id,
            "envelope_id": envelope["envelope_id"],
            "schema_version": schema_version,
            "kind": kind,
            "envelope_path": str(envelope_path),
            "envelope_digest": envelope["envelope_digest"],
            "artifact_trace": trace,
            "parent_db_write_allowed": False,
            "candidate_only": True,
            "goal_state_mutated": False,
            "skill_state_mutated": False,
            "completion_authority_transferred": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring mailbox envelope draft only; parent must import and "
                "validate as candidate evidence before any canonical state change"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_mailbox_receipt(receipt, "offspring_mailbox_envelope_drafted")
        return {**receipt, "envelope": envelope}

    def receive_mailbox_envelope(
        self,
        *,
        envelope: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("offspring mailbox receive reason is required")
        schema_version = str(envelope.get("schema_version", ""))
        if schema_version != "offspring-mailbox-v1":
            return self._quarantine_mailbox_envelope(
                envelope=envelope,
                reason=reason,
                quarantine_reason=f"unsupported schema version: {schema_version}",
            )
        required = {
            "envelope_id",
            "offspring_id",
            "sender",
            "recipient",
            "task_id",
            "attempt_id",
            "kind",
            "parts",
            "artifact_refs",
            "child_evidence",
            "digests",
            "envelope_digest",
        }
        missing = sorted(required - set(envelope))
        if missing:
            return self._quarantine_mailbox_envelope(
                envelope=envelope,
                reason=reason,
                quarantine_reason=f"missing required fields: {', '.join(missing)}",
            )
        digest_payload = dict(envelope)
        claimed_digest = str(digest_payload.pop("envelope_digest"))
        if digest_json(digest_payload) != claimed_digest:
            return self._quarantine_mailbox_envelope(
                envelope=envelope,
                reason=reason,
                quarantine_reason="envelope digest mismatch",
            )
        digests = envelope["digests"]
        if (
            digests.get("parts_digest") != digest_json(envelope["parts"])
            or digests.get("artifact_refs_digest")
            != digest_json(envelope["artifact_refs"])
            or digests.get("child_evidence_digest")
            != digest_json(envelope["child_evidence"])
        ):
            return self._quarantine_mailbox_envelope(
                envelope=envelope,
                reason=reason,
                quarantine_reason="component digest mismatch",
            )
        if not envelope.get("candidate_only", False):
            return self._quarantine_mailbox_envelope(
                envelope=envelope,
                reason=reason,
                quarantine_reason="envelope claims non-candidate authority",
            )
        if envelope.get("canonical_completion_claim", False):
            return self._quarantine_mailbox_envelope(
                envelope=envelope,
                reason=reason,
                quarantine_reason="envelope claims canonical completion",
            )
        self._require_state_receipt(str(envelope["offspring_id"]))
        trace = self._mailbox_artifact_trace(
            list(envelope["artifact_refs"]),
            list(envelope["child_evidence"]),
        )
        receipt = {
            "receipt_type": "OFFSPRING_MAILBOX_ENVELOPE_RECEIVED",
            "status": "CANDIDATE_RECEIVED",
            "offspring_id": envelope["offspring_id"],
            "envelope_id": envelope["envelope_id"],
            "schema_version": schema_version,
            "kind": envelope["kind"],
            "task_id": envelope["task_id"],
            "attempt_id": envelope["attempt_id"],
            "artifact_trace": trace,
            "parent_validated_digest": claimed_digest,
            "parent_db_write_allowed": False,
            "candidate_only": True,
            "goal_state_mutated": False,
            "skill_state_mutated": False,
            "completion_authority_transferred": False,
            "second_authority_created": False,
            "claim_ceiling": (
                "offspring envelope accepted as candidate evidence only; parent "
                "retains evaluator, approval, goal, skill, and completion authority"
            ),
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_mailbox_receipt(receipt, "offspring_mailbox_envelope_received")
        return receipt

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"expected JSON object: {path}")
        return value

    @staticmethod
    def _validate_inputs(
        *,
        parent_head: str,
        mission: str,
        budget: dict[str, Any],
        inheritance_manifest: dict[str, Any],
        termination_conditions: list[str],
        reason: str,
    ) -> None:
        if not parent_head.strip():
            raise ValueError("parent_head is required")
        if not mission.strip():
            raise ValueError("mission is required")
        if not reason.strip():
            raise ValueError("offspring birth reason is required")
        if not isinstance(budget, dict) or not budget:
            raise ValueError("bounded offspring budget is required")
        if budget.get("writes", 0) not in {0, "0", False}:
            raise ValueError("P59 offspring birth contract must be read-only")
        if not isinstance(inheritance_manifest, dict) or not inheritance_manifest:
            raise ValueError("inheritance_manifest is required")
        if not termination_conditions:
            raise ValueError("termination_conditions are required")

    def _find_birth_receipt(self, offspring_id: str) -> dict[str, Any] | None:
        for receipt in self.latest_receipts(limit=100):
            if receipt.get("offspring_id") == offspring_id:
                return receipt
        return None

    def _find_state_receipt(self, offspring_id: str) -> dict[str, Any] | None:
        for receipt in self.state_receipts(limit=100):
            if receipt.get("offspring_id") == offspring_id:
                return receipt
        return None

    def _find_retirement_receipt(self, offspring_id: str) -> dict[str, Any] | None:
        for receipt in self.retirement_receipts(limit=100):
            if receipt.get("offspring_id") == offspring_id:
                return receipt
        return None

    def _require_state_receipt(self, offspring_id: str) -> dict[str, Any]:
        if not offspring_id.strip():
            raise ValueError("offspring_id is required")
        state_receipt = self._find_state_receipt(offspring_id)
        if state_receipt is None:
            raise KeyError(f"unknown offspring isolated state: {offspring_id}")
        return state_receipt

    def _record_budget_receipt(
        self, receipt: dict[str, Any], event_type: str
    ) -> None:
        current = self.budget_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_budget_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def _record_checkpoint_receipt(
        self, receipt: dict[str, Any], event_type: str
    ) -> None:
        current = self.checkpoint_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_checkpoint_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def _record_mailbox_receipt(
        self, receipt: dict[str, Any], event_type: str
    ) -> None:
        current = self.mailbox_receipts(limit=100)
        updated = [receipt, *current][:100]
        with self.db.transaction() as connection:
            self.db.set_runtime("offspring_mailbox_receipts", updated, connection)
            self.ledger.append(event_type, receipt, connection)

    def _lineage_for(self, offspring_id: str) -> dict[str, Any]:
        birth = self._find_birth_receipt(offspring_id) or {}
        return {
            "offspring_id": offspring_id,
            "parent_id": birth.get("parent_id"),
            "parent_head": birth.get("parent_head"),
            "contract_digest": birth.get("contract", {}).get("contract_digest"),
            "forks": [
                {
                    "fork_id": receipt.get("fork_id"),
                    "parent_checkpoint_id": receipt.get("parent_checkpoint_id"),
                    "child_offspring_id": receipt.get("child_offspring_id"),
                }
                for receipt in self.checkpoint_receipts(limit=100)
                if receipt.get("parent_offspring_id") == offspring_id
                or receipt.get("child_offspring_id") == offspring_id
            ],
        }

    def _budget_for(self, offspring_id: str) -> dict[str, Any]:
        state = self._find_state_receipt(offspring_id) or {}
        budget_path = state.get("budget_ledger_path")
        ledger = {}
        if budget_path:
            path = Path(str(budget_path))
            if path.is_file():
                ledger = self._read_json(path)
        return {
            "ledger": ledger,
            "receipts": [
                receipt
                for receipt in self.budget_receipts(limit=100)
                if receipt.get("offspring_id") == offspring_id
            ],
        }

    def _mailbox_results_for(self, offspring_id: str) -> list[dict[str, Any]]:
        return [
            receipt
            for receipt in self.mailbox_receipts(limit=100)
            if receipt.get("offspring_id") == offspring_id
            and receipt.get("status") == "CANDIDATE_RECEIVED"
        ]

    def _mailbox_rejections_for(self, offspring_id: str) -> list[dict[str, Any]]:
        return [
            receipt
            for receipt in self.mailbox_receipts(limit=100)
            if receipt.get("offspring_id") == offspring_id
            and receipt.get("status") == "QUARANTINED"
        ]

    def _cleanup_child_resources(self, child_home: Path) -> dict[str, Any]:
        child_home = child_home.resolve()
        targets = ["secrets", "leases", "sandbox", "tmp_credentials"]
        removed = []
        for name in targets:
            path = (child_home / name).resolve()
            if child_home not in path.parents:
                raise ValueError("offspring cleanup path escape")
            if path.is_dir():
                for item in sorted(path.rglob("*"), reverse=True):
                    if item.is_file() or item.is_symlink():
                        item.unlink()
                    elif item.is_dir():
                        item.rmdir()
                path.rmdir()
                removed.append(name)
            elif path.is_file():
                path.unlink()
                removed.append(name)
        return {
            "removed_targets": removed,
            "resource_cleanup_scopes": targets,
            "child_home": str(child_home),
        }

    def _scan_child_residuals(self, child_home: Path) -> dict[str, list[str]]:
        child_home = child_home.resolve()
        secret_paths: list[str] = []
        lease_paths: list[str] = []
        mount_paths: list[str] = []
        for path in child_home.rglob("*"):
            if not path.exists():
                continue
            resolved = path.resolve()
            if child_home not in resolved.parents and resolved != child_home:
                continue
            relative = str(resolved.relative_to(child_home)).replace("\\", "/")
            lowered = relative.lower()
            if any(part in lowered.split("/") for part in {"secrets", "tmp_credentials"}):
                secret_paths.append(relative)
            if any(part in lowered.split("/") for part in {"leases"}):
                lease_paths.append(relative)
            if any(part in lowered.split("/") for part in {"mounts", "sandbox"}):
                mount_paths.append(relative)
        return {
            "secret_paths": sorted(secret_paths),
            "lease_paths": sorted(lease_paths),
            "mount_paths": sorted(mount_paths),
        }

    def _quarantine_mailbox_envelope(
        self,
        *,
        envelope: dict[str, Any],
        reason: str,
        quarantine_reason: str,
    ) -> dict[str, Any]:
        receipt = {
            "receipt_type": "OFFSPRING_MAILBOX_ENVELOPE_QUARANTINED",
            "status": "QUARANTINED",
            "schema_version": str(envelope.get("schema_version", "")),
            "envelope_id": str(envelope.get("envelope_id", "")),
            "offspring_id": str(envelope.get("offspring_id", "")),
            "reason": reason,
            "quarantine_reason": quarantine_reason,
            "envelope_digest": digest_json(envelope),
            "parent_db_write_allowed": False,
            "candidate_only": False,
            "goal_state_mutated": False,
            "skill_state_mutated": False,
            "completion_authority_transferred": False,
            "second_authority_created": False,
            "created_at": utc_now(),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        self._record_mailbox_receipt(receipt, "offspring_mailbox_envelope_quarantined")
        return receipt

    @staticmethod
    def _mailbox_artifact_trace(
        artifact_refs: list[dict[str, Any]],
        child_evidence: list[dict[str, Any]],
    ) -> dict[str, Any]:
        evidence_ids = {str(item.get("evidence_id", "")) for item in child_evidence}
        linked = []
        missing = []
        for ref in artifact_refs:
            evidence_id = str(ref.get("child_evidence_id", ""))
            item = {
                "artifact_id": str(ref.get("artifact_id", "")),
                "sha256": str(ref.get("sha256", "")),
                "child_evidence_id": evidence_id,
                "linked": bool(evidence_id and evidence_id in evidence_ids),
            }
            linked.append(item)
            if not item["linked"]:
                missing.append(item)
        return {
            "artifact_count": len(artifact_refs),
            "child_evidence_count": len(child_evidence),
            "all_artifacts_linked_to_child_evidence": not missing,
            "items": linked,
            "missing": missing,
        }

    @staticmethod
    def _normalize_budget(payload: dict[str, Any]) -> dict[str, float]:
        if not isinstance(payload, dict) or not payload:
            raise ValueError("budget payload is required")
        normalized: dict[str, float] = {}
        for key, value in payload.items():
            number = float(value)
            if number < 0 and number != -1:
                raise ValueError("budget values must be non-negative or -1")
            normalized[str(key)] = number
        return normalized

    @staticmethod
    def _normalize_ecology_entry(item: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError("offspring ecology population entries must be objects")
        offspring_id = str(item.get("offspring_id", "")).strip()
        if not offspring_id:
            raise ValueError("offspring ecology entry requires offspring_id")
        status = str(item.get("status", "")).strip().upper()
        if status not in {"CANDIDATE", "RETIRED", "FAILED", "QUARANTINED"}:
            raise ValueError("unsupported offspring ecology status")
        cost = float(item.get("cost", 0.0))
        score = float(item.get("score", 0.0))
        if cost < 0 or score < 0:
            raise ValueError("offspring ecology cost and score must be non-negative")
        return {
            "offspring_id": offspring_id,
            "parent_id": str(item.get("parent_id", "WLS-PRIME")),
            "status": status,
            "niche": str(item.get("niche", "")).strip(),
            "cost": cost,
            "score": score,
            "failures": int(item.get("failures", 0)),
            "novelty": float(item.get("novelty", 0.0)),
            "evidence_ids": [str(value) for value in item.get("evidence_ids", [])],
        }

    @staticmethod
    def _remaining_before(
        remaining_after: dict[str, float], requested: dict[str, float]
    ) -> dict[str, float]:
        before = dict(remaining_after)
        for key, value in requested.items():
            if before.get(key, -1.0) >= 0:
                before[key] = before.get(key, 0.0) + value
        return before

    def _find_checkpoint_receipt(
        self, offspring_id: str, checkpoint_id: str
    ) -> dict[str, Any] | None:
        for receipt in self.checkpoint_receipts(limit=100):
            if (
                receipt.get("offspring_id") == offspring_id
                and receipt.get("checkpoint_id") == checkpoint_id
                and receipt.get("receipt_type") == "OFFSPRING_CHECKPOINT_RECORDED"
            ):
                return receipt
        return None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
