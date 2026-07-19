from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .outcome_learning import OwnerOutcomeLearner
from .schemas import ActionSpec, Plan, RiskLevel, utc_now


ACTION_RISK_CLASSES = {"read", "draft", "write", "cleanup", "rollback", "external"}


@dataclass(slots=True)
class BoundedActionCandidateBuilder:
    """Produce one owner-readable action candidate without authorizing it."""

    max_preview_chars: int = 500

    def build(
        self,
        *,
        plan: Plan | None = None,
        goal_pressure: dict[str, Any] | None = None,
        daily_perception: dict[str, Any] | None = None,
        memory_influence: dict[str, Any] | None = None,
        outcome_learning: dict[str, Any] | None = None,
        tool_side_effects: dict[str, str] | None = None,
        policy_decisions: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        tool_side_effects = tool_side_effects or {}
        policy_decisions = policy_decisions or {}
        action = self._first_action(plan)
        if action is not None:
            return self._from_action(
                action,
                plan=plan,
                goal_pressure=goal_pressure,
                daily_perception=daily_perception,
                memory_influence=memory_influence,
                outcome_learning=outcome_learning,
                side_effect_class=tool_side_effects.get(action.tool, "unknown"),
                policy_decision=policy_decisions.get(action.action_id),
            )
        return self._from_pressure(
            goal_pressure=goal_pressure,
            daily_perception=daily_perception,
            memory_influence=memory_influence,
        )

    @staticmethod
    def classify_action(
        action: ActionSpec | dict[str, Any],
        *,
        side_effect_class: str = "unknown",
    ) -> str:
        data = action.to_dict() if hasattr(action, "to_dict") else dict(action)
        tool = str(data.get("tool", "")).lower()
        purpose = str(data.get("purpose", "")).lower()
        expected = str(data.get("expected_result", "")).lower()
        risk = str(data.get("risk", RiskLevel.READ.value))
        text = f"{tool} {purpose} {expected}"
        side_effect = str(side_effect_class or "unknown").lower()

        if "rollback" in text or "restore" in text:
            return "rollback"
        if "cleanup" in text or "quarantine" in text or tool == "delete_file":
            return "cleanup"
        if side_effect in {"external", "irreversible"} or tool in {
            "run_command",
            "publish_external",
            "send_message",
        }:
            return "external"
        if (
            tool == "emit_note" or "draft" in text or "propose" in text
        ) and tool not in {"write_file", "apply_patch", "delete_file"}:
            return "draft"
        if (
            side_effect == "reversible"
            or risk in {RiskLevel.REVERSIBLE_WRITE.value, RiskLevel.HIGH.value}
            or tool in {"write_file", "apply_patch"}
        ):
            return "write"
        return "read"

    def _from_action(
        self,
        action: ActionSpec,
        *,
        plan: Plan | None,
        goal_pressure: dict[str, Any] | None,
        daily_perception: dict[str, Any] | None,
        memory_influence: dict[str, Any] | None,
        outcome_learning: dict[str, Any] | None,
        side_effect_class: str,
        policy_decision: dict[str, Any] | None,
    ) -> dict[str, Any]:
        risk_class = self.classify_action(
            action, side_effect_class=side_effect_class
        )
        decision = policy_decision or {}
        requires_approval = bool(
            decision.get("requires_approval", risk_class not in {"read"})
        )
        allowed = bool(decision.get("allowed", risk_class == "read"))
        executes_now = bool(allowed and not requires_approval and risk_class == "read")
        candidate = {
            "schema_version": 1,
            "available": True,
            "generated_at": utc_now(),
            "candidate_type": "planned_action",
            "source": "planner",
            "action_id": action.action_id,
            "plan_id": plan.plan_id if plan is not None else None,
            "goal_id": action.goal_id,
            "title": action.purpose[:160],
            "tool": action.tool,
            "recommended_tool": action.tool,
            "risk": action.risk.value,
            "risk_class": risk_class,
            "action_signature": OwnerOutcomeLearner.action_signature(
                {
                    "tool": action.tool,
                    "goal_id": action.goal_id,
                    "risk": action.risk.value,
                    "risk_class": risk_class,
                    "purpose": action.purpose,
                }
            ),
            "side_effect_class": side_effect_class,
            "status": action.status.value,
            "requires_owner_approval": requires_approval,
            "approval_required": requires_approval,
            "approval_route": (
                "owner_policy_approval" if requires_approval else "policy_permits_read"
            ),
            "policy_decision": {
                "allowed": allowed,
                "requires_approval": requires_approval,
                "reason": str(decision.get("reason", "")),
            },
            "executes_now": executes_now,
            "arguments_preview": self._bounded(action.arguments),
            "expected_result": action.expected_result[:300],
            "rationale": self._rationale(
                plan=plan,
                goal_pressure=goal_pressure,
                memory_influence=memory_influence,
            ),
            "evidence_links": self._evidence_links(
                goal_pressure=goal_pressure,
                daily_perception=daily_perception,
                memory_influence=memory_influence,
            ),
            "authority": {
                "candidate_only": True,
                "persists_action": False,
                "executes_action": False,
                "policy_and_approval_still_required": True,
            },
        }
        suppression = self._suppression(candidate, outcome_learning)
        if suppression is None:
            return candidate
        return {
            **candidate,
            "available": False,
            "candidate_type": "suppressed_planned_action",
            "status": "SUPPRESSED_BY_OUTCOME_LEARNING",
            "suppressed": True,
            "suppression": suppression,
            "requires_owner_approval": False,
            "approval_required": False,
            "executes_now": False,
            "reason": suppression["suppression_reason"],
        }

    def _from_pressure(
        self,
        *,
        goal_pressure: dict[str, Any] | None,
        daily_perception: dict[str, Any] | None,
        memory_influence: dict[str, Any] | None,
    ) -> dict[str, Any]:
        step = (
            goal_pressure.get("next_small_step", {})
            if isinstance(goal_pressure, dict)
            else {}
        )
        if not isinstance(step, dict) or not step.get("available"):
            return {
                "schema_version": 1,
                "available": False,
                "generated_at": utc_now(),
                "reason": str(step.get("reason", "no useful action candidate exists")),
            }
        return {
            "schema_version": 1,
            "available": True,
            "generated_at": utc_now(),
            "candidate_type": "goal_pressure_step",
            "source": "goal_pressure",
            "goal_id": step.get("goal_id"),
            "title": str(step.get("title", "Choose one bounded next step"))[:160],
            "tool": None,
            "recommended_tool": "owner_review",
            "risk": RiskLevel.READ.value,
            "risk_class": "read",
            "status": "CANDIDATE_ONLY",
            "requires_owner_approval": False,
            "approval_required": False,
            "approval_route": "none_read_only_candidate",
            "executes_now": False,
            "arguments_preview": {},
            "expected_result": "Owner can review the suggested next small step.",
            "rationale": str(step.get("rationale", ""))[:500],
            "evidence_links": self._evidence_links(
                goal_pressure=goal_pressure,
                daily_perception=daily_perception,
                memory_influence=memory_influence,
            ),
            "authority": {
                "candidate_only": True,
                "persists_action": False,
                "executes_action": False,
                "policy_and_approval_still_required": True,
            },
        }

    @staticmethod
    def _first_action(plan: Plan | None) -> ActionSpec | None:
        if plan is None:
            return None
        return plan.actions[0] if plan.actions else None

    @staticmethod
    def _suppression(
        candidate: dict[str, Any],
        outcome_learning: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if not isinstance(outcome_learning, dict):
            return None
        signature = str(candidate.get("action_signature", ""))
        if not signature:
            return None
        for item in outcome_learning.get("suppressed_signatures", []) or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("action_signature", "")) == signature:
                return {
                    "action_signature": signature,
                    "suppression_reason": str(
                        item.get("suppression_reason", "owner_outcome_feedback")
                    ),
                    "helped": int(item.get("helped", 0) or 0),
                    "failed": int(item.get("failed", 0) or 0),
                    "avoid": int(item.get("avoid", 0) or 0),
                    "examples": item.get("examples", []),
                }
        return None

    def _bounded(self, value: Any) -> Any:
        text = str(value)
        if len(text) <= self.max_preview_chars:
            return value
        return {
            "truncated": True,
            "preview": text[: self.max_preview_chars],
            "original_type": type(value).__name__,
        }

    @staticmethod
    def _rationale(
        *,
        plan: Plan | None,
        goal_pressure: dict[str, Any] | None,
        memory_influence: dict[str, Any] | None,
    ) -> str:
        parts: list[str] = []
        if plan is not None and plan.rationale:
            parts.append(plan.rationale[:300])
        top_goal = (
            goal_pressure.get("top_goal")
            if isinstance(goal_pressure, dict)
            else None
        )
        if isinstance(top_goal, dict):
            parts.append(
                "Top goal pressure: "
                + ", ".join(str(item) for item in top_goal.get("pressure_reasons", [])[:4])
            )
        if isinstance(memory_influence, dict) and memory_influence.get("influences"):
            parts.append("Memory influence present in this judgment.")
        return " ".join(part for part in parts if part).strip()

    @staticmethod
    def _evidence_links(
        *,
        goal_pressure: dict[str, Any] | None,
        daily_perception: dict[str, Any] | None,
        memory_influence: dict[str, Any] | None,
    ) -> dict[str, list[str]]:
        goal_ids: list[str] = []
        observation_ids: list[str] = []
        memory_ids: list[str] = []
        if isinstance(goal_pressure, dict):
            top_goal = goal_pressure.get("top_goal")
            if isinstance(top_goal, dict) and top_goal.get("goal_id"):
                goal_ids.append(str(top_goal["goal_id"]))
            for item in goal_pressure.get("ranked_goals", []) or []:
                if isinstance(item, dict) and item.get("goal_id"):
                    goal_ids.append(str(item["goal_id"]))
        if isinstance(daily_perception, dict):
            for item in daily_perception.get("top_daily_changes", []) or []:
                if isinstance(item, dict) and item.get("observation_id"):
                    observation_ids.append(str(item["observation_id"]))
        if isinstance(memory_influence, dict):
            for item in memory_influence.get("influences", []) or []:
                if isinstance(item, dict) and item.get("memory_id"):
                    memory_ids.append(str(item["memory_id"]))
        return {
            "goal_ids": list(dict.fromkeys(goal_ids))[:5],
            "observation_ids": list(dict.fromkeys(observation_ids))[:5],
            "memory_ids": list(dict.fromkeys(memory_ids))[:5],
        }
