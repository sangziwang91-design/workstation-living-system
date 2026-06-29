from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence
import json
import sqlite3
import threading

from wls.db import EXAMINER_SCHEMA_SQL

from .models import ExamResult, ExaminerVersion, PromotionDecision


class StandaloneDatabase:
    """Minimal WLS-compatible SQLite adapter used by sandbox/tests."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    @contextmanager
    def transaction(self, immediate: bool = True) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
                yield connection
                if connection.in_transaction:
                    connection.execute("COMMIT")
            except Exception:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                raise
            finally:
                connection.close()

    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> int:
        with self.transaction() as connection:
            return connection.execute(sql, parameters).rowcount

    def query_one(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        connection = self.connect()
        try:
            return connection.execute(sql, parameters).fetchone()
        finally:
            connection.close()

    def query_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        connection = self.connect()
        try:
            return list(connection.execute(sql, parameters).fetchall())
        finally:
            connection.close()

    def integrity_check(self) -> tuple[bool, str]:
        connection = self.connect()
        try:
            result = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            if result.lower() != "ok":
                return False, result
            if foreign_rows:
                return False, f"foreign_key_violations={len(foreign_rows)}"
            return True, "ok"
        finally:
            connection.close()


class ExaminerStore:
    def __init__(self, db: Any, ledger: Any | None = None, *, ensure_schema: bool = True):
        self.db = db
        self.ledger = ledger
        if ensure_schema:
            self._ensure_tables()

    def _ensure_tables(self) -> None:
        with self.db.transaction() as connection:
            for statement in EXAMINER_SCHEMA_SQL:
                connection.execute(statement)

    def _append_ledger(self, event_type: str, payload: dict[str, Any], connection: Any) -> None:
        if self.ledger is not None:
            self.ledger.append(event_type, payload, connection)

    def register_version(self, version: ExaminerVersion, status: str = "CANDIDATE") -> None:
        with self.db.transaction() as connection:
            inserted = self._register_version(connection, version, status)
            if inserted:
                self._append_ledger(
                    "examiner_version_registered",
                    {"version_id": version.version_id, "version_digest": version.digest},
                    connection,
                )

    @staticmethod
    def _version_row_payload(version: ExaminerVersion) -> tuple[Any, ...]:
        return (
            version.version_id,
            version.digest,
            version.parent_version_id,
            version.epoch_id,
            version.constitution_digest,
            version.anchor_manifest_digest,
            version.implementation_digest,
            json.dumps(list(version.enabled_rules), sort_keys=True),
            version.created_at,
        )

    def _register_version(
        self, connection: Any, version: ExaminerVersion, status: str
    ) -> bool:
        existing = connection.execute(
            "SELECT * FROM examiner_versions WHERE version_id=? OR version_digest=?",
            (version.version_id, version.digest),
        ).fetchone()
        if existing is not None:
            expected = self._version_row_payload(version)
            actual = (
                str(existing["version_id"]),
                str(existing["version_digest"]),
                existing["parent_version_id"],
                str(existing["epoch_id"]),
                str(existing["constitution_digest"]),
                str(existing["anchor_manifest_digest"]),
                str(existing["implementation_digest"]),
                str(existing["enabled_rules_json"]),
                str(existing["created_at"]),
            )
            if actual != expected:
                raise RuntimeError(
                    "examiner version identity collision: version IDs and digests are immutable"
                )
            return False
        connection.execute(
            """
            INSERT INTO examiner_versions(
                version_id,version_digest,parent_version_id,epoch_id,
                constitution_digest,anchor_manifest_digest,implementation_digest,
                enabled_rules_json,
                created_at,status
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (*self._version_row_payload(version), status),
        )
        return True

    def open_epoch(self, version: ExaminerVersion, opened_at: str) -> None:
        with self.db.transaction() as connection:
            active = connection.execute(
                "SELECT epoch_id FROM examiner_epochs WHERE status='FROZEN'"
            ).fetchone()
            if active is not None and str(active["epoch_id"]) != version.epoch_id:
                raise RuntimeError(f"active frozen epoch already exists: {active['epoch_id']}")
            existing = connection.execute(
                "SELECT * FROM examiner_epochs WHERE epoch_id=?", (version.epoch_id,)
            ).fetchone()
            if existing is not None:
                exact = (
                    str(existing["evaluator_version_id"]) == version.version_id
                    and str(existing["evaluator_version_digest"]) == version.digest
                    and str(existing["constitution_digest"]) == version.constitution_digest
                    and str(existing["anchor_manifest_digest"])
                    == version.anchor_manifest_digest
                    and str(existing["status"]) == "FROZEN"
                )
                if exact:
                    return
                raise RuntimeError("epoch identity collision or closed-epoch reuse")
            connection.execute(
                """
                INSERT INTO examiner_epochs(
                    epoch_id,evaluator_version_id,evaluator_version_digest,
                    constitution_digest,anchor_manifest_digest,implementation_digest,
                    status,opened_at
                ) VALUES (?,?,?,?,?,?,'FROZEN',?)
                """,
                (
                    version.epoch_id,
                    version.version_id,
                    version.digest,
                    version.constitution_digest,
                    version.anchor_manifest_digest,
                    version.implementation_digest,
                    opened_at,
                ),
            )
            connection.execute(
                "UPDATE examiner_versions SET status='ACTIVE' WHERE version_id=?",
                (version.version_id,),
            )
            self._append_ledger(
                "examiner_epoch_opened",
                {
                    "epoch_id": version.epoch_id,
                    "version_id": version.version_id,
                    "version_digest": version.digest,
                },
                connection,
            )

    def assert_epoch_frozen(self, version: ExaminerVersion) -> None:
        row = self.db.query_one(
            "SELECT * FROM examiner_epochs WHERE epoch_id=?", (version.epoch_id,)
        )
        if row is None or row["status"] != "FROZEN":
            raise RuntimeError("examiner epoch is not frozen")
        if str(row["evaluator_version_digest"]) != version.digest:
            raise RuntimeError("epoch evaluator digest mismatch")
        if str(row["constitution_digest"]) != version.constitution_digest:
            raise RuntimeError("epoch constitution digest mismatch")
        if str(row["anchor_manifest_digest"]) != version.anchor_manifest_digest:
            raise RuntimeError("epoch anchor manifest digest mismatch")
        if str(row["implementation_digest"]) != version.implementation_digest:
            raise RuntimeError("epoch implementation digest mismatch")

    def record_verdict(self, result: ExamResult) -> None:
        with self.db.transaction() as connection:
            if result.nonce:
                replay = connection.execute(
                    "SELECT verdict_id FROM examiner_verdicts WHERE nonce=?", (result.nonce,)
                ).fetchone()
                if replay is not None:
                    raise RuntimeError(f"submission nonce replay: {result.nonce}")
            connection.execute(
                """
                INSERT INTO examiner_verdicts(
                    verdict_id,verdict_digest,candidate_id,candidate_digest,
                    version_id,version_digest,epoch_id,verdict,result_json,
                    created_at,nonce
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    result.verdict_id,
                    result.digest,
                    result.candidate_id,
                    result.candidate_digest,
                    result.version_id,
                    result.version_digest,
                    result.epoch_id,
                    result.verdict.value,
                    json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True),
                    result.created_at,
                    result.nonce,
                ),
            )
            self._append_ledger(
                "examiner_verdict_recorded",
                {
                    "verdict_id": result.verdict_id,
                    "verdict_digest": result.digest,
                    "candidate_id": result.candidate_id,
                    "version_id": result.version_id,
                    "epoch_id": result.epoch_id,
                    "verdict": result.verdict.value,
                },
                connection,
            )

    def mark_epoch_verdicts_stale(self, epoch_id: str, reason: str) -> int:
        with self.db.transaction() as connection:
            count = connection.execute(
                """
                UPDATE examiner_verdicts
                SET stale=1,stale_reason=?
                WHERE epoch_id=? AND stale=0
                """,
                (reason, epoch_id),
            ).rowcount
            self._append_ledger(
                "examiner_selective_erasure",
                {"epoch_id": epoch_id, "stale_count": count, "reason": reason},
                connection,
            )
            return int(count)

    def close_epoch(
        self, epoch_id: str, closed_at: str, owner_approval_ref: str | None = None
    ) -> None:
        with self.db.transaction() as connection:
            updated = connection.execute(
                """
                UPDATE examiner_epochs
                SET status='CLOSED',closed_at=?,owner_approval_ref=?
                WHERE epoch_id=? AND status='FROZEN'
                """,
                (closed_at, owner_approval_ref, epoch_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError(f"cannot close epoch {epoch_id}")
            connection.execute(
                """
                UPDATE examiner_versions SET status='RETIRED'
                WHERE version_id=(
                    SELECT evaluator_version_id FROM examiner_epochs WHERE epoch_id=?
                )
                """,
                (epoch_id,),
            )
            self._append_ledger(
                "examiner_epoch_closed",
                {"epoch_id": epoch_id, "owner_approval_ref": owner_approval_ref},
                connection,
            )

    def record_promotion(
        self,
        promotion_id: str,
        decision: PromotionDecision,
        status: str,
        created_at: str,
        owner_actor: str | None = None,
        owner_approval_ref: str | None = None,
        applied_at: str | None = None,
    ) -> None:
        with self.db.transaction() as connection:
            existing = connection.execute(
                "SELECT * FROM examiner_promotions WHERE promotion_id=?", (promotion_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO examiner_promotions(
                        promotion_id,incumbent_version_id,challenger_version_id,
                        decision_digest,decision_json,status,owner_actor,owner_approval_ref,
                        created_at,applied_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        promotion_id,
                        decision.incumbent_version_id,
                        decision.challenger_version_id,
                        decision.digest,
                        json.dumps(decision.to_dict(), sort_keys=True),
                        status,
                        owner_actor,
                        owner_approval_ref,
                        created_at,
                        applied_at,
                    ),
                )
            else:
                if str(existing["decision_digest"]) != decision.digest:
                    raise RuntimeError("promotion decision is immutable")
                if str(existing["status"]) != status:
                    raise RuntimeError("use atomic promotion to change promotion status")
            self._append_ledger(
                "examiner_promotion_recorded",
                {
                    "promotion_id": promotion_id,
                    "status": status,
                    "incumbent": decision.incumbent_version_id,
                    "challenger": decision.challenger_version_id,
                },
                connection,
            )

    def apply_promotion_atomic(
        self,
        promotion_id: str,
        decision: PromotionDecision,
        *,
        incumbent: ExaminerVersion,
        challenger: ExaminerVersion,
        applied_at: str,
        owner_actor: str,
        owner_approval_ref: str,
    ) -> int:
        """Apply evaluator replacement as one database/evidence transaction."""

        with self.db.transaction() as connection:
            request = connection.execute(
                "SELECT * FROM examiner_promotions WHERE promotion_id=?", (promotion_id,)
            ).fetchone()
            if request is None:
                raise RuntimeError("promotion request is missing")
            if str(request["status"]) != "AWAITING_OWNER":
                raise RuntimeError("promotion request is not awaiting owner approval")
            if str(request["decision_digest"]) != decision.digest:
                raise RuntimeError("promotion request decision digest mismatch")
            active = connection.execute(
                "SELECT * FROM examiner_epochs WHERE status='FROZEN'"
            ).fetchone()
            if active is None:
                raise RuntimeError("active examiner epoch is missing")
            if (
                str(active["epoch_id"]) != incumbent.epoch_id
                or str(active["evaluator_version_digest"]) != incumbent.digest
            ):
                raise RuntimeError("active examiner epoch changed after comparison")
            existing_epoch = connection.execute(
                "SELECT epoch_id FROM examiner_epochs WHERE epoch_id=?",
                (challenger.epoch_id,),
            ).fetchone()
            if existing_epoch is not None:
                raise RuntimeError("challenger epoch ID has already been used")
            inserted = self._register_version(connection, challenger, "CANDIDATE")
            if inserted:
                self._append_ledger(
                    "examiner_version_registered",
                    {
                        "version_id": challenger.version_id,
                        "version_digest": challenger.digest,
                    },
                    connection,
                )
            stale_count = connection.execute(
                """
                UPDATE examiner_verdicts SET stale=1,stale_reason=?
                WHERE epoch_id=? AND stale=0
                """,
                (
                    f"evaluator_replaced_by:{challenger.version_id}",
                    incumbent.epoch_id,
                ),
            ).rowcount
            connection.execute(
                """
                UPDATE examiner_epochs
                SET status='CLOSED',closed_at=?,owner_approval_ref=?
                WHERE epoch_id=? AND status='FROZEN'
                """,
                (applied_at, owner_approval_ref, incumbent.epoch_id),
            )
            connection.execute(
                "UPDATE examiner_versions SET status='RETIRED' WHERE version_id=?",
                (incumbent.version_id,),
            )
            connection.execute(
                """
                INSERT INTO examiner_epochs(
                    epoch_id,evaluator_version_id,evaluator_version_digest,
                    constitution_digest,anchor_manifest_digest,implementation_digest,
                    status,opened_at,owner_approval_ref
                ) VALUES (?,?,?,?,?,?,'FROZEN',?,?)
                """,
                (
                    challenger.epoch_id,
                    challenger.version_id,
                    challenger.digest,
                    challenger.constitution_digest,
                    challenger.anchor_manifest_digest,
                    challenger.implementation_digest,
                    applied_at,
                    owner_approval_ref,
                ),
            )
            connection.execute(
                "UPDATE examiner_versions SET status='ACTIVE' WHERE version_id=?",
                (challenger.version_id,),
            )
            updated = connection.execute(
                """
                UPDATE examiner_promotions
                SET status='APPLIED',owner_actor=?,owner_approval_ref=?,applied_at=?
                WHERE promotion_id=? AND status='AWAITING_OWNER' AND decision_digest=?
                """,
                (
                    owner_actor,
                    owner_approval_ref,
                    applied_at,
                    promotion_id,
                    decision.digest,
                ),
            ).rowcount
            if updated != 1:
                raise RuntimeError("promotion request transition failed")
            self._append_ledger(
                "examiner_selective_erasure",
                {
                    "epoch_id": incumbent.epoch_id,
                    "stale_count": int(stale_count),
                    "reason": f"evaluator_replaced_by:{challenger.version_id}",
                },
                connection,
            )
            self._append_ledger(
                "examiner_epoch_closed",
                {
                    "epoch_id": incumbent.epoch_id,
                    "owner_approval_ref": owner_approval_ref,
                },
                connection,
            )
            self._append_ledger(
                "examiner_epoch_opened",
                {
                    "epoch_id": challenger.epoch_id,
                    "version_id": challenger.version_id,
                    "version_digest": challenger.digest,
                },
                connection,
            )
            self._append_ledger(
                "examiner_promotion_recorded",
                {
                    "promotion_id": promotion_id,
                    "status": "APPLIED",
                    "incumbent": incumbent.version_id,
                    "challenger": challenger.version_id,
                    "decision_digest": decision.digest,
                },
                connection,
            )
            return int(stale_count)

    def active_epoch(self) -> dict[str, Any] | None:
        row = self.db.query_one(
            "SELECT * FROM examiner_epochs WHERE status='FROZEN' ORDER BY opened_at DESC LIMIT 1"
        )
        return None if row is None else dict(row)

    def verdict_rows(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.query_all("SELECT * FROM examiner_verdicts")]

    def integrity_check(self) -> tuple[bool, str]:
        return self.db.integrity_check()
