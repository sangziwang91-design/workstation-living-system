from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
import hashlib
import json
import os
import shutil


ALLOWED_ROUND_STATUSES = {
    "PENDING",
    "RUNNING",
    "PASS",
    "FAIL",
    "BLOCKED",
    "OWNER_REVIEW",
    "ROLLED_BACK",
}
TERMINAL_BLOCKING_STATUSES = {"FAIL", "BLOCKED", "OWNER_REVIEW"}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def validate_campaign_spec(spec: dict[str, Any]) -> list[str]:
    rounds = spec.get("rounds")
    if not isinstance(rounds, list):
        raise ValueError("campaign spec must contain rounds[]")
    expected = [f"R{index:02d}" for index in range(1, 31)]
    actual = [item.get("round_id") for item in rounds if isinstance(item, dict)]
    if actual != expected:
        raise ValueError(f"round sequence must be R01..R30, got {actual}")
    required = {
        "round_id",
        "title",
        "phase",
        "entry_conditions",
        "operations",
        "pass_conditions",
        "fail_conditions",
        "automation_level",
        "owner_gate",
        "rollback",
        "claim_ceiling",
    }
    for item in rounds:
        missing = required - set(item)
        if missing:
            raise ValueError(f"{item.get('round_id', '<unknown>')} missing {missing}")
        if not isinstance(item["owner_gate"], bool):
            raise ValueError(f"{item['round_id']} owner_gate must be boolean")
    return expected


@dataclass(slots=True)
class CampaignPaths:
    install_root: Path
    live_home: Path
    campaign_home: Path
    wls_python: Path

    def resolve(self) -> "CampaignPaths":
        return CampaignPaths(
            install_root=self.install_root.expanduser().resolve(),
            live_home=self.live_home.expanduser().resolve(),
            campaign_home=self.campaign_home.expanduser().resolve(),
            wls_python=self.wls_python.expanduser().resolve(),
        )

    def validate(self) -> None:
        resolved = self.resolve()
        if not resolved.install_root.exists():
            raise FileNotFoundError(f"install root not found: {resolved.install_root}")
        if not resolved.live_home.exists():
            raise FileNotFoundError(f"live home not found: {resolved.live_home}")
        if not resolved.wls_python.exists():
            raise FileNotFoundError(f"WLS Python not found: {resolved.wls_python}")
        if resolved.live_home == resolved.campaign_home:
            raise ValueError("live_home and campaign_home must be different paths")
        try:
            resolved.campaign_home.relative_to(resolved.live_home)
        except ValueError:
            pass
        else:
            raise ValueError("campaign_home must not be inside live_home")


