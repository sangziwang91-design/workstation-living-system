from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ComputerUseContract:
    target: str
    sandbox_verified: bool = False
    focus_hash: str | None = None
    rollback_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ComputerUseAdapter:
    def admit(self, contract: ComputerUseContract) -> dict[str, Any]:
        if not contract.sandbox_verified:
            raise PermissionError("computer use is blocked without verified disposable sandbox")
        if not contract.focus_hash:
            raise PermissionError("computer use requires focus verification")
        if not contract.rollback_steps:
            raise PermissionError("computer use requires rollback steps")
        return {"status": "DESIGN_ONLY", "contract": contract.to_dict()}
