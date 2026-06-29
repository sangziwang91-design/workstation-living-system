from __future__ import annotations

from typing import Any
import json
import secrets

from .schemas import digest_json, utc_now


class ReplayManifestStore:
    """Records replay intent over canonical cycle checkpoints; never executes it."""

    def __init__(self, db: Any, ledger: Any) -> None:
        self.db = db
        self.ledger = ledger
        with db.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS replay_manifests(
                    replay_id TEXT PRIMARY KEY,
                    source_cycle_id TEXT NOT NULL,
                    source_checkpoint_id TEXT NOT NULL,
                    source_checkpoint_digest TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    overrides_json TEXT NOT NULL,
                    overrides_digest TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    consumed_at TEXT,
                    FOREIGN KEY(source_cycle_id) REFERENCES cycles(cycle_id),
                    FOREIGN KEY(source_checkpoint_id)
                      REFERENCES cycle_checkpoints(checkpoint_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_replay_manifest_status ON replay_manifests(status,created_at)"
            )

    def create(
        self,
        source_cycle_id: str,
        source_checkpoint_id: str,
        *,
        mode: str = "READ_ONLY",
        overrides: dict[str, Any] | None = None,
        created_by: str = "owner",
    ) -> str:
        if mode not in {"READ_ONLY", "COUNTERFACTUAL"}:
            raise ValueError("invalid replay mode")
        if not created_by.strip():
            raise ValueError("created_by is required")
        normalized = json.loads(
            json.dumps(overrides or {}, ensure_ascii=False, sort_keys=True)
        )
        if mode == "READ_ONLY" and normalized:
            raise ValueError("READ_ONLY replay cannot contain overrides")
        replay_id = f"replay_{secrets.token_hex(12)}"
        with self.db.transaction() as connection:
            checkpoint = connection.execute(
                "SELECT cycle_id,payload_digest FROM cycle_checkpoints WHERE checkpoint_id=?",
                (source_checkpoint_id,),
            ).fetchone()
            if checkpoint is None:
                raise KeyError(source_checkpoint_id)
            if str(checkpoint["cycle_id"]) != source_cycle_id:
                raise ValueError("checkpoint does not belong to source cycle")
            now = utc_now()
            overrides_digest = digest_json(normalized)
            connection.execute(
                """
                INSERT INTO replay_manifests(
                    replay_id,source_cycle_id,source_checkpoint_id,
                    source_checkpoint_digest,mode,status,overrides_json,
                    overrides_digest,created_by,created_at
                ) VALUES (?,?,?,?,?,'PROPOSED',?,?,?,?)
                """,
                (
                    replay_id,
                    source_cycle_id,
                    source_checkpoint_id,
                    str(checkpoint["payload_digest"]),
                    mode,
                    json.dumps(normalized, ensure_ascii=False, sort_keys=True),
                    overrides_digest,
                    created_by,
                    now,
                ),
            )
            self.ledger.append(
                "replay_manifest_created",
                {
                    "replay_id": replay_id,
                    "source_cycle_id": source_cycle_id,
                    "source_checkpoint_id": source_checkpoint_id,
                    "mode": mode,
                    "overrides_digest": overrides_digest,
                },
                connection,
            )
        return replay_id

    def approve(self, replay_id: str, *, approved_by: str = "owner") -> dict[str, Any]:
        if not approved_by.strip():
            raise ValueError("approved_by is required")
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM replay_manifests WHERE replay_id=?", (replay_id,)
            ).fetchone()
            if row is None:
                raise KeyError(replay_id)
            if str(row["status"]) == "APPROVED":
                return self.get(replay_id)
            if str(row["status"]) != "PROPOSED":
                raise RuntimeError("replay manifest is not approvable")
            checkpoint = connection.execute(
                "SELECT payload_digest FROM cycle_checkpoints WHERE checkpoint_id=?",
                (row["source_checkpoint_id"],),
            ).fetchone()
            if checkpoint is None or str(checkpoint["payload_digest"]) != str(
                row["source_checkpoint_digest"]
            ):
                raise RuntimeError("source checkpoint drifted")
            now = utc_now()
            connection.execute(
                "UPDATE replay_manifests SET status='APPROVED',approved_at=? WHERE replay_id=?",
                (now, replay_id),
            )
            self.ledger.append(
                "replay_manifest_approved",
                {"replay_id": replay_id, "approved_by": approved_by},
                connection,
            )
        return self.get(replay_id)

    def mark_consumed(self, replay_id: str) -> dict[str, Any]:
        with self.db.transaction() as connection:
            row = connection.execute(
                "SELECT status FROM replay_manifests WHERE replay_id=?", (replay_id,)
            ).fetchone()
            if row is None:
                raise KeyError(replay_id)
            if str(row["status"]) == "CONSUMED":
                return self.get(replay_id)
            if str(row["status"]) != "APPROVED":
                raise RuntimeError("replay manifest is not approved")
            connection.execute(
                "UPDATE replay_manifests SET status='CONSUMED',consumed_at=? WHERE replay_id=?",
                (utc_now(), replay_id),
            )
            self.ledger.append(
                "replay_manifest_consumed", {"replay_id": replay_id}, connection
            )
        return self.get(replay_id)

    def get(self, replay_id: str) -> dict[str, Any]:
        row = self.db.query_one(
            "SELECT * FROM replay_manifests WHERE replay_id=?", (replay_id,)
        )
        if row is None:
            raise KeyError(replay_id)
        return {
            "replay_id": str(row["replay_id"]),
            "source_cycle_id": str(row["source_cycle_id"]),
            "source_checkpoint_id": str(row["source_checkpoint_id"]),
            "source_checkpoint_digest": str(row["source_checkpoint_digest"]),
            "mode": str(row["mode"]),
            "status": str(row["status"]),
            "overrides": json.loads(row["overrides_json"]),
            "overrides_digest": str(row["overrides_digest"]),
            "created_by": str(row["created_by"]),
            "created_at": str(row["created_at"]),
            "approved_at": str(row["approved_at"]) if row["approved_at"] else None,
            "consumed_at": str(row["consumed_at"]) if row["consumed_at"] else None,
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "orphan_source": "SELECT COUNT(*) AS n FROM replay_manifests r LEFT JOIN cycles c ON c.cycle_id=r.source_cycle_id LEFT JOIN cycle_checkpoints j ON j.checkpoint_id=r.source_checkpoint_id WHERE c.cycle_id IS NULL OR j.checkpoint_id IS NULL",
            "cycle_mismatch": "SELECT COUNT(*) AS n FROM replay_manifests r JOIN cycle_checkpoints j ON j.checkpoint_id=r.source_checkpoint_id WHERE r.source_cycle_id<>j.cycle_id",
            "invalid_status": "SELECT COUNT(*) AS n FROM replay_manifests WHERE status NOT IN ('PROPOSED','APPROVED','CONSUMED')",
            "approved_without_time": "SELECT COUNT(*) AS n FROM replay_manifests WHERE status IN ('APPROVED','CONSUMED') AND approved_at IS NULL",
            "consumed_without_time": "SELECT COUNT(*) AS n FROM replay_manifests WHERE status='CONSUMED' AND consumed_at IS NULL",
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts
