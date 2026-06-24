from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def replace_once(relative: str, old: str, new: str) -> None:
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{relative}: expected one replacement, found {count}")
    path.write_text(text.replace(old, new), encoding="utf-8")


def main() -> None:
    replace_once(
        "source/src/wls/memory_index.py",
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,NULL,?,?)",
        ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,NULL,?,?)",
    )
    replace_once(
        "source/src/wls/memory_index.py",
        '''            rollback_reason = self._linked_rollback_reason(row)
            if rollback_reason and state not in TERMINAL_MEMORY_STATES:
                self.set_state(memory_id, "SUPERSEDED", rollback_reason, [rollback_reason])
                state = "SUPERSEDED"
            if not enabled:
                suppressed.append(self._suppressed(row, "memory_disabled_baseline", state))
                continue
''',
        '''            if not enabled:
                suppressed.append(self._suppressed(row, "memory_disabled_baseline", state))
                continue
            rollback_reason = None if frozen else self._linked_rollback_reason(row)
            if rollback_reason and state not in TERMINAL_MEMORY_STATES:
                self.set_state(memory_id, "SUPERSEDED", rollback_reason, [rollback_reason])
                state = "SUPERSEDED"
''',
    )
    replace_once(
        "source/src/wls/memory_index.py",
        '''            structured_score, reasons = self._structured_score(row, normalized_context)
            lexical_score = self._overlap(query_tokens, tokens(str(row["normalized_text"])))
''',
        '''            structured_score, reasons = self._structured_score(row, normalized_context)
            substantive_reasons = [
                reason for reason in reasons if reason != "time_window"
            ]
            lexical_score = self._overlap(query_tokens, tokens(str(row["normalized_text"])))
''',
    )
    replace_once(
        "source/src/wls/memory_index.py",
        '''            if score <= 0.10 or (has_structured_query and not reasons and lexical_score <= 0.15):
''',
        '''            if score <= 0.10 or (
                has_structured_query
                and not substantive_reasons
                and lexical_score <= 0.15
            ):
''',
    )

    replace_once(
        "source/src/wls/stores.py",
        "from typing import Any\n",
        "from typing import Any, Iterable\n",
    )
    replace_once(
        "source/src/wls/stores.py",
        "from .evidence import EvidenceLedger\nfrom .text import tokens\n",
        "from .evidence import EvidenceLedger\nfrom .memory_index import CausalMemoryIndex\nfrom .text import tokens\n",
    )
    replace_once(
        "source/src/wls/stores.py",
        '''    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def add(self, memory: MemoryItem) -> str:
''',
        '''    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger
        self.causal = CausalMemoryIndex(db, ledger)

    def add(self, memory: MemoryItem) -> str:
''',
    )
    replace_once(
        "source/src/wls/stores.py",
        '''                connection,
            )
        return memory.memory_id

    def retrieve(
''',
        '''                connection,
            )
        self.causal.index_memory(memory.memory_id)
        return memory.memory_id

    def retrieve(
''',
    )
    replace_once(
        "source/src/wls/stores.py",
        '''            for score, row in selected
        ]

    def recent(
''',
        '''            for score, row in selected
        ]

    def retrieve_causal(
        self,
        query: str,
        limit: int = 8,
        *,
        context: dict[str, Any] | None = None,
        enabled: bool = True,
        frozen: bool = False,
    ) -> dict[str, Any]:
        return self.causal.retrieve(
            query,
            limit,
            context=context,
            enabled=enabled,
            frozen=frozen,
        )

    def build_query_context(
        self, events: Iterable[Any], goals: Iterable[Any]
    ) -> dict[str, Any]:
        return self.causal.build_query_context(events, goals)

    def record_decision_outcome(
        self,
        memory_ids: Iterable[str],
        *,
        success: bool | None,
        prediction_statuses: Iterable[str],
        source_ids: Iterable[str],
    ) -> list[dict[str, Any]]:
        return self.causal.record_outcome(
            memory_ids,
            success=success,
            prediction_statuses=prediction_statuses,
            source_ids=source_ids,
        )

    def memory_state(self, memory_id: str) -> dict[str, Any]:
        return self.causal.state(memory_id)

    def memory_summary(self) -> dict[str, Any]:
        return self.causal.summary()

    def memory_integrity(self) -> tuple[bool, dict[str, int]]:
        return self.causal.integrity()

    def recent(
''',
    )
    replace_once(
        "source/src/wls/stores.py",
        '''    def deactivate(self, memory_id: str) -> None:
        self.db.execute("UPDATE memories SET active=0 WHERE memory_id=?", (memory_id,))

    @staticmethod
''',
        '''    def deactivate(self, memory_id: str) -> None:
        self.db.execute("UPDATE memories SET active=0 WHERE memory_id=?", (memory_id,))
        state = self.causal.state(memory_id)["validity_state"]
        if state not in {"REFUTED", "EXPIRED", "SUPERSEDED"}:
            self.causal.set_state(
                memory_id,
                "SUPERSEDED",
                "memory deactivated",
                ["MemoryStore.deactivate"],
            )

    @staticmethod
''',
    )

    replace_once(
        "source/src/wls/runtime.py",
        "from .learning import LearningSystem\nfrom .lease import ProcessLease\n",
        "from .learning import LearningSystem\nfrom .memory_attribution import MemoryAttributionStore\nfrom .lease import ProcessLease\n",
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''        self.cognition = CognitiveEngine(
            self.db, self.ledger, self.temporal_world, config
        )
        self.planner = Planner(config, self.cognition, self.ledger)
''',
        '''        self.cognition = CognitiveEngine(
            self.db, self.ledger, self.temporal_world, config
        )
        self.memory_attribution = MemoryAttributionStore(
            self.db, self.ledger, self.memories.causal
        )
        self.planner = Planner(config, self.cognition, self.ledger)
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''            query_text = self._query_text(reserved, active_goals)
            retrieved_memories = (
                self.memories.retrieve(query_text, self.config.memory_retrieval_limit)
                if query_text
                else []
            )
            resource_snapshot = self._resource_snapshot()
''',
        '''            query_text = self._query_text(reserved, active_goals)
            memory_mode = str(
                self.config.provider.get("memory_mode", "enabled")
            ).lower()
            memory_query_context = self.memories.build_query_context(
                reserved, active_goals
            )
            memory_retrieval = self.memories.retrieve_causal(
                query_text,
                self.config.memory_retrieval_limit,
                context=memory_query_context,
                enabled=memory_mode != "disabled",
                frozen=memory_mode == "frozen",
            )
            retrieved_memories = memory_retrieval["selected"]
            resource_snapshot = self._resource_snapshot()
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''                "world_facts": world_facts,
                "memories": retrieved_memories,
                "matching_skills": self.skills.match(query_text),
''',
        '''                "world_facts": world_facts,
                "memories": retrieved_memories,
                "memory_retrieval": memory_retrieval,
                "matching_skills": self.skills.match(query_text),
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''            if meaningful_input:
                plan = self.planner.plan(context)
                plan.actions = plan.actions[: int(budget["max_actions"])]
''',
        '''            if meaningful_input:
                plan = self.planner.plan(context)
                self.memory_attribution.record(
                    cycle_id, plan.memory_ids, memory_retrieval
                )
                plan.actions = plan.actions[: int(budget["max_actions"])]
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''                outcomes = [*recovery_outcomes, *self._execute_plan(plan)]
                cognition_result = self.cognition.resolve_cycle(cycle_id, plan, outcomes)
                plan_status = self._plan_status(plan.plan_id)
''',
        '''                outcomes = [*recovery_outcomes, *self._execute_plan(plan)]
                cognition_result = self.cognition.resolve_cycle(cycle_id, plan, outcomes)
                memory_resolution = self.memory_attribution.resolve(
                    cycle_id,
                    outcomes,
                    cognition_result,
                    frozen=memory_mode == "frozen",
                )
                if cognition_result is not None and memory_resolution is not None:
                    cognition_result["memory_attribution"] = memory_resolution
                plan_status = self._plan_status(plan.plan_id)
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''                "selected_events": len(selected_events),
                "workspace_items": len(workspace),
                "actions": len(plan.actions),
''',
        '''                "selected_events": len(selected_events),
                "workspace_items": len(workspace),
                "memory_retrieval": {
                    "mode": memory_retrieval["mode"],
                    "selected_memory_ids": [
                        item["memory_id"] for item in memory_retrieval["selected"]
                    ],
                    "suppressed_memory_ids": [
                        item["memory_id"] for item in memory_retrieval["suppressed"]
                    ],
                },
                "actions": len(plan.actions),
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''            "cognition": self.cognition.summary(limit=500),
            "temporal_world": self.temporal_world.summary(),
            "next_focus": self.db.get_runtime("next_focus", []),
''',
        '''            "cognition": self.cognition.summary(limit=500),
            "temporal_world": self.temporal_world.summary(),
            "causal_memory": self.memories.memory_summary(),
            "memory_attribution": self.memory_attribution.summary(),
            "next_focus": self.db.get_runtime("next_focus", []),
''',
    )
    replace_once(
        "source/src/wls/runtime.py",
        '''        cognition_ok, cognition_details = self.cognition.integrity()
        result = {
            "ok": ledger_ok and db_ok and cognition_ok,
            "ledger": ledger_details,
            "database": db_details,
            "cognition": cognition_details,
        }
''',
        '''        cognition_ok, cognition_details = self.cognition.integrity()
        memory_ok, memory_details = self.memories.memory_integrity()
        attribution_ok, attribution_details = self.memory_attribution.integrity()
        result = {
            "ok": (
                ledger_ok
                and db_ok
                and cognition_ok
                and memory_ok
                and attribution_ok
            ),
            "ledger": ledger_details,
            "database": db_details,
            "cognition": cognition_details,
            "causal_memory": memory_details,
            "memory_attribution": attribution_details,
        }
''',
    )

    cognition_old = '''        if include_memories:
            for memory in context.get("memories", []):
                if str(memory.get("memory_type")) != "procedural":
                    continue
'''
    cognition_new = '''        if include_memories:
            for memory in context.get("memories", []):
                memory_id = str(memory.get("memory_id", ""))
                content = memory.get("content", {})
                guidance = (
                    content.get("decision_guidance")
                    if isinstance(content, dict)
                    else None
                )
                causal_score = float(
                    memory.get("causal_score", memory.get("score", 0.0))
                )
                validity_state = str(
                    memory.get("validity_state", "ACTIVE")
                )
                if (
                    memory_id
                    and isinstance(guidance, dict)
                    and float(memory.get("confidence", 0.0)) >= 0.85
                    and causal_score >= 0.20
                    and validity_state in {"ACTIVE", "WEAKENED"}
                ):
                    effect = str(guidance.get("effect", ""))
                    tool = str(guidance.get("tool", ""))
                    originals = list(candidates)
                    if effect == "avoid_tool" and tool:
                        for original in originals:
                            if not any(
                                str(action.get("tool")) == tool
                                for action in original.actions
                            ):
                                continue
                            candidates.append(
                                HypothesisCandidate(
                                    key=(
                                        f"causal_memory_avoid_tool:{memory_id}:"
                                        f"{original.key}"
                                    ),
                                    subject=original.subject,
                                    claim=(
                                        f"Applicable causal memory requires avoiding "
                                        f"{tool} for this context."
                                    ),
                                    rationale=str(
                                        guidance.get(
                                            "reason",
                                            "Structured causal evidence matched.",
                                        )
                                    ),
                                    base_score=max(
                                        0.97, original.base_score + 0.04
                                    ),
                                    actions=[
                                        self._noop(
                                            f"Causal memory suppressed {tool}",
                                            original.goal_id,
                                        )
                                    ],
                                    support_ids=list(
                                        dict.fromkeys(
                                            [*original.support_ids, memory_id]
                                        )
                                    ),
                                    memory_ids=[memory_id],
                                    fact_ids=list(original.fact_ids),
                                    goal_id=original.goal_id,
                                    cause_predicate="causal_memory_guidance",
                                )
                            )
                    elif effect == "replace_acceptance" and tool:
                        acceptance = [
                            str(value)
                            for value in guidance.get("acceptance", [])
                        ]
                        if acceptance:
                            for original in originals:
                                if not any(
                                    str(action.get("tool")) == tool
                                    for action in original.actions
                                ):
                                    continue
                                actions = [
                                    dict(action) for action in original.actions
                                ]
                                for action in actions:
                                    if str(action.get("tool")) == tool:
                                        action["acceptance"] = acceptance
                                candidates.append(
                                    HypothesisCandidate(
                                        key=(
                                            f"causal_memory_contract:{memory_id}:"
                                            f"{original.key}"
                                        ),
                                        subject=original.subject,
                                        claim=(
                                            f"Applicable causal memory supplies "
                                            f"the acceptance contract for {tool}."
                                        ),
                                        rationale=str(
                                            guidance.get(
                                                "reason",
                                                "Structured causal evidence matched.",
                                            )
                                        ),
                                        base_score=max(
                                            0.97, original.base_score + 0.04
                                        ),
                                        actions=actions,
                                        support_ids=list(
                                            dict.fromkeys(
                                                [*original.support_ids, memory_id]
                                            )
                                        ),
                                        memory_ids=[memory_id],
                                        fact_ids=list(original.fact_ids),
                                        goal_id=original.goal_id,
                                        cause_predicate="causal_memory_guidance",
                                    )
                                )
                if str(memory.get("memory_type")) != "procedural":
                    continue
'''
    replace_once("source/src/wls/cognition.py", cognition_old, cognition_new)
    replace_once(
        "source/src/wls/cognition.py",
        '''                memory_id = str(memory.get("memory_id", ""))
                effect = str(rule.get("effect", ""))
''',
        '''                effect = str(rule.get("effect", ""))
''',
    )

    replace_once(
        "source/pyproject.toml",
        'version = "0.8.0.dev1"',
        'version = "0.9.0.dev1"',
    )
    replace_once(
        "source/src/wls/_version.py",
        '__version__ = "0.8.0.dev1"',
        '__version__ = "0.9.0.dev1"',
    )


if __name__ == "__main__":
    main()
