from __future__ import annotations

from typing import Any
import json

from .schemas import ActionResult, ActionSpec


class Evaluator:
    """Programmatic acceptance checks; failures remain visible."""

    def evaluate(self, action: ActionSpec, result: ActionResult) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        text = json.dumps(result.output, ensure_ascii=False, sort_keys=True).lower()
        for criterion in action.acceptance:
            passed = self._check(criterion, result.output, text)
            checks.append({"criterion": criterion, "passed": passed})
        accepted = result.success and all(check["passed"] for check in checks)
        return {
            "accepted": accepted,
            "tool_success": result.success,
            "checks": checks,
            "error": result.error,
        }

    @staticmethod
    def _check(criterion: str, output: dict[str, Any], text: str) -> bool:
        lower = criterion.lower().strip()
        if lower.startswith("output contains "):
            expression = lower.removeprefix("output contains ").strip(" '`\"")
            alternatives = [item.strip() for item in expression.split(" or ")]
            for key in alternatives:
                if key.endswith(" marker"):
                    key = key.removesuffix(" marker")
                if key in output or key in text:
                    return True
            return False
        if lower == "output ok is true":
            return output.get("ok") is True
        if lower.startswith("returncode is "):
            try:
                expected = int(lower.rsplit(" ", 1)[1])
                return output.get("returncode") == expected
            except ValueError:
                return False
        # Unknown criteria are never silently passed.
        return False
