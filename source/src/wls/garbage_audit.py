from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
import json
import os
import shutil

from .schemas import new_id, utc_now


LeaseProbe = Callable[[Path], dict[str, Any]]


@dataclass(slots=True)
class GarbageCandidate:
    candidate_id: str
    path: str
    kind: str
    risk: str
    bytes: int
    reason: str
    recommended_action: str
    owner_review_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GarbageAuditor:
    """Read-only filesystem garbage scanner.

    The auditor never deletes files. It produces owner-review candidates that can
    be used for a later explicitly approved cleanup flow.
    """

    CACHE_DIR_NAMES = {
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        ".nox",
        ".wls_quarantine",
    }
    BUILD_DIR_NAMES = {"build", "htmlcov"}
    SKIP_DIR_NAMES = {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        "venv",
        ".venv",
        "site-packages",
    }
    TEMP_SUFFIXES = {
        ".tmp",
        ".temp",
        ".bak",
        ".old",
        ".orig",
        ".rej",
        ".pyc",
        ".pyo",
        ".log",
    }

    def __init__(
        self,
        *,
        lease_probe: LeaseProbe | None = None,
        max_candidates: int = 500,
    ):
        if max_candidates < 0:
            raise ValueError("max_candidates must be non-negative")
        self.lease_probe = lease_probe
        self.max_candidates = max_candidates

    def audit(self, roots: Iterable[str | Path]) -> dict[str, Any]:
        resolved_roots = self._resolve_roots(roots)
        candidates: list[GarbageCandidate] = []
        truncated = False
        for root in resolved_roots:
            for candidate in self._scan_root(root):
                if len(candidates) >= self.max_candidates:
                    truncated = True
                    break
                candidates.append(candidate)
            if truncated:
                break
        total_bytes = sum(item.bytes for item in candidates)
        by_kind: dict[str, int] = {}
        by_risk: dict[str, int] = {}
        for item in candidates:
            by_kind[item.kind] = by_kind.get(item.kind, 0) + 1
            by_risk[item.risk] = by_risk.get(item.risk, 0) + 1
        return {
            "audit_id": new_id("garbage_audit"),
            "created_at": utc_now(),
            "status": "OWNER_REVIEW_REQUIRED" if candidates else "CLEAN",
            "roots": [str(root) for root in resolved_roots],
            "candidate_count": len(candidates),
            "total_candidate_bytes": total_bytes,
            "by_kind": by_kind,
            "by_risk": by_risk,
            "truncated": truncated,
            "owner_review_required": bool(candidates),
            "cleanup_executed": False,
            "claim_ceiling": (
                "read-only garbage candidate audit only; no filesystem cleanup "
                "was performed"
            ),
            "candidates": [candidate.to_dict() for candidate in candidates],
        }

    def execute_cleanup(
        self, receipt: dict[str, Any], *, approval_reference: str
    ) -> dict[str, Any]:
        if not approval_reference.strip():
            raise PermissionError("cleanup requires an explicit owner approval reference")
        roots = [Path(root).expanduser().resolve() for root in receipt.get("roots", [])]
        if not roots:
            raise ValueError("cleanup receipt has no roots")
        candidates = receipt.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("cleanup receipt candidates must be a list")
        actions: list[dict[str, Any]] = []
        deleted_bytes = 0
        quarantined_bytes = 0
        audit_id = self._safe_name(str(receipt.get("audit_id", "audit")), max_length=96)
        quarantine_root = (roots[0] / ".wls_quarantine" / audit_id).resolve()
        if not self._within_roots(quarantine_root, roots):
            raise PermissionError("quarantine root must remain inside an audit root")
        quarantine_root.mkdir(parents=True, exist_ok=True)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            action = self._cleanup_candidate(candidate, roots, quarantine_root)
            actions.append(action)
            if action["status"] == "DELETED":
                deleted_bytes += int(action.get("bytes", 0))
            if action["status"] == "QUARANTINED":
                quarantined_bytes += int(action.get("bytes", 0))
        updated = dict(receipt)
        updated["cleanup_executed"] = True
        updated["cleanup_executed_at"] = utc_now()
        updated["approval_reference"] = approval_reference
        updated["quarantine_root"] = str(quarantine_root)
        updated["cleanup_actions"] = actions
        updated["deleted_candidate_count"] = sum(
            1 for action in actions if action["status"] == "DELETED"
        )
        updated["deleted_bytes"] = deleted_bytes
        updated["quarantined_candidate_count"] = sum(
            1 for action in actions if action["status"] == "QUARANTINED"
        )
        updated["quarantined_bytes"] = quarantined_bytes
        updated["owner_review_required"] = False
        updated["status"] = (
            "CLEANUP_EXECUTED"
            if any(action["status"] in {"DELETED", "QUARANTINED"} for action in actions)
            else "CLEANUP_NOOP"
        )
        updated["claim_ceiling"] = (
            "owner-approved cleanup receipt for candidates produced by this "
            "garbage audit only; candidates are quarantined where possible and "
            "no non-candidate paths were removed"
        )
        self._write_quarantine_manifest(quarantine_root, updated)
        return updated

    def _cleanup_candidate(
        self, candidate: dict[str, Any], roots: list[Path], quarantine_root: Path
    ) -> dict[str, Any]:
        candidate_id = str(candidate.get("candidate_id", ""))
        raw_path = str(candidate.get("path", ""))
        recommended_action = str(candidate.get("recommended_action", ""))
        risk = str(candidate.get("risk", "")).upper()
        action = {
            "candidate_id": candidate_id,
            "path": raw_path,
            "kind": str(candidate.get("kind", "")),
            "risk": risk,
            "recommended_action": recommended_action,
            "bytes": self._safe_int(candidate.get("bytes", 0)),
        }
        if risk not in {"LOW", "MEDIUM"}:
            return {**action, "status": "SKIPPED", "reason": "risk_not_auto_cleanable"}
        if recommended_action not in {
            "delete_file_after_owner_approval",
            "delete_directory_after_owner_approval",
        }:
            return {**action, "status": "SKIPPED", "reason": "unsupported_action"}
        try:
            path = Path(raw_path).expanduser().resolve()
        except OSError as exc:
            return {**action, "status": "SKIPPED", "reason": f"path_error:{exc}"}
        if not self._within_roots(path, roots) or path in roots:
            return {**action, "status": "SKIPPED", "reason": "outside_audit_roots"}
        if not path.exists():
            return {**action, "status": "MISSING", "reason": "already_absent"}
        try:
            if recommended_action == "delete_file_after_owner_approval":
                if not path.is_file():
                    return {**action, "status": "SKIPPED", "reason": "not_a_file"}
            else:
                if not path.is_dir():
                    return {**action, "status": "SKIPPED", "reason": "not_a_directory"}
            quarantine_target = self._quarantine_target(
                quarantine_root, candidate_id, path.name
            )
            shutil.move(str(path), str(quarantine_target))
        except OSError as exc:
            return {**action, "status": "FAILED", "reason": f"{type(exc).__name__}:{exc}"}
        return {
            **action,
            "status": "QUARANTINED",
            "reason": "owner_approved_candidate_quarantined",
            "quarantine_path": str(quarantine_target),
        }

    def _scan_root(self, root: Path) -> Iterable[GarbageCandidate]:
        for current, dir_names, file_names in os.walk(root):
            current_path = Path(current)
            skipped = [name for name in dir_names if name in self.SKIP_DIR_NAMES]
            dir_names[:] = [
                name for name in dir_names if name not in self.SKIP_DIR_NAMES
            ]
            for name in list(dir_names):
                path = current_path / name
                if name in self.CACHE_DIR_NAMES:
                    yield self._candidate(
                        path,
                        "cache_dir",
                        "LOW",
                        "Recognized generated cache directory.",
                        "delete_directory_after_owner_approval",
                    )
                    dir_names.remove(name)
                elif name in self.BUILD_DIR_NAMES:
                    yield self._candidate(
                        path,
                        "build_artifact_dir",
                        "MEDIUM",
                        "Recognized generated build or coverage output directory.",
                        "delete_directory_after_owner_approval",
                    )
                    dir_names.remove(name)
            for name in file_names:
                path = current_path / name
                suffix = path.suffix.lower()
                if suffix in self.TEMP_SUFFIXES:
                    yield self._candidate(
                        path,
                        "temporary_file",
                        "LOW" if suffix in {".pyc", ".pyo", ".tmp", ".temp"} else "MEDIUM",
                        f"Recognized generated or temporary suffix {suffix}.",
                        "delete_file_after_owner_approval",
                    )
                elif suffix == ".lock":
                    candidate = self._lock_candidate(path)
                    if candidate is not None:
                        yield candidate
            if skipped:
                continue

    def _lock_candidate(self, path: Path) -> GarbageCandidate | None:
        if self.lease_probe is None:
            return None
        state = self.lease_probe(path)
        if not state.get("present") or state.get("held"):
            return None
        return self._candidate(
            path,
            "stale_lock_file",
            "MEDIUM",
            "Lock file exists but is not currently held.",
            "delete_file_after_owner_approval",
        )

    def _candidate(
        self,
        path: Path,
        kind: str,
        risk: str,
        reason: str,
        recommended_action: str,
    ) -> GarbageCandidate:
        return GarbageCandidate(
            candidate_id=new_id("garbage_candidate"),
            path=str(path),
            kind=kind,
            risk=risk,
            bytes=self._path_size(path),
            reason=reason,
            recommended_action=recommended_action,
        )

    @classmethod
    def _resolve_roots(cls, roots: Iterable[str | Path]) -> list[Path]:
        resolved: list[Path] = []
        for root in roots:
            path = Path(root).expanduser().resolve()
            if path.exists() and path not in resolved:
                resolved.append(path)
        if not resolved:
            raise FileNotFoundError("no garbage audit roots exist")
        return resolved

    @staticmethod
    def _within_roots(path: Path, roots: list[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    @staticmethod
    def _quarantine_target(root: Path, candidate_id: str, name: str) -> Path:
        safe_id = GarbageAuditor._safe_name(candidate_id, max_length=80)
        safe_name = GarbageAuditor._safe_name(name, max_length=120)
        target = root / f"{safe_id}_{safe_name}"
        counter = 1
        while target.exists():
            target = root / f"{safe_id}_{counter}_{safe_name}"
            counter += 1
        return target

    @staticmethod
    def _safe_name(value: str, *, max_length: int) -> str:
        safe = "".join(
            char if char.isalnum() or char in {"_", "-", "."} else "_"
            for char in value
        ).strip(".")
        return (safe or "candidate")[:max_length]

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _write_quarantine_manifest(root: Path, receipt: dict[str, Any]) -> None:
        manifest = {
            "audit_id": receipt.get("audit_id"),
            "cleanup_executed_at": receipt.get("cleanup_executed_at"),
            "approval_reference": receipt.get("approval_reference"),
            "quarantined_candidate_count": receipt.get("quarantined_candidate_count", 0),
            "quarantined_bytes": receipt.get("quarantined_bytes", 0),
            "cleanup_actions": receipt.get("cleanup_actions", []),
        }
        (root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    @classmethod
    def _path_size(cls, path: Path) -> int:
        try:
            if path.is_file():
                return path.stat().st_size
            total = 0
            for current, dir_names, file_names in os.walk(path):
                dir_names[:] = [
                    name for name in dir_names if name not in cls.SKIP_DIR_NAMES
                ]
                for name in file_names:
                    try:
                        total += (Path(current) / name).stat().st_size
                    except OSError:
                        continue
            return total
        except OSError:
            return 0
