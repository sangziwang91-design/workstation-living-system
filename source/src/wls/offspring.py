from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
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

    def budget_receipts(self, limit: int = 20) -> list[dict[str, Any]]:
        receipts = self.db.get_runtime("offspring_budget_receipts", [])
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
    def _remaining_before(
        remaining_after: dict[str, float], requested: dict[str, float]
    ) -> dict[str, float]:
        before = dict(remaining_after)
        for key, value in requested.items():
            if before.get(key, -1.0) >= 0:
                before[key] = before.get(key, 0.0) + value
        return before