class CampaignLock:
    def __init__(self, path: Path):
        self.path = path
        self.acquired = False

    def __enter__(self) -> "CampaignLock":
        try:
            self.path.mkdir(parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise RuntimeError(f"campaign lock is already held: {self.path}") from exc
        atomic_write_json(
            self.path / "owner.json",
            {"pid": os.getpid(), "acquired_at": utc_now(), "cwd": str(Path.cwd())},
        )
        self.acquired = True
        return self

    def __exit__(self, exc_type, exc, tb) -> Literal[False]:
        if self.acquired:
            shutil.rmtree(self.path, ignore_errors=True)
            self.acquired = False
        return False


@dataclass(slots=True)
class CampaignState:
    path: Path
    spec: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load_or_create(
        cls, path: Path, spec: dict[str, Any], paths: CampaignPaths
    ) -> "CampaignState":
        round_ids = validate_campaign_spec(spec)
        if path.exists():
            data = load_json(path)
        else:
            data = {
                "schema_version": 1,
                "campaign_id": spec.get("campaign_id", "WLS-LIFE-EVOLUTION-30-001"),
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "paths": {
                    "install_root": str(paths.install_root),
                    "live_home": str(paths.live_home),
                    "campaign_home": str(paths.campaign_home),
                    "wls_python": str(paths.wls_python),
                },
                "automation_level": "LEVEL_0",
                "rounds": {
                    round_id: {
                        "status": "PENDING",
                        "started_at": None,
                        "finished_at": None,
                        "evidence": [],
                        "verdict": None,
                    }
                    for round_id in round_ids
                },
            }
            atomic_write_json(path, data)
        state = cls(path=path, spec=spec, data=data)
        state._validate_state_rounds(round_ids)
        return state

    def _validate_state_rounds(self, round_ids: list[str]) -> None:
        rounds = self.data.get("rounds")
        if not isinstance(rounds, dict):
            raise ValueError("campaign state must contain rounds{}")
        if list(rounds) != round_ids:
            raise ValueError("campaign state round order does not match spec")
        for round_id, value in rounds.items():
            status = value.get("status")
            if status not in ALLOWED_ROUND_STATUSES:
                raise ValueError(f"{round_id} has invalid status {status!r}")

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        atomic_write_json(self.path, self.data)

    def round_status(self, round_id: str) -> str:
        return str(self.data["rounds"][round_id]["status"])

    def can_start(self, round_id: str) -> tuple[bool, str]:
        previous = self.previous_rounds(round_id)
        for prior in previous:
            status = self.round_status(prior)
            if status != "PASS":
                return False, f"{round_id} is blocked by {prior} status {status}"
        status = self.round_status(round_id)
        if status in {"PASS", "OWNER_REVIEW"}:
            return False, f"{round_id} already finished with {status}"
        if status in {"FAIL", "BLOCKED"}:
            return False, f"{round_id} is terminal {status}; use rollback/manual review"
        return True, "ready"

    def previous_rounds(self, round_id: str) -> list[str]:
        keys = list(self.data["rounds"])
        index = keys.index(round_id)
        return keys[:index]

    def mark_running(self, round_id: str) -> None:
        round_state = self.data["rounds"][round_id]
        round_state["status"] = "RUNNING"
        round_state["started_at"] = round_state.get("started_at") or utc_now()
        round_state["finished_at"] = None
        self.save()

    def mark_pass(self, round_id: str, verdict: dict[str, Any]) -> None:
        round_state = self.data["rounds"][round_id]
        round_state["status"] = "PASS"
        round_state["finished_at"] = utc_now()
        round_state["verdict"] = verdict
        if round_id == "R05":
            self.data["automation_level"] = "LEVEL_1"
        elif round_id == "R15":
            self.data["automation_level"] = "LEVEL_3"
        self.save()

    def mark_fail(self, round_id: str, verdict: dict[str, Any]) -> None:
        round_state = self.data["rounds"][round_id]
        round_state["status"] = "FAIL"
        round_state["finished_at"] = utc_now()
        round_state["verdict"] = verdict
        self.block_after(round_id, f"blocked after {round_id} failure")
        self.save()

    def mark_owner_review(self, round_id: str, verdict: dict[str, Any]) -> None:
        round_state = self.data["rounds"][round_id]
        round_state["status"] = "OWNER_REVIEW"
        round_state["finished_at"] = utc_now()
        round_state["verdict"] = verdict
        self.block_after(round_id, f"blocked pending owner review after {round_id}")
        self.save()

    def block_after(self, round_id: str, reason: str) -> None:
        keys = list(self.data["rounds"])
        start = keys.index(round_id) + 1
        for later in keys[start:]:
            value = self.data["rounds"][later]
            if value["status"] == "PENDING":
                value["status"] = "BLOCKED"
                value["verdict"] = {"reason": reason, "blocked_at": utc_now()}

    def append_evidence(self, round_id: str, evidence_id: str) -> None:
        self.data["rounds"][round_id]["evidence"].append(evidence_id)
        self.save()


class EvidenceManifest:
    def __init__(self, campaign_home: Path):
        self.root = campaign_home / "campaign_evidence"
        self.manifest_path = self.root / "manifest.json"
        self.root.mkdir(parents=True, exist_ok=True)
        if self.manifest_path.exists():
            self.data = load_json(self.manifest_path)
        else:
            self.data = {"schema_version": 1, "created_at": utc_now(), "records": []}
            self.save()

    def save(self) -> None:
        self.data["updated_at"] = utc_now()
        atomic_write_json(self.manifest_path, self.data)

    def record_file(
        self, round_id: str, label: str, path: Path, metadata: dict[str, Any] | None = None
    ) -> str:
        resolved = path.resolve()
        record_id = f"ev-{len(self.data['records']) + 1:04d}"
        record = {
            "id": record_id,
            "round_id": round_id,
            "kind": "file",
            "label": label,
            "path": str(resolved),
            "sha256": sha256_file(resolved) if resolved.exists() and resolved.is_file() else None,
            "bytes": resolved.stat().st_size if resolved.exists() and resolved.is_file() else None,
            "recorded_at": utc_now(),
            "metadata": metadata or {},
        }
        self.data["records"].append(record)
        self.save()
        return record_id

    def record_command(
        self,
        round_id: str,
        label: str,
        args: list[str],
        cwd: Path,
        returncode: int,
        stdout: str,
        stderr: str,
        started_at: str,
        finished_at: str,
    ) -> str:
        record_id = f"ev-{len(self.data['records']) + 1:04d}"
        raw_dir = self.root / round_id / record_id
        raw_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = raw_dir / "stdout.txt"
        stderr_path = raw_dir / "stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8")
        stderr_path.write_text(stderr, encoding="utf-8")
        record = {
            "id": record_id,
            "round_id": round_id,
            "kind": "command",
            "label": label,
            "args": args,
            "cwd": str(cwd),
            "returncode": returncode,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "stdout_sha256": sha256_file(stdout_path),
            "stderr_sha256": sha256_file(stderr_path),
            "started_at": started_at,
            "finished_at": finished_at,
        }
        self.data["records"].append(record)
        self.save()
        return record_id
