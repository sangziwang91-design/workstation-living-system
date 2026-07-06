from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import hashlib
import json
import shutil
import sys

from .experiments import IsolatedToolHarness
from .schemas import RiskLevel, digest_json, new_id, utc_now


@dataclass(frozen=True, slots=True)
class SandboxContract:
    adapter_id: str
    sandbox_root: str
    allowed_tools: list[str]
    allowed_mounts: list[str] = field(default_factory=list)
    network_enabled: bool = False
    secret_injection: str = "none"
    cpu_limit: str = "local-fixture"
    memory_limit: str = "local-fixture"
    time_limit_seconds: int = 30

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["environment_digest"] = digest_json(
            {
                "adapter": "local_fixture",
                "python": sys.version.split()[0],
                "platform": sys.platform,
                "allowed_tools": sorted(self.allowed_tools),
                "network_enabled": self.network_enabled,
                "secret_injection": self.secret_injection,
            }
        )
        payload["contract_digest"] = digest_json(payload)
        return payload


class SandboxAdapter:
    """Evidence-producing local sandbox adapter, not a canonical executor."""

    def __init__(self, contract: SandboxContract) -> None:
        self.contract = contract
        self.root = Path(contract.sandbox_root).expanduser().resolve()
        self.harness = IsolatedToolHarness(self.root, list(contract.allowed_tools))

    def run_probe(
        self,
        *,
        tool: str,
        arguments: dict[str, Any],
        purpose: str,
        risk: RiskLevel = RiskLevel.READ,
    ) -> dict[str, Any]:
        contract = self.contract.to_dict()
        result = self.harness.execute(
            tool,
            arguments,
            purpose,
            risk=risk,
        )
        destroy = self.destroy()
        receipt = {
            "sandbox_run_id": new_id("sandbox_run"),
            "adapter_id": self.contract.adapter_id,
            "contract": contract,
            "tool": tool,
            "purpose": purpose,
            "result": result,
            "destroy": destroy,
            "network_enabled": self.contract.network_enabled,
            "secret_injection": self.contract.secret_injection,
            "mount_count": len(self.contract.allowed_mounts),
            "passed": bool(result.get("passed")) and destroy["destroy_verified"],
            "direct_execution": False,
            "remote_execution": False,
            "claim_ceiling": (
                "local sandbox adapter fixture receipt only; no remote worker, "
                "provider call, secret access, network access, canonical task "
                "completion, or delegated authority is inferred"
            ),
        }
        receipt["receipt_digest"] = digest_json(receipt)
        return receipt

    def destroy(self) -> dict[str, Any]:
        before_digest = self._tree_digest(self.root)
        shutil.rmtree(self.root, ignore_errors=True)
        exists_after = self.root.exists()
        return {
            "destroyed_at": utc_now(),
            "root": str(self.root),
            "tree_digest_before_destroy": before_digest,
            "exists_after_destroy": exists_after,
            "destroy_verified": not exists_after,
        }

    @staticmethod
    def _tree_digest(root: Path) -> str:
        if not root.exists():
            return digest_json({"missing": str(root)})
        hasher = hashlib.sha256()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix()
            hasher.update(relative.encode("utf-8"))
            hasher.update(b"\0")
            hasher.update(path.read_bytes())
            hasher.update(b"\0")
        return hasher.hexdigest()


def write_sandbox_receipt(path: Path, receipt: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
