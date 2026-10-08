"""Candidate-only, content-addressed RSI artifact admission.

Uses the canonical WLS EvidenceLedger; this is NOT a second runtime, not an
executor, and not a sandbox. Never execute admitted content on a trusted host.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from .evidence import EvidenceLedger
from .schemas import digest_json


class ArtifactIntegrityError(ValueError):
    """An artifact is missing, tampered with, or outside its frozen scope."""


def _safe_id(value: str) -> bool:
    return bool(
        isinstance(value, str)
        and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value)
        and not value.endswith(".")
    )


def _safe_relative(value: str) -> bool:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        return False
    parsed = PurePosixPath(value)
    return (
        not parsed.is_absolute()
        and parsed.as_posix() == value
        and all(
            part not in {"", ".", ".."} and not part.endswith((".", " "))
            for part in parsed.parts
        )
    )


class RsiArtifactGate:
    """Persist immutable candidate source with an exact file-allowlist."""

    def __init__(
        self,
        root: Path,
        ledger: EvidenceLedger,
        *,
        allowed_files: frozenset[str],
        max_total_bytes: int = 1_000_000,
    ) -> None:
        if not allowed_files or "manifest.json" in allowed_files or any(
            not _safe_relative(item) for item in allowed_files
        ):
            raise ValueError("allowed_files must list canonical relative file names")
        if max_total_bytes < 1:
            raise ValueError("max_total_bytes must be positive")
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.ledger = ledger
        self.allowed_files = allowed_files
        self.max_total_bytes = max_total_bytes

    def register(
        self,
        *,
        artifact_id: str,
        parent_id: str | None,
        generation: int,
        branch: int,
        files: dict[str, bytes],
        policy_digest: str,
        evaluator_digest: str,
    ) -> dict[str, Any]:
        if not _safe_id(artifact_id):
            raise ValueError("unsafe artifact identifier")
        if parent_id is not None and not _safe_id(parent_id):
            raise ValueError("unsafe parent identifier")
        if generation < 0 or branch < 0 or not files:
            raise ValueError("invalid artifact generation or files")
        if set(files).difference(self.allowed_files):
            raise ValueError("candidate attempted a non-allowlisted file")
        if any(not isinstance(data, bytes) for data in files.values()):
            raise TypeError("candidate files must be immutable bytes")
        if sum(len(data) for data in files.values()) > self.max_total_bytes:
            raise ValueError("candidate exceeds size cap")
        if any(
            not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in (policy_digest, evaluator_digest)
        ):
            raise ValueError("policy/evaluator digest must be lowercase SHA-256")
        if generation == 0:
            if parent_id is not None:
                raise ValueError("baseline cannot have a parent")
        else:
            if parent_id is None:
                raise ValueError("non-baseline candidate requires a parent")
            parent = self.verify(parent_id)
            if parent["generation"] != generation - 1:
                raise ValueError("candidate generation must follow its parent")
            if (parent["policy_digest"] != policy_digest
                    or parent["evaluator_digest"] != evaluator_digest):
                raise ValueError("candidate cannot change inherited policy or evaluator")
        if (self.root / artifact_id).exists():
            raise ValueError("artifact ID already admitted")

        file_index = {
            filename: {
                "sha256": hashlib.sha256(data).hexdigest(),
                "bytes": len(data),
            }
            for filename, data in sorted(files.items())
        }
        payload = {
            "artifact_id": artifact_id,
            "parent_id": parent_id,
            "generation": generation,
            "branch": branch,
            "policy_digest": policy_digest,
            "evaluator_digest": evaluator_digest,
            "files": file_index,
            "authority": "candidate_only",
        }
        payload["manifest_digest"] = digest_json(payload)
        with tempfile.TemporaryDirectory(prefix=".rsi-stage-", dir=self.root) as scratch:
            stage = Path(scratch)
            for filename, data in files.items():
                target = stage.joinpath(*PurePosixPath(filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (stage / "manifest.json").write_text(
                json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if (self.root / artifact_id).exists():
                raise ValueError("concurrent artifact admission collision")
            stage.rename(self.root / artifact_id)
        try:
            self.ledger.append(
                "rsi_candidate_artifact_registered",
                {
                    "artifact_id": artifact_id,
                    "parent_id": parent_id,
                    "manifest_digest": payload["manifest_digest"],
                },
            )
        except Exception:
            # File rename and SQLite append cannot be one atomic operation.
            # If append certainly did not persist, compensate the staged
            # rename so this generation can retry under the same candidate
            # identity. If the DB cannot be read, or the signed receipt was
            # committed before an exceptional return, preserve the artifact:
            # deleting it would invalidate canonical WLS evidence.
            try:
                rows = self.ledger.db.query_all(
                    "SELECT payload_json FROM evidence "
                    "WHERE event_type='rsi_candidate_artifact_registered'"
                )
            except (OSError, sqlite3.Error):
                rows = None
            if rows is not None and not any(
                json.loads(row["payload_json"]).get("artifact_id") == artifact_id
                for row in rows
            ):
                folder = self.root / artifact_id
                if folder.is_dir() and not folder.is_symlink():
                    shutil.rmtree(folder)
            raise
        return payload

    def verify(self, artifact_id: str) -> dict[str, Any]:
        """Check every signed ancestor, not merely the selected descendant.

        An intact child whose parent was deleted or tampered with is not a
        trustworthy evolved agent. Traverse iteratively: deep lineages must
        not trigger recursion limits or rescan the ledger per generation.
        """
        if not _safe_id(artifact_id):
            raise ArtifactIntegrityError("unsafe artifact identifier")
        ledger_ok, ledger_reason = self.ledger.verify()
        if not ledger_ok:
            raise ArtifactIntegrityError(f"WLS evidence ledger invalid: {ledger_reason}")
        receipts = [
            json.loads(row["payload_json"])
            for row in self.ledger.db.query_all(
                "SELECT payload_json FROM evidence "
                "WHERE event_type='rsi_candidate_artifact_registered'"
            )
        ]
        receipt_index: dict[str, list[dict[str, Any]]] = {}
        for item in receipts:
            if isinstance(item, dict) and isinstance(item.get("artifact_id"), str):
                receipt_index.setdefault(item["artifact_id"], []).append(item)

        cursor = artifact_id
        seen: set[str] = set()
        child: dict[str, Any] | None = None
        requested: dict[str, Any] | None = None
        while True:
            if not _safe_id(cursor) or cursor in seen:
                raise ArtifactIntegrityError("candidate ancestry is invalid or cyclic")
            seen.add(cursor)
            folder = self.root / cursor
            manifest_path = folder / "manifest.json"
            if folder.is_symlink() or not manifest_path.is_file() or manifest_path.is_symlink():
                raise ArtifactIntegrityError("candidate manifest missing or symlinked")
            try:
                payload = json.loads(
                    manifest_path.read_text(encoding="utf-8"),
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
                )
            except (ValueError, OSError) as exc:
                raise ArtifactIntegrityError("invalid candidate manifest") from exc
            if not isinstance(payload, dict) or payload.get("artifact_id") != cursor:
                raise ArtifactIntegrityError("candidate identity mismatch")
            manifest_digest = payload.pop("manifest_digest", None)
            if manifest_digest != digest_json(payload):
                raise ArtifactIntegrityError("candidate manifest digest mismatch")
            payload["manifest_digest"] = manifest_digest
            matching_receipts = receipt_index.get(cursor, [])
            if (
                len(matching_receipts) != 1
                or matching_receipts[0].get("manifest_digest") != manifest_digest
                or matching_receipts[0].get("parent_id") != payload.get("parent_id")
            ):
                raise ArtifactIntegrityError("candidate manifest is not bound to signed WLS evidence")
            generation = payload.get("generation")
            parent_id = payload.get("parent_id")
            if not isinstance(generation, int) or isinstance(generation, bool) or generation < 0:
                raise ArtifactIntegrityError("candidate generation is invalid")
            if parent_id is not None and not _safe_id(parent_id):
                raise ArtifactIntegrityError("candidate parent identifier is invalid")
            if not isinstance(payload.get("policy_digest"), str) or not isinstance(
                payload.get("evaluator_digest"), str
            ):
                raise ArtifactIntegrityError("candidate policy or evaluator is invalid")
            if child is not None:
                if child["generation"] != generation + 1:
                    raise ArtifactIntegrityError("candidate lineage generation mismatch")
                if (child["policy_digest"] != payload["policy_digest"]
                        or child["evaluator_digest"] != payload["evaluator_digest"]):
                    raise ArtifactIntegrityError("candidate lineage policy/evaluator mismatch")
            index = payload.get("files")
            if not isinstance(index, dict) or not index or set(index).difference(self.allowed_files):
                raise ArtifactIntegrityError("candidate contains unauthorized files")
            actual: set[str] = set()
            for path in folder.rglob("*"):
                if path.is_symlink():
                    raise ArtifactIntegrityError("candidate contains a symlink")
                if not path.is_file():
                    continue
                rel = path.relative_to(folder).as_posix()
                if rel != "manifest.json":
                    actual.add(rel)
            if actual != set(index):
                raise ArtifactIntegrityError("candidate file inventory changed")
            for relative, record in index.items():
                if not _safe_relative(relative) or not isinstance(record, dict):
                    raise ArtifactIntegrityError("candidate file index is invalid")
                blob = folder.joinpath(*PurePosixPath(relative).parts).read_bytes()
                if (
                    len(blob) != record.get("bytes")
                    or hashlib.sha256(blob).hexdigest() != record.get("sha256")
                ):
                    raise ArtifactIntegrityError("candidate source bytes modified")
            if requested is None:
                requested = payload
            if parent_id is None:
                if generation != 0:
                    raise ArtifactIntegrityError("candidate lineage lacks baseline")
                return requested
            if generation == 0:
                raise ArtifactIntegrityError("candidate baseline cannot have a parent")
            child = payload
            cursor = parent_id
