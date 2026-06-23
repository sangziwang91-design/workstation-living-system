from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
import hashlib
import hmac
import json
import os
import secrets

from .db import Database
from .evidence import EvidenceLedger
from .schemas import ActionSpec, digest_json, new_id, utc_now


class ApprovalManager:
    def __init__(self, db: Database, ledger: EvidenceLedger, secret_path: str | Path):
        self.db = db
        self.ledger = ledger
        self.secret_path = Path(secret_path)
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_or_create()

    def _load_or_create(self) -> bytes:
        if self.secret_path.exists():
            data = self.secret_path.read_bytes()
            if len(data) < 32:
                raise ValueError("approval key too short")
            return data
        data = os.urandom(32)
        fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            os.chmod(self.secret_path, 0o600)
        except OSError:
            pass
        return data

    @staticmethod
    def action_digest(action: ActionSpec | dict[str, Any]) -> str:
        data = action.to_dict() if isinstance(action, ActionSpec) else dict(action)
        stable = {
            "action_id": data["action_id"],
            "tool": data["tool"],
            "arguments": data["arguments"],
            "purpose": data["purpose"],
            "expected_result": data["expected_result"],
            "risk": data["risk"],
            "goal_id": data.get("goal_id"),
            "skill_id": data.get("skill_id"),
            "idempotency_key": data.get("idempotency_key"),
            "acceptance": list(data.get("acceptance", [])),
        }
        return digest_json(stable)

    def issue(
        self, action_id: str, approve: bool, ttl_minutes: int = 30, reason: str = ""
    ) -> str:
        row = self.db.query_one("SELECT * FROM actions WHERE action_id=?", (action_id,))
        if row is None:
            raise KeyError(action_id)
        if row["status"] != "WAITING_APPROVAL":
            raise ValueError(f"action is not awaiting approval: {row['status']}")
        action_data = {
            "action_id": row["action_id"],
            "tool": row["tool"],
            "arguments": json.loads(row["arguments_json"]),
            "purpose": row["purpose"],
            "expected_result": row["expected_result"],
            "risk": row["risk"],
            "goal_id": row["goal_id"],
            "skill_id": row["skill_id"],
            "idempotency_key": row["idempotency_key"],
            "acceptance": json.loads(row["acceptance_json"]),
        }
        digest = self.action_digest(action_data)
        approval_id = new_id("approval")
        nonce = secrets.token_hex(16)
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(minutes=max(1, min(1440, ttl_minutes)))
        decision = "APPROVE" if approve else "REJECT"
        body = {
            "approval_id": approval_id,
            "action_id": action_id,
            "action_digest": digest,
            "decision": decision,
            "issued_at": issued_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "nonce": nonce,
        }
        signature = hmac.new(
            self._secret, json.dumps(body, sort_keys=True).encode(), hashlib.sha256
        ).hexdigest()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO approvals(
                    approval_id,action_id,action_digest,decision,issued_at,expires_at,
                    consumed_at,nonce,signature,reason
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    approval_id,
                    action_id,
                    digest,
                    decision,
                    issued_at.isoformat(),
                    expires_at.isoformat(),
                    None,
                    nonce,
                    signature,
                    reason,
                ),
            )
            new_status = "APPROVED" if approve else "REJECTED"
            connection.execute(
                "UPDATE actions SET approval_id=?,status=? WHERE action_id=?",
                (approval_id, new_status, action_id),
            )
            if not approve:
                remaining = connection.execute(
                    "SELECT COUNT(*) AS n FROM actions WHERE plan_id=? AND status NOT IN ('SUCCEEDED','FAILED','REJECTED','CANCELLED')",
                    (row["plan_id"],),
                ).fetchone()["n"]
                if int(remaining) == 0:
                    connection.execute(
                        "UPDATE plans SET status='FAILED',completed_at=? WHERE plan_id=?",
                        (utc_now(), row["plan_id"]),
                    )
            self.ledger.append(
                "approval_issued",
                {
                    "approval_id": approval_id,
                    "action_id": action_id,
                    "decision": decision,
                    "reason": reason,
                },
                connection,
            )
        return approval_id

    def validate_and_consume(self, action: ActionSpec, approval_id: str) -> bool:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if (
                row is None
                or row["consumed_at"] is not None
                or row["decision"] != "APPROVE"
            ):
                return False
            if datetime.fromisoformat(row["expires_at"]) <= datetime.now(UTC):
                return False
            if row["action_id"] != action.action_id:
                return False
            if not hmac.compare_digest(
                row["action_digest"], self.action_digest(action)
            ):
                return False
            body = {
                "approval_id": row["approval_id"],
                "action_id": row["action_id"],
                "action_digest": row["action_digest"],
                "decision": row["decision"],
                "issued_at": row["issued_at"],
                "expires_at": row["expires_at"],
                "nonce": row["nonce"],
            }
            expected = hmac.new(
                self._secret, json.dumps(body, sort_keys=True).encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, row["signature"]):
                return False
            consumed_at = utc_now()
            connection.execute(
                "UPDATE approvals SET consumed_at=? WHERE approval_id=?",
                (consumed_at, approval_id),
            )
            self.ledger.append(
                "approval_consumed",
                {"approval_id": approval_id, "action_id": action.action_id},
                connection,
            )
            return True
