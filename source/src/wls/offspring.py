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

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

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
