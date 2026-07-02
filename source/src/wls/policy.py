from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import RuntimeConfig
from .schemas import ActionSpec, RiskLevel


@dataclass(slots=True)
class PolicyDecision:
    allowed: bool
    requires_approval: bool
    reason: str


class PolicyEngine:
    """Deterministic policy layer; model output cannot lower risk."""

    HIGH_RISK_TOOLS = {
        "run_command",
        "write_file",
        "delete_file",
        "restart_service",
        "apply_patch",
    }
    IRREVERSIBLE_TOOLS = {"delete_file", "publish_external", "send_message"}

    def __init__(self, config: RuntimeConfig):
        self.config = config

    def classify(self, action: ActionSpec) -> RiskLevel:
        declared = action.risk
        if action.tool in self.IRREVERSIBLE_TOOLS:
            required = RiskLevel.IRREVERSIBLE
        elif action.tool in self.HIGH_RISK_TOOLS:
            required = (
                RiskLevel.REVERSIBLE_WRITE
                if action.tool in {"write_file", "apply_patch"}
                else RiskLevel.HIGH
            )
        else:
            required = RiskLevel.READ
        rank = {
            RiskLevel.READ: 0,
            RiskLevel.REVERSIBLE_WRITE: 1,
            RiskLevel.HIGH: 2,
            RiskLevel.IRREVERSIBLE: 3,
        }
        return required if rank[required] > rank[declared] else declared

    def decide(
        self, action: ActionSpec, approval_valid: bool = False
    ) -> PolicyDecision:
        risk = self.classify(action)
        if self.config.read_only and risk != RiskLevel.READ:
            if approval_valid:
                return PolicyDecision(
                    True,
                    False,
                    "explicit approval overrides read-only for this exact action",
                )
            return PolicyDecision(False, True, "runtime is in read-only mode")
        if risk == RiskLevel.READ:
            if self.config.allow_autonomous_read_actions:
                return PolicyDecision(True, False, "read action allowed")
            return PolicyDecision(False, True, "autonomous reads disabled")
        if risk == RiskLevel.REVERSIBLE_WRITE:
            if approval_valid:
                return PolicyDecision(True, False, "exact reversible write approved")
            if self.config.allow_autonomous_reversible_writes:
                return PolicyDecision(
                    True, False, "configured reversible write allowance"
                )
            return PolicyDecision(False, True, "reversible write requires approval")
        if approval_valid:
            return PolicyDecision(True, False, "exact high-risk action approved")
        return PolicyDecision(False, True, f"{risk.value} action requires approval")

    def validate_arguments(self, action: ActionSpec) -> None:
        policy = self.config.tool_policy
        if action.tool in {
            "read_file",
            "list_directory",
            "write_file",
            "delete_file",
            "emit_note",
            "inspect_asset",
        }:
            raw_path = action.arguments.get("path")
            if not isinstance(raw_path, str) or not raw_path.strip():
                raise ValueError("path is required")
            roots_key = (
                "allowed_write_roots"
                if action.tool in {"write_file", "delete_file", "emit_note"}
                else "allowed_read_roots"
            )
            roots = [
                Path(item).expanduser().resolve() for item in policy.get(roots_key, [])
            ]
            if not roots:
                raise PermissionError(f"no roots configured for {action.tool}")
            candidate = Path(raw_path).expanduser().resolve(strict=False)
            if not any(self._contained(candidate, root) for root in roots):
                raise PermissionError(f"path outside {roots_key}: {candidate}")
        if action.tool == "http_get":
            allowed_hosts = {
                str(item).lower() for item in policy.get("allowed_hosts", [])
            }
            host = str(action.arguments.get("host", "")).lower()
            if not host or host not in allowed_hosts:
                raise PermissionError("HTTP host not allowlisted")
        if action.tool == "run_command":
            command = action.arguments.get("command")
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(item, str) for item in command)
            ):
                raise ValueError("command must be a non-empty string list")
            allowed = {str(item).lower() for item in policy.get("allowed_commands", [])}
            executable = Path(command[0]).name.lower()
            if executable not in allowed and command[0].lower() not in allowed:
                raise PermissionError(f"command not allowlisted: {command[0]}")

    @staticmethod
    def _contained(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False
