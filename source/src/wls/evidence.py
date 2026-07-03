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
            if len(data) != 32:
                raise ValueError(
                    "evidence key must be exactly 32 bytes; "
                    "the file may have legacy Windows text-mode corruption"
                )
            return data
        data = os.urandom(32)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(self.secret_path, flags, 0o600)
        try:
            remaining = memoryview(data)
            while remaining:
                written = os.write(descriptor, remaining)
                if written <= 0:
                    raise OSError("failed to persist evidence key")
                remaining = remaining[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        persisted = self.secret_path.read_bytes()
        if not hmac.compare_digest(persisted, data):
            self.secret_path.unlink(missing_ok=True)
            raise OSError("evidence key binary round-trip verification failed")
        try:
            os.chmod(self.secret_path, 0o600)
        except OSError:
            pass
        return data

    def append(self, event_type: str, payload: dict[str, Any], connection=None, source_type: str = "synthetic", producer: str | None = None, branch: str | None = None, commit_sha: str | None = None) -> str:
        if connection is None:
            with self.db.transaction() as owned_connection:
                return self.append(event_type, payload, owned_connection, source_type, producer, branch, commit_sha)
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
            "source_type": source_type,
            "producer": producer,
            "branch": branch,
            "commit_sha": commit_sha,
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
                evidence_id, event_type, payload_json, created_at, source_type, producer, branch, commit_sha,
                previous_hash, record_hash, signature,
                source_type,
                producer,
                branch,
                commit_sha, source_type, producer, branch, commit_sha
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                evidence_id,
                event_type,
                json.dumps(payload, ensure_ascii=False, sort_keys=True),
                created_at,
                previous_hash,
                record_hash,
                signature,
                source_type,
                producer,
                branch,
                commit_sha,
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
                "source_type": row["source_type"],
                "producer": row["producer"],
                "branch": row["branch"],
                "commit_sha": row["commit_sha"],
            }
            expected_hash = hashlib.sha256(
                canonical_json(body).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(expected_hash, row["record_hash"]):
                return False, {"reason": "hash_mismatch", "seq": row["seq"]}
            expected_signature = hmac.new(
                self._secret, row["record_hash"].encode("ascii"), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected_signature,
                source_type,
                producer,
                branch,
                commit_sha, row["signature"]):
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
