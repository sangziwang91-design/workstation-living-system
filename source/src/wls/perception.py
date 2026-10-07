from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from collections.abc import Sequence
from typing import Any
import json

from .schemas import Goal
from .text import tokens


PERCEPTION_CLASSES = {
    "noise",
    "context",
    "opportunity",
    "risk",
    "owner_relevant",
}


@dataclass(slots=True)
class PerceptionClassifier:
    """Classify observations into life-loop perception categories."""

    min_meaningful_score: float = 0.35

    def classify(
        self,
        observation: dict[str, Any],
        *,
        goals: Sequence[Goal | dict[str, Any]],
        memories: Sequence[dict[str, Any]],
    ) -> dict[str, Any]:
        text = self._observation_text(observation)
        lowered = text.lower()
        metadata = observation.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        warning = bool(metadata.get("warning"))
        verification = str(observation.get("verification", "UNKNOWN"))
        kind = str(observation.get("kind", ""))
        predicate = str(observation.get("predicate", ""))
        confidence = self._float(observation.get("confidence"), 0.5)
        salience = self._float(observation.get("salience"), 0.0)
        goal_links = self._goal_links(text, goals)
        memory_links = self._memory_links(text, memories)
        risk_terms = (
            "error",
            "failed",
            "failure",
            "unhealthy",
            "deleted",
            "stopped",
            "blocked",
            "warning",
            "exception",
            "refuted",
            "low disk",
        )
        opportunity_terms = (
            "created",
            "new",
            "ready",
            "succeeded",
            "available",
            "proposal",
            "candidate",
            "request",
            "todo",
            "next",
        )
        owner_terms = ("owner", "user", "inbox", "approval", "request")
        risk_signal = (
            warning
            or verification == "REFUTED"
            or kind in {"sensor_error", "service_health", "sensor_warning"}
            or any(term in lowered for term in risk_terms)
        )
        owner_signal = any(term in lowered for term in owner_terms)
        opportunity_signal = any(term in lowered for term in opportunity_terms)
        relevance = max(
            [0.0, *[link["score"] for link in goal_links], *[link["score"] for link in memory_links]]
        )
        score = round(
            min(
                1.0,
                0.28 * salience
                + 0.22 * confidence
                + 0.25 * relevance
                + (0.18 if risk_signal else 0.0)
                + (0.16 if owner_signal else 0.0)
                + (0.12 if opportunity_signal else 0.0),
            ),
            4,
        )
        if risk_signal:
            classification = "risk"
        elif owner_signal:
            classification = "owner_relevant"
        elif opportunity_signal and (goal_links or memory_links or salience >= 0.45):
            classification = "opportunity"
        elif goal_links or memory_links or kind in {"state", "resource", "time"}:
            classification = "context"
        else:
            classification = "noise"
        if score < 0.2 and classification == "context":
            classification = "noise"
        reasons = [
            f"salience={salience:.2f}",
            f"confidence={confidence:.2f}",
        ]
        if risk_signal:
            reasons.append("risk_signal")
        if owner_signal:
            reasons.append("owner_signal")
        if opportunity_signal:
            reasons.append("opportunity_signal")
        if goal_links:
            reasons.append("goal_link")
        if memory_links:
            reasons.append("memory_link")
        return {
            "classification": classification,
            "meaningful": classification != "noise" and score >= self.min_meaningful_score,
            "score": score,
            "goal_links": goal_links[:3],
            "memory_links": memory_links[:3],
            "reasons": reasons,
        }

    def daily_summary(
        self,
        observations: list[dict[str, Any]],
        *,
        goals: Sequence[Goal | dict[str, Any]],
        memories: Sequence[dict[str, Any]],
        limit: int = 10,
        day: str | None = None,
    ) -> dict[str, Any]:
        classified = [
            {**observation, "perception": self.classify(observation, goals=goals, memories=memories)}
            for observation in observations
        ]
        class_counts: dict[str, int] = {name: 0 for name in sorted(PERCEPTION_CLASSES)}
        for item in classified:
            class_counts[str(item["perception"]["classification"])] = (
                class_counts.get(str(item["perception"]["classification"]), 0) + 1
            )
        top_changes = [
            self._summary_item(item)
            for item in sorted(
                classified,
                key=lambda item: (
                    bool(item["perception"]["meaningful"]),
                    float(item["perception"]["score"]),
                    str(item.get("observed_at", "")),
                ),
                reverse=True,
            )
            if item["perception"]["meaningful"]
        ][:limit]
        return {
            "schema_version": 1,
            "day": day or datetime.now(UTC).date().isoformat(),
            "observation_count": len(observations),
            "class_counts": class_counts,
            "top_daily_changes": top_changes,
            "noise_suppressed_count": sum(
                1
                for item in classified
                if item["perception"]["classification"] == "noise"
            ),
            "generated_at": datetime.now(UTC).isoformat(),
        }

    @classmethod
    def _goal_links(
        cls, text: str, goals: Sequence[Goal | dict[str, Any]]
    ) -> list[dict[str, Any]]:
        text_tokens = tokens(text)
        links: list[dict[str, Any]] = []
        for goal in goals:
            if isinstance(goal, dict):
                goal_id = str(goal.get("goal_id", ""))
                title = str(goal.get("title", ""))
                description = str(goal.get("description", ""))
            else:
                goal_id = goal.goal_id
                title = goal.title
                description = goal.description
            goal_tokens = tokens(f"{title} {description}")
            overlap = len(text_tokens & goal_tokens) / max(1, len(text_tokens | goal_tokens))
            if overlap > 0:
                links.append(
                    {
                        "goal_id": goal_id,
                        "title": title[:160],
                        "score": round(overlap, 4),
                    }
                )
        links.sort(key=lambda item: item["score"], reverse=True)
        return links

    @classmethod
    def _memory_links(
        cls, text: str, memories: Sequence[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        text_tokens = tokens(text)
        links: list[dict[str, Any]] = []
        for memory in memories:
            content = memory.get("content", {})
            rendered = json.dumps(content, ensure_ascii=False, sort_keys=True, default=str)
            memory_tokens = tokens(rendered)
            overlap = len(text_tokens & memory_tokens) / max(1, len(text_tokens | memory_tokens))
            if overlap > 0:
                links.append(
                    {
                        "memory_id": str(memory.get("memory_id", "")),
                        "memory_type": str(memory.get("memory_type", "")),
                        "score": round(
                            overlap
                            * max(0.1, cls._float(memory.get("importance"), 0.5))
                            * max(0.1, cls._float(memory.get("confidence"), 0.5)),
                            4,
                        ),
                    }
                )
        links.sort(key=lambda item: item["score"], reverse=True)
        return links

    @staticmethod
    def _observation_text(observation: dict[str, Any]) -> str:
        return json.dumps(observation, ensure_ascii=False, sort_keys=True, default=str)

    @staticmethod
    def _summary_item(observation: dict[str, Any]) -> dict[str, Any]:
        perception = observation["perception"]
        return {
            "observation_id": observation.get("observation_id"),
            "event_id": observation.get("event_id"),
            "source": observation.get("source"),
            "kind": observation.get("kind"),
            "subject": observation.get("subject"),
            "predicate": observation.get("predicate"),
            "observed_at": observation.get("observed_at"),
            "classification": perception["classification"],
            "score": perception["score"],
            "goal_links": perception["goal_links"],
            "memory_links": perception["memory_links"],
            "reasons": perception["reasons"],
        }

    @staticmethod
    def _float(value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
