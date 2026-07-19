from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schemas import digest_json, utc_now
from .text import tokens


OWNER_OUTCOMES = {"helped", "failed", "avoid", "neutral"}


@dataclass(slots=True)
class OwnerOutcomeLearner:
    """Summarize owner feedback into bounded future action guidance."""

    failure_suppression_threshold: int = 2
    success_reuse_threshold: int = 2

    def feedback_record(
        self,
        *,
        target: dict[str, Any],
        outcome: str,
        owner_note: str = "",
        evidence: dict[str, Any] | None = None,
        goal_progress_delta: float = 0.0,
    ) -> dict[str, Any]:
        normalized = str(outcome).lower().strip()
        if normalized not in OWNER_OUTCOMES:
            raise ValueError(f"unsupported owner outcome: {outcome}")
        evidence = evidence or {}
        signature = self.action_signature(target)
        return {
            "feedback_id": f"feedback_{digest_json({'signature': signature, 'at': utc_now()})[:24]}",
            "created_at": utc_now(),
            "outcome": normalized,
            "owner_note": str(owner_note)[:1000],
            "evidence": self._bounded_value(evidence),
            "goal_progress_delta": round(float(goal_progress_delta), 4),
            "action_signature": signature,
            "action": {
                "action_id": target.get("action_id"),
                "plan_id": target.get("plan_id"),
                "goal_id": target.get("goal_id"),
                "tool": target.get("tool") or target.get("recommended_tool"),
                "purpose": str(target.get("purpose") or target.get("title") or "")[:400],
                "risk": target.get("risk"),
                "risk_class": target.get("risk_class"),
                "status": target.get("status"),
            },
            "authority": {
                "feedback_only": True,
                "executes_action": False,
                "updates_goal_progress_only_with_evidence": True,
            },
        }

    def summary(
        self,
        feedback: list[dict[str, Any]],
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        grouped: dict[str, dict[str, Any]] = {}
        for item in feedback:
            if not isinstance(item, dict):
                continue
            signature = str(item.get("action_signature", ""))
            if not signature:
                continue
            bucket = grouped.setdefault(
                signature,
                {
                    "action_signature": signature,
                    "helped": 0,
                    "failed": 0,
                    "avoid": 0,
                    "neutral": 0,
                    "examples": [],
                    "latest_at": "",
                },
            )
            outcome = str(item.get("outcome", "neutral")).lower()
            if outcome in OWNER_OUTCOMES:
                bucket[outcome] += 1
            bucket["latest_at"] = max(str(bucket["latest_at"]), str(item.get("created_at", "")))
            if len(bucket["examples"]) < 3:
                bucket["examples"].append(
                    {
                        "feedback_id": item.get("feedback_id"),
                        "outcome": outcome,
                        "owner_note": str(item.get("owner_note", ""))[:240],
                        "action": item.get("action", {}),
                    }
                )
        signatures = list(grouped.values())
        signatures.sort(
            key=lambda item: (
                int(item["avoid"]),
                int(item["failed"]),
                int(item["helped"]),
                str(item["latest_at"]),
            ),
            reverse=True,
        )
        suppressed = [
            {
                **item,
                "suppression_reason": "owner_avoid"
                if int(item["avoid"]) > 0
                else "repeated_owner_failed",
            }
            for item in signatures
            if int(item["avoid"]) > 0
            or int(item["failed"]) >= self.failure_suppression_threshold
        ]
        reusable = [
            {
                **item,
                "reuse_reason": "repeated_owner_helped",
            }
            for item in signatures
            if int(item["helped"]) >= self.success_reuse_threshold
            and int(item["avoid"]) == 0
        ]
        return {
            "schema_version": 1,
            "generated_at": utc_now(),
            "feedback_count": len(feedback),
            "signature_count": len(signatures),
            "suppressed_signatures": suppressed[:limit],
            "reusable_patterns": reusable[:limit],
            "recent_feedback": feedback[:limit],
            "authority": {
                "learning_guidance_only": True,
                "executes_action": False,
                "promotes_skills": False,
            },
        }

    @staticmethod
    def action_signature(action: dict[str, Any]) -> str:
        tool = str(action.get("tool") or action.get("recommended_tool") or "none").lower()
        goal_id = str(action.get("goal_id") or "")
        risk_class = str(action.get("risk_class") or action.get("risk") or "").lower()
        purpose = str(action.get("purpose") or action.get("title") or "")
        purpose_tokens = sorted(tokens(purpose))[:12]
        return digest_json(
            {
                "tool": tool,
                "goal_id": goal_id,
                "risk_class": risk_class,
                "purpose_tokens": purpose_tokens,
            }
        )

    @staticmethod
    def _bounded_value(value: Any) -> Any:
        rendered = str(value)
        if len(rendered) <= 1000:
            return value
        return {
            "truncated": True,
            "preview": rendered[:1000],
            "original_type": type(value).__name__,
        }
