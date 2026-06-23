from __future__ import annotations

from copy import deepcopy
from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import ActionSpec, MemoryItem, Plan, RiskLevel, digest_json, new_id, utc_now
from .stores import MemoryStore


SUPPORTED_RULE_EFFECTS = {"replace_acceptance", "avoid_tool"}
SUPPORTED_RULE_TOOLS = {
    "noop",
    "read_file",
    "list_directory",
    "write_file",
    "emit_note",
    "http_get",
    "run_command",
}


def ensure_decision_memory_tables(db: Database) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS decision_attributions (
            attribution_id TEXT PRIMARY KEY,
            cycle_id TEXT,
            provider_type TEXT NOT NULL,
            context_sha256 TEXT NOT NULL,
            retrieved_memory_ids_json TEXT NOT NULL,
            applied_rules_json TEXT NOT NULL,
            actual_plan_json TEXT NOT NULL,
            counterfactual_plan_json TEXT NOT NULL,
            changed INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_decision_attributions_cycle_time
        ON decision_attributions(cycle_id, created_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS memory_rule_links (
            candidate_id TEXT PRIMARY KEY,
            experiment_id TEXT NOT NULL,
            memory_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(candidate_id) REFERENCES evolution_candidates(candidate_id),
            FOREIGN KEY(memory_id) REFERENCES memories(memory_id)
        )
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class MemoryAttributionPlanner:
    """Apply evidence-backed memory rules and retain a no-memory comparison."""

    def __init__(
        self,
        planner,
        db: Database,
        ledger: EvidenceLedger,
        provider_type: str = "deterministic",
    ) -> None:
        self.planner = planner
        self.db = db
        self.ledger = ledger
        self.provider_type = provider_type

    def plan(self, context: dict[str, Any]) -> Plan:
        counterfactual_context = self._without_memories(context)
        if self.provider_type == "deterministic":
            counterfactual = self.planner.plan(counterfactual_context)
        else:
            counterfactual = self.planner.plan(context)
        actual = self.planner.plan(context)
        retrieved_ids = [
            str(item.get("memory_id"))
            for item in context.get("memories", [])
            if item.get("memory_id")
        ]
        rules = self._eligible_rules(context.get("memories", []))
        applied = self._apply_rules(actual, rules)
        actual.memory_ids = list(
            dict.fromkeys([*actual.memory_ids, *[item["memory_id"] for item in applied]])
        )
        actual_json = self._plan_for_comparison(actual)
        counterfactual_json = self._plan_for_comparison(counterfactual)
        changed = actual_json != counterfactual_json
        attribution_id = new_id("attribution")
        record = {
            "attribution_id": attribution_id,
            "cycle_id": context.get("cycle_id"),
            "provider_type": self.provider_type,
            "context_sha256": digest_json(self._context_for_digest(context)),
            "retrieved_memory_ids": retrieved_ids,
            "applied_rules": applied,
            "actual_plan": actual_json,
            "counterfactual_plan": counterfactual_json,
            "changed": changed,
            "created_at": utc_now(),
        }
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO decision_attributions(
                    attribution_id,cycle_id,provider_type,context_sha256,
                    retrieved_memory_ids_json,applied_rules_json,actual_plan_json,
                    counterfactual_plan_json,changed,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    attribution_id,
                    record["cycle_id"],
                    self.provider_type,
                    record["context_sha256"],
                    json.dumps(retrieved_ids, ensure_ascii=False),
                    json.dumps(applied, ensure_ascii=False, sort_keys=True),
                    json.dumps(actual_json, ensure_ascii=False, sort_keys=True),
                    json.dumps(counterfactual_json, ensure_ascii=False, sort_keys=True),
                    int(changed),
                    record["created_at"],
                ),
            )
            self.ledger.append(
                "decision_memory_attribution",
                {
                    "attribution_id": attribution_id,
                    "cycle_id": record["cycle_id"],
                    "retrieved_memory_ids": retrieved_ids,
                    "applied_memory_ids": [item["memory_id"] for item in applied],
                    "changed": changed,
                },
                connection,
            )
        return actual

    def summary(self, limit: int = 500) -> dict[str, Any]:
        rows = self.db.query_all(
            "SELECT changed,applied_rules_json FROM decision_attributions ORDER BY created_at DESC LIMIT ?",
            (max(1, min(5000, int(limit))),),
        )
        with_rules = sum(bool(json.loads(row["applied_rules_json"])) for row in rows)
        changed = sum(bool(row["changed"]) for row in rows)
        return {
            "decisions": len(rows),
            "with_applied_rules": with_rules,
            "changed_vs_no_memory": changed,
            "change_rate": changed / len(rows) if rows else 0.0,
        }

    def _eligible_rules(self, memories: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rules: list[dict[str, Any]] = []
        for memory in memories:
            if str(memory.get("memory_type")) != "procedural":
                continue
            if float(memory.get("confidence", 0.0)) < 0.9:
                continue
            content = memory.get("content", {})
            if not isinstance(content, dict):
                continue
            rule = content.get("decision_rule")
            if not isinstance(rule, dict):
                continue
            candidate_id = str(rule.get("source_candidate_id", ""))
            candidate = self.db.query_one(
                "SELECT status FROM evolution_candidates WHERE candidate_id=?",
                (candidate_id,),
            )
            if candidate is None or candidate["status"] != "PROMOTED":
                continue
            effect = str(rule.get("effect", ""))
            tool = str(rule.get("tool", ""))
            if effect not in SUPPORTED_RULE_EFFECTS or tool not in SUPPORTED_RULE_TOOLS:
                continue
            normalized = dict(rule)
            normalized["memory_id"] = str(memory["memory_id"])
            rules.append(normalized)
        return rules

    @staticmethod
    def _apply_rules(plan: Plan, rules: list[dict[str, Any]]) -> list[dict[str, Any]]:
        applied: list[dict[str, Any]] = []
        output: list[ActionSpec] = []
        for action in plan.actions:
            current = action
            for rule in rules:
                if current.tool != rule["tool"]:
                    continue
                effect = rule["effect"]
                if effect == "replace_acceptance":
                    acceptance = [str(item) for item in rule.get("acceptance", [])]
                    if not acceptance:
                        continue
                    current.acceptance = acceptance
                    applied.append(
                        {
                            "memory_id": rule["memory_id"],
                            "effect": effect,
                            "tool": current.tool,
                            "acceptance": acceptance,
                        }
                    )
                elif effect == "avoid_tool":
                    current = ActionSpec(
                        tool="noop",
                        arguments={
                            "reason": f"Evidence-backed memory blocked tool {action.tool}"
                        },
                        purpose=f"Avoid previously harmful tool path: {action.purpose}",
                        expected_result="No external side effect",
                        risk=RiskLevel.READ,
                        goal_id=action.goal_id,
                        skill_id=None,
                        acceptance=["output ok is true"],
                    )
                    applied.append(
                        {
                            "memory_id": rule["memory_id"],
                            "effect": effect,
                            "tool": action.tool,
                        }
                    )
            output.append(current)
        plan.actions = output
        return applied

    @staticmethod
    def _without_memories(context: dict[str, Any]) -> dict[str, Any]:
        value = deepcopy(context)
        value["memories"] = []
        value["workspace"] = [
            item
            for item in value.get("workspace", [])
            if item.get("item_type") != "memory"
        ]
        return value

    @staticmethod
    def _plan_for_comparison(plan: Plan) -> dict[str, Any]:
        return {
            "actions": [
                {
                    "tool": action.tool,
                    "arguments": action.arguments,
                    "purpose": action.purpose,
                    "expected_result": action.expected_result,
                    "risk": action.risk.value,
                    "goal_id": action.goal_id,
                    "skill_id": action.skill_id,
                    "acceptance": action.acceptance,
                }
                for action in plan.actions
            ],
            "world_fact_ids": plan.world_fact_ids,
            "unknowns": plan.unknowns,
        }

    @staticmethod
    def _context_for_digest(context: dict[str, Any]) -> dict[str, Any]:
        return {
            "cycle_id": context.get("cycle_id"),
            "workspace": context.get("workspace", []),
            "goals": context.get("goals", []),
            "memory_ids": [
                item.get("memory_id") for item in context.get("memories", [])
            ],
            "world_fact_ids": [
                item.get("fact_id") for item in context.get("world_facts", [])
            ],
            "budget": context.get("budget", {}),
        }


class RecoveryMemorySynthesizer:
    """Convert human-promoted recovery evidence into bounded procedural memory."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        memories: MemoryStore,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.memories = memories

    def sync_promoted(self) -> list[str]:
        rows = self.db.query_all(
            """
            SELECT c.candidate_id,c.source_ids_json,r.experiment_id,r.result_json
            FROM evolution_candidates c
            JOIN recovery_experiments r ON r.candidate_id=c.candidate_id
            LEFT JOIN memory_rule_links l ON l.candidate_id=c.candidate_id
            WHERE c.status='PROMOTED' AND r.status='PASSED'
              AND r.finished_at=(
                  SELECT MAX(r2.finished_at) FROM recovery_experiments r2
                  WHERE r2.candidate_id=c.candidate_id AND r2.status='PASSED'
              )
              AND l.candidate_id IS NULL
            """
        )
        created: list[str] = []
        for row in rows:
            result = json.loads(row["result_json"])
            rule = self._rule_from_result(str(row["candidate_id"]), result)
            if rule is None:
                continue
            source_ids = [
                str(row["candidate_id"]),
                str(row["experiment_id"]),
                *[str(item) for item in json.loads(row["source_ids_json"])],
            ]
            memory = MemoryItem(
                memory_type="procedural",
                content={
                    "decision_rule": rule,
                    "validation": {
                        "experiment_id": row["experiment_id"],
                        "baseline_pass_rate": result.get("baseline_pass_rate"),
                        "candidate_pass_rate": result.get("candidate_pass_rate"),
                        "regressions": result.get("regressions"),
                    },
                },
                importance=0.9,
                confidence=0.95,
                source_ids=source_ids,
                tags=["promoted_recovery", "decision_rule"],
            )
            memory_id = self.memories.add(memory)
            with self.db.transaction() as connection:
                connection.execute(
                    "INSERT INTO memory_rule_links(candidate_id,experiment_id,memory_id,created_at) VALUES (?,?,?,?)",
                    (
                        row["candidate_id"],
                        row["experiment_id"],
                        memory_id,
                        utc_now(),
                    ),
                )
                self.ledger.append(
                    "promoted_recovery_memory_created",
                    {
                        "candidate_id": row["candidate_id"],
                        "experiment_id": row["experiment_id"],
                        "memory_id": memory_id,
                        "rule_sha256": digest_json(rule),
                    },
                    connection,
                )
            created.append(memory_id)
        return created

    @staticmethod
    def _rule_from_result(
        candidate_id: str, result: dict[str, Any]
    ) -> dict[str, Any] | None:
        if not result.get("passed") or int(result.get("regressions", 1)) != 0:
            return None
        cases = result.get("case_results", [])
        if not cases:
            return None
        summaries = [item.get("recovery", {}) for item in cases]
        if any(item.get("type") != "contract_recovery" for item in summaries):
            return None
        tools = {
            str(item.get("candidate", {}).get("tool", "")) for item in cases
        }
        tools.discard("")
        after_values = {
            json.dumps(item.get("after", []), ensure_ascii=False, sort_keys=True)
            for item in summaries
        }
        if len(tools) != 1 or len(after_values) != 1:
            return None
        acceptance = json.loads(next(iter(after_values)))
        if not acceptance:
            return None
        return {
            "effect": "replace_acceptance",
            "tool": next(iter(tools)),
            "acceptance": acceptance,
            "source_candidate_id": candidate_id,
        }
