from __future__ import annotations

from collections import defaultdict
from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .learning import LearningSystem
from .schemas import MemoryItem, utc_now
from .stores import MemoryStore
from .world import WorldModel
from .self_model import SelfModel
from .skills import SkillLibrary


class SleepConsolidator:
    """Offline consolidation. It performs no external high-risk action."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        memories: MemoryStore,
        world: WorldModel,
        self_model: SelfModel,
        skills: SkillLibrary,
        learning: LearningSystem,
    ):
        self.db = db
        self.ledger = ledger
        self.memories = memories
        self.world = world
        self.self_model = self_model
        self.skills = skills
        self.learning = learning

    def run(self) -> dict[str, Any]:
        expired = self.world.expire_stale()
        contradictions_resolved = self.world.resolve_contradictions()
        semantic_created = self._consolidate_semantics()
        duplicates_deactivated = self._deduplicate_memories()
        skill_candidates = self.skills.propose_from_action_sequences()
        failure_candidates = self.learning.create_failure_candidates()
        prediction_candidates = self.learning.create_prediction_error_candidates()
        focus = self._focus_items()
        evidence_id = self.ledger.append(
            "sleep_consolidation_completed",
            {
                "expired_facts": expired,
                "contradictions_resolved": contradictions_resolved,
                "semantic_created": semantic_created,
                "duplicates_deactivated": duplicates_deactivated,
                "skill_candidates": skill_candidates,
                "failure_candidates": failure_candidates,
                "prediction_candidates": prediction_candidates,
                "focus": focus,
            },
        )
        self.db.set_runtime("last_sleep_at", utc_now())
        return {
            "evidence_id": evidence_id,
            "expired_facts": expired,
            "contradictions_resolved": contradictions_resolved,
            "semantic_created": semantic_created,
            "duplicates_deactivated": duplicates_deactivated,
            "skill_candidates": skill_candidates,
            "failure_candidates": failure_candidates,
            "prediction_candidates": prediction_candidates,
            "focus": focus,
        }

    def _consolidate_semantics(self) -> list[str]:
        episodes = self.memories.recent("episodic", limit=200)
        facts: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for episode in episodes:
            content = episode["content"]
            for item in content.get("workspace", []):
                if item.get("item_type") == "event":
                    event = item.get("payload", {})
                    observation = event.get("payload", {}).get("observation", {})
                    if observation:
                        signature = json.dumps(
                            {
                                "kind": observation.get("kind"),
                                "subject": observation.get("subject"),
                                "predicate": observation.get("predicate"),
                                "value": observation.get("value"),
                            },
                            ensure_ascii=False,
                            sort_keys=True,
                        )[:1000]
                    else:
                        signature = item.get("summary", "")[:300]
                    facts[signature].append(episode)
        created: list[str] = []
        existing = self.memories.recent("semantic", limit=500)
        existing_signatures = {item["content"].get("pattern") for item in existing}
        for signature, matching in facts.items():
            if len(matching) < 3 or signature in existing_signatures:
                continue
            memory = MemoryItem(
                memory_type="semantic",
                content={
                    "pattern": signature,
                    "occurrences": len(matching),
                    "claim": "Repeated observed pattern; not a universal rule.",
                },
                importance=min(1.0, 0.5 + 0.05 * len(matching)),
                confidence=min(0.9, 0.45 + 0.08 * len(matching)),
                source_ids=[item["memory_id"] for item in matching],
                tags=["consolidated", "pattern"],
            )
            created.append(self.memories.add(memory))
        return created

    def _deduplicate_memories(self) -> int:
        rows = self.db.query_all(
            "SELECT memory_id,memory_type,normalized_text,importance,created_at FROM memories WHERE active=1 ORDER BY created_at DESC"
        )
        seen: dict[tuple[str, str], str] = {}
        deactivated = 0
        for row in rows:
            key = (str(row["memory_type"]), str(row["normalized_text"]))
            if key in seen:
                self.memories.deactivate(str(row["memory_id"]))
                deactivated += 1
            else:
                seen[key] = str(row["memory_id"])
        return deactivated

    def _focus_items(self) -> list[dict[str, Any]]:
        contradictions = self.world.unresolved_contradictions(limit=5)
        failed = self.db.query_all(
            "SELECT action_id,tool,purpose,error,finished_at FROM actions WHERE status IN ('FAILED','UNKNOWN_SIDE_EFFECT') ORDER BY finished_at DESC LIMIT 5"
        )
        focus = [
            {
                "type": "contradiction",
                "subject": item["subject"],
                "predicate": item["predicate"],
            }
            for item in contradictions
        ]
        focus.extend(
            {
                "type": "failure",
                "action_id": row["action_id"],
                "tool": row["tool"],
                "error": row["error"],
            }
            for row in failed
        )
        self.db.set_runtime("next_focus", focus)
        return focus
