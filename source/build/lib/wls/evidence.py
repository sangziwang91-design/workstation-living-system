from __future__ import annotations

from pathlib import Path
from typing import Any
import hashlib
import hmac
import json
import os

from .db import Database
from .schemas import canonical_json, new_id, utc_now


class EvidenceLedger:
    """Append-only HMAC chained evidence stored atomically in SQLite."""

    def __init__(self, db: Database, secret_path: str | Path):
        self.db = db
        self.secret_path = Path(secret_path)
        self.secret_path.parent.mkdir(parents=True, exist_ok=True)
        self._secret = self._load_or_create_secret()

    def _load_or_create_secret(self) -> bytes:
        if self.secret_path.exists():
            data = self.secret_path.read_bytes()
            if len(data) < 32:
                raise ValueError("evidence key is too short")
            return data
        data = os.urandom(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        descriptor = os.open(self.secret_path, flags, 0o600)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        try:
            os.chmod(self.secret_path, 0o600)
        except OSError:
            pass
        return data

    def append(self, event_type: str, payload: dict[str, Any], connection=None) -> str:
        if connection is None:
            with self.db.transaction() as owned_connection:
                return self.append(event_type, payload, owned_connection)
        evidence_id = new_id("evd")
        created_at = utc_now()
        row = connection.execute(
            "SELECT record_hash FROM evidence ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        previous_hash = row["record_hash"] if row else "GENESIS"
        record_body = {
            "evidence_id": evidence_id,
            "event_type": event_type,
            "payload": payload,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
        record_hash = hashlib.sha256(
            canonical_json(record_body).encode("utf-8")
        ).hexdigest()
        signature = hmac.new(
            self._secret, record_hash.encode("ascii"), hashlib.sha256
        ).hexdigest()
        connection.execute(
            """
            INSERT INTO evidence(
                evidence_id, event_type, payload_json, created_at,
                previous_hash, record_hash, signature
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                created_at,
                previous_hash,
                record_hash,
                signature,
            ),
        )
        return evidence_id

    def verify(self) -> tuple[bool, dict[str, Any]]:
        rows = self.db.query_all("SELECT * FROM evidence ORDER BY seq ASC")
        previous_hash = "GENESIS"
        for row in rows:
            if row["previous_hash"] != previous_hash:
                return False, {"reason": "chain_break", "seq": row["seq"]}
            payload = json.loads(row["payload_json"])
            body = {
                "evidence_id": row["evidence_id"],
                "event_type": row["event_type"],
                "payload": payload,
                "created_at": row["created_at"],
                "previous_hash": row["previous_hash"],
            }
            expected_hash = hashlib.sha256(
                canonical_json(body).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(expected_hash, row["record_hash"]):
                return False, {"reason": "hash_mismatch", "seq": row["seq"]}
            expected_signature = hmac.new(
                self._secret, row["record_hash"].encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature, row["signature"]):
                return False, {"reason": "signature_mismatch", "seq": row["seq"]}
            previous_hash = row["record_hash"]
        return True, {"records": len(rows), "head": previous_hash}

    def export_jsonl(self, target: str | Path) -> Path:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = self.db.query_all("SELECT * FROM evidence ORDER BY seq ASC")
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            for row in rows:
                stream.write(
                    json.dumps(dict(row), ensure_ascii=False, sort_keys=True) + "\n"
                )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        return path
