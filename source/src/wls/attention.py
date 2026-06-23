from __future__ import annotations

from typing import Any
import json
import math

from .schemas import AffectState, DriveState, Event, Goal, WorkspaceItem
from .text import tokens


class AttentionSystem:
    def __init__(self, capacity: int = 8):
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity

    def select(
        self,
        events: list[Event],
        goals: list[Goal],
        memories: list[dict[str, Any]],
        drives: DriveState,
        affect: AffectState,
    ) -> list[WorkspaceItem]:
        candidates: list[WorkspaceItem] = []
        goal_terms = self._tokens(
            " ".join(f"{goal.title} {goal.description}" for goal in goals)
        )
        for event in events:
            text = json.dumps(event.payload, ensure_ascii=False, sort_keys=True)
            tokens = self._tokens(text)
            relevance = (
                len(tokens & goal_terms) / max(1, len(tokens | goal_terms))
                if goal_terms
                else 0.0
            )
            threat = (
                1.0
                if any(
                    term in text.lower()
                    for term in ("error", "failed", "unhealthy", "deleted", "stopped")
                )
                else 0.0
            )
            novelty = 1.0 / max(1, event.attempts)
            salience = (
                0.38 * event.salience_hint
                + 0.24 * relevance
                + 0.18 * threat * max(0.4, affect.safety_tension)
                + 0.12 * novelty * max(0.3, drives.curiosity)
                + 0.08 * (1.0 - affect.fatigue)
            )
            candidates.append(
                WorkspaceItem(
                    item_type="event",
                    reference_id=event.event_id,
                    summary=f"{event.event_type} from {event.source}: {text[:500]}",
                    salience=max(0.0, min(1.0, salience)),
                    reasons=[
                        f"hint={event.salience_hint:.2f}",
                        f"goal_relevance={relevance:.2f}",
                        f"threat={threat:.2f}",
                    ],
                    payload=event.to_dict(),
                )
            )
        for goal in goals:
            urgency = goal.priority
            if goal.status.value == "BLOCKED":
                urgency = min(1.0, urgency + 0.2)
            candidates.append(
                WorkspaceItem(
                    item_type="goal",
                    reference_id=goal.goal_id,
                    summary=f"Goal: {goal.title}; progress={goal.progress:.2f}; status={goal.status.value}",
                    salience=max(
                        0.0, min(1.0, 0.65 * urgency + 0.2 * (1.0 - goal.progress))
                    ),
                    reasons=[
                        f"priority={goal.priority:.2f}",
                        f"progress={goal.progress:.2f}",
                    ],
                    payload=goal.to_dict(),
                )
            )
        for memory in memories:
            score = float(memory.get("score", 0.0))
            candidates.append(
                WorkspaceItem(
                    item_type="memory",
                    reference_id=str(memory["memory_id"]),
                    summary=f"{memory['memory_type']} memory: {json.dumps(memory['content'], ensure_ascii=False)[:500]}",
                    salience=max(
                        0.0,
                        min(
                            1.0,
                            0.6 * score
                            + 0.2 * float(memory["importance"])
                            + 0.2 * float(memory["confidence"]),
                        ),
                    ),
                    reasons=[f"retrieval_score={score:.2f}"],
                    payload=memory,
                )
            )
        candidates.extend(
            [
                WorkspaceItem(
                    item_type="drive",
                    reference_id="drives",
                    summary=f"Drive state: {drives.to_dict()}",
                    salience=max(0.2, 1.0 - min(drives.to_dict().values())),
                    reasons=["homeostatic_signal"],
                    payload=drives.to_dict(),
                ),
                WorkspaceItem(
                    item_type="affect",
                    reference_id="affect",
                    summary=f"Functional affect: {affect.to_dict()}",
                    salience=max(
                        0.2, affect.arousal, affect.frustration, affect.safety_tension
                    ),
                    reasons=["control_state"],
                    payload=affect.to_dict(),
                ),
            ]
        )
        candidates.sort(
            key=lambda item: (item.salience, item.reference_id), reverse=True
        )
        selected: list[WorkspaceItem] = []
        selected_types: dict[str, int] = {}
        for candidate in candidates:
            if len(selected) >= self.capacity:
                break
            # Keep workspace diverse; events may occupy at most 70% when other categories exist.
            if candidate.item_type == "event" and selected_types.get(
                "event", 0
            ) >= math.ceil(self.capacity * 0.7):
                continue
            selected.append(candidate)
            selected_types[candidate.item_type] = (
                selected_types.get(candidate.item_type, 0) + 1
            )

        # External change must not be starved by internally generated state or memory.
        if events and not any(item.item_type == "event" for item in selected):
            best_event = max(
                (item for item in candidates if item.item_type == "event"),
                key=lambda item: (item.salience, item.reference_id),
            )
            if len(selected) < self.capacity:
                selected.append(best_event)
            else:
                replace_index = min(
                    range(len(selected)),
                    key=lambda index: (
                        0
                        if selected[index].item_type in {"drive", "affect", "memory"}
                        else 1,
                        selected[index].salience,
                    ),
                )
                selected[replace_index] = best_event
            selected.sort(
                key=lambda item: (item.salience, item.reference_id), reverse=True
            )
        return selected

    @staticmethod
    def event_ids(workspace: list[WorkspaceItem]) -> set[str]:
        return {item.reference_id for item in workspace if item.item_type == "event"}

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return tokens(text)
