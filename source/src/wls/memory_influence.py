from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
import json

from .text import tokens


MEMORY_STANCES = {"support", "warn", "contradict"}


@dataclass(slots=True)
class MemoryInfluenceAnalyzer:
    """Explain how retrieved memories influence a bounded plan candidate."""

    stale_after_days: float = 30.0
    low_confidence_threshold: float = 0.45

    def analyze_plan(
        self,
        *,
        plan: Any,
        memories: list[dict[str, Any]],
        goal_pressure: dict[str, Any] | None = None,
        now: datetime | None = None,
        limit: int = 8,
    ) -> dict[str, Any]:
        now = now or datetime.now(UTC)
        plan_text = self._plan_text(plan, goal_pressure=goal_pressure)
        plan_tokens = tokens(plan_text)
        influences = [
            self._memory_influence(memory, plan_tokens=plan_tokens, now=now)
            for memory in memories
            if isinstance(memory, dict)
        ]
        influences = [item for item in influences if item["score"] > 0.0]
        influences.sort(
            key=lambda item: (
                item["effective_score"],
                item["score"],
                item["confidence"],
            ),
            reverse=True,
        )
        bounded = influences[: max(1, min(20, int(limit)))]
        return {
            "schema_version": 1,
            "generated_at": now.isoformat(),
            "plan_digest": self._digest_text(plan_text),
            "influences": bounded,
            "stance_counts": {
                stance: sum(1 for item in bounded if item["stance"] == stance)
                for stance in sorted(MEMORY_STANCES)
            },
            "changed_decision_hint": any(
                item["stance"] in {"warn", "contradict"} for item in bounded
            ),
            "authority": {
                "candidate_only": True,
                "executes_action": False,
                "policy_and_approval_still_required": True,
            },
        }

    def _memory_influence(
        self,
        memory: dict[str, Any],
        *,
        plan_tokens: set[str],
        now: datetime,
    ) -> dict[str, Any]:
        content = memory.get("content", {})
        rendered = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
        memory_tokens = tokens(rendered)
        overlap = len(plan_tokens & memory_tokens) / max(1, len(plan_tokens | memory_tokens))
        confidence = self._float(memory.get("confidence"), 0.5)
        importance = self._float(memory.get("importance"), 0.5)
        causal_score = self._float(memory.get("causal_score", memory.get("score")), 0.0)
        created_at = self._parse_time(memory.get("created_at"))
        age_days = (
            max(0.0, (now - created_at).total_seconds() / 86400.0)
            if created_at is not None
            else None
        )
        stale_penalty = (
            min(0.5, ((age_days - self.stale_after_days) / self.stale_after_days) * 0.25)
            if age_days is not None and age_days > self.stale_after_days
            else 0.0
        )
        low_confidence_penalty = (
            0.25 if confidence < self.low_confidence_threshold else 0.0
        )
        stance, stance_reason = self._stance(content, rendered)
        base = min(
            1.0,
            0.45 * overlap
            + 0.20 * confidence
            + 0.20 * importance
            + 0.15 * causal_score,
        )
        effective = max(0.0, base - stale_penalty - low_confidence_penalty)
        penalties = []
        if stale_penalty:
            penalties.append("stale")
        if low_confidence_penalty:
            penalties.append("low_confidence")
        reasons = [
            f"token_overlap={overlap:.3f}",
            f"confidence={confidence:.2f}",
            f"importance={importance:.2f}",
            stance_reason,
        ]
        return {
            "memory_id": str(memory.get("memory_id", "")),
            "memory_type": str(memory.get("memory_type", "")),
            "stance": stance,
            "score": round(base, 4),
            "effective_score": round(effective, 4),
            "confidence": confidence,
            "importance": importance,
            "age_days": round(age_days, 3) if age_days is not None else None,
            "penalties": penalties,
            "reasons": reasons,
            "content_summary": rendered[:300],
        }

    @staticmethod
    def _stance(content: Any, rendered: str) -> tuple[str, str]:
        text = rendered.lower()
        guidance = content.get("decision_guidance") if isinstance(content, dict) else None
        rule = content.get("decision_rule") if isinstance(content, dict) else None
        merged = guidance if isinstance(guidance, dict) else rule
        if isinstance(merged, dict):
            effect = str(merged.get("effect", "")).lower()
            if effect in {"avoid_tool", "block", "defer"}:
                return "warn", f"structured_effect={effect}"
            if effect in {"replace_acceptance", "prefer_tool", "support"}:
                return "support", f"structured_effect={effect}"
            if effect in {"contradict", "refute"}:
                return "contradict", f"structured_effect={effect}"
        if any(term in text for term in ("avoid", "blocked", "failed", "failure", "risk", "do not")):
            return "warn", "warning_terms"
        if any(term in text for term in ("contradict", "refute", "false", "wrong")):
            return "contradict", "contradiction_terms"
        return "support", "default_support"

    @staticmethod
    def _plan_text(plan: Any, *, goal_pressure: dict[str, Any] | None) -> str:
        if hasattr(plan, "to_dict"):
            payload = plan.to_dict()
        elif isinstance(plan, dict):
            payload = plan
        else:
            payload = {"plan": str(plan)}
        return json.dumps(
            {
                "plan": payload,
                "goal_pressure": goal_pressure or {},
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )

    @staticmethod
    def _digest_text(text: str) -> str:
        from hashlib import sha256

        return sha256(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _parse_time(value: Any) -> datetime | None:
        if value is None:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
