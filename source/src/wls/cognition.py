from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json
import re

from .config import RuntimeConfig
from .db import Database
from .evidence import EvidenceLedger
from .schemas import Plan, RiskLevel, digest_json, new_id, utc_now
from .temporal_world import TemporalCausalWorld
from .text import tokens


TEXT_SUFFIXES = {
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".ts",
    ".ps1", ".bat", ".sh", ".ini", ".cfg", ".csv", ".log", ".tex",
}


@dataclass(slots=True)
class HypothesisCandidate:
    key: str
    subject: str
    claim: str
    rationale: str
    base_score: float
    actions: list[dict[str, Any]] = field(default_factory=list)
    support_ids: list[str] = field(default_factory=list)
    opposing_ids: list[str] = field(default_factory=list)
    memory_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    goal_id: str | None = None
    cause_predicate: str = "evidence_present"
    score: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ensure_cognition_tables(db: Database) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS cognitive_traces (
            trace_id TEXT PRIMARY KEY,
            cycle_id TEXT NOT NULL UNIQUE,
            provider TEXT NOT NULL,
            context_sha256 TEXT NOT NULL,
            selected_hypothesis_id TEXT,
            selected_key TEXT NOT NULL,
            counterfactual_key TEXT NOT NULL,
            memory_changed_decision INTEGER NOT NULL,
            memory_delta REAL NOT NULL,
            plan_id TEXT,
            status TEXT NOT NULL,
            decomposition_json TEXT NOT NULL,
            alternatives_json TEXT NOT NULL,
            outcome_json TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS cognitive_hypotheses (
            hypothesis_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            cycle_id TEXT NOT NULL,
            hypothesis_key TEXT NOT NULL,
            subject TEXT NOT NULL,
            claim TEXT NOT NULL,
            rationale TEXT NOT NULL,
            score REAL NOT NULL,
            selected INTEGER NOT NULL,
            support_ids_json TEXT NOT NULL,
            opposing_ids_json TEXT NOT NULL,
            memory_ids_json TEXT NOT NULL,
            fact_ids_json TEXT NOT NULL,
            actions_json TEXT NOT NULL,
            temporal_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(trace_id) REFERENCES cognitive_traces(trace_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_cognitive_hypotheses_cycle
        ON cognitive_hypotheses(cycle_id, score DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS cognitive_predictions (
            prediction_id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            hypothesis_id TEXT NOT NULL,
            action_index INTEGER NOT NULL,
            expected_json TEXT NOT NULL,
            confidence REAL NOT NULL,
            status TEXT NOT NULL,
            actual_json TEXT,
            created_at TEXT NOT NULL,
            resolved_at TEXT,
            FOREIGN KEY(trace_id) REFERENCES cognitive_traces(trace_id),
            FOREIGN KEY(hypothesis_id) REFERENCES cognitive_hypotheses(hypothesis_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_cognitive_predictions_trace
        ON cognitive_predictions(trace_id, status)
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class CognitiveEngine:
    """Evidence-bound local hypothesis competition and outcome calibration."""

    def __init__(
        self,
        db: Database,
        ledger: EvidenceLedger,
        temporal_world: TemporalCausalWorld,
        config: RuntimeConfig,
    ) -> None:
        self.db = db
        self.ledger = ledger
        self.temporal_world = temporal_world
        self.config = config
        ensure_cognition_tables(db)

    @property
    def minimum_confidence(self) -> float:
        value = float(self.config.provider.get("cognitive_min_confidence", 0.52))
        return max(0.0, min(1.0, value))

    @property
    def maximum_hypotheses(self) -> int:
        value = int(self.config.provider.get("cognitive_max_hypotheses", 6))
        return max(2, min(20, value))

    def create_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        cycle_id = str(context.get("cycle_id") or new_id("cycle"))
        actual = self._rank(context, include_memories=True)
        counterfactual = self._rank(context, include_memories=False)
        selected = actual[0]
        counter_selected = counterfactual[0]
        max_actions = max(0, int(context.get("budget", {}).get("max_actions", 0)))
        actions = selected.actions[:max_actions]
        if selected.score < self.minimum_confidence:
            actions = [self._noop("No hypothesis crossed the evidence threshold", selected.goal_id)][:max_actions]

        trace_id = new_id("cogtrace")
        now = utc_now()
        memory_free_score = self._score(selected, context, include_memories=False)
        memory_delta = selected.score - memory_free_score
        memory_changed = selected.key != counter_selected.key
        alternatives = [
            {
                "key": item.key,
                "claim": item.claim,
                "score": round(item.score, 6),
                "action_tools": [action["tool"] for action in item.actions],
                "support_ids": item.support_ids,
            }
            for item in actual[1 : self.maximum_hypotheses]
        ]
        decomposition = self._decomposition(selected, actions)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO cognitive_traces(
                    trace_id,cycle_id,provider,context_sha256,selected_hypothesis_id,
                    selected_key,counterfactual_key,memory_changed_decision,memory_delta,
                    plan_id,status,decomposition_json,alternatives_json,outcome_json,
                    created_at,resolved_at
                ) VALUES (?,?,?,?,NULL,?,?,?,?,NULL,'DELIBERATED',?,?,NULL,?,NULL)
                """,
                (
                    trace_id,
                    cycle_id,
                    "bounded_cognitive",
                    digest_json(self._digest_context(context)),
                    selected.key,
                    counter_selected.key,
                    int(memory_changed),
                    memory_delta,
                    json.dumps(decomposition, ensure_ascii=False, sort_keys=True),
                    json.dumps(alternatives, ensure_ascii=False, sort_keys=True),
                    now,
                ),
            )

        selected_id: str | None = None
        for index, candidate in enumerate(actual[: self.maximum_hypotheses]):
            temporal = self._temporalize(candidate, cycle_id) if index == 0 and candidate.support_ids else {}
            hypothesis_id = new_id("hyp")
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO cognitive_hypotheses(
                        hypothesis_id,trace_id,cycle_id,hypothesis_key,subject,claim,
                        rationale,score,selected,support_ids_json,opposing_ids_json,
                        memory_ids_json,fact_ids_json,actions_json,temporal_json,created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        hypothesis_id,
                        trace_id,
                        cycle_id,
                        candidate.key,
                        candidate.subject,
                        candidate.claim,
                        candidate.rationale,
                        candidate.score,
                        int(index == 0),
                        json.dumps(candidate.support_ids, ensure_ascii=False),
                        json.dumps(candidate.opposing_ids, ensure_ascii=False),
                        json.dumps(candidate.memory_ids, ensure_ascii=False),
                        json.dumps(candidate.fact_ids, ensure_ascii=False),
                        json.dumps(candidate.actions, ensure_ascii=False, sort_keys=True),
                        json.dumps(temporal, ensure_ascii=False, sort_keys=True),
                        now,
                    ),
                )
            if index == 0:
                selected_id = hypothesis_id
                self._create_predictions(trace_id, hypothesis_id, actions, candidate.score, now)

        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE cognitive_traces SET selected_hypothesis_id=? WHERE trace_id=?",
                (selected_id, trace_id),
            )
            self.ledger.append(
                "cognitive_deliberation_completed",
                {
                    "trace_id": trace_id,
                    "cycle_id": cycle_id,
                    "selected_key": selected.key,
                    "selected_score": selected.score,
                    "counterfactual_key": counter_selected.key,
                    "memory_changed_decision": memory_changed,
                    "memory_delta": memory_delta,
                    "alternatives": alternatives,
                },
                connection,
            )
        return {
            "rationale": (
                f"Bounded local cognition selected '{selected.claim}' at confidence "
                f"{selected.score:.3f}; the memory-free counterfactual selected "
                f"'{counter_selected.claim}'."
            ),
            "actions": actions,
            "memory_ids": list(dict.fromkeys(selected.memory_ids)),
            "world_fact_ids": list(dict.fromkeys(selected.fact_ids)),
            "unknowns": list(dict.fromkeys([
                *[str(item) for item in context.get("unknowns", [])],
                *([f"hypothesis_below_threshold:{selected.key}"] if selected.score < self.minimum_confidence else []),
            ])),
        }

    def attach_plan(self, cycle_id: str, plan: Plan) -> None:
        with self.db.transaction() as connection:
            updated = connection.execute(
                "UPDATE cognitive_traces SET plan_id=?,status='PLANNED' WHERE cycle_id=? AND plan_id IS NULL",
                (plan.plan_id, cycle_id),
            ).rowcount
            if updated:
                self.ledger.append(
                    "cognitive_plan_attached",
                    {"cycle_id": cycle_id, "plan_id": plan.plan_id},
                    connection,
                )

    def resolve_cycle(
        self,
        cycle_id: str,
        plan: Plan,
        outcomes: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        trace = self.db.query_one("SELECT * FROM cognitive_traces WHERE cycle_id=?", (cycle_id,))
        if trace is None:
            return None
        predictions = self.db.query_all(
            "SELECT * FROM cognitive_predictions WHERE trace_id=? AND status='OPEN' ORDER BY action_index",
            (trace["trace_id"],),
        )
        outcome_by_action = {
            str(item.get("action_id")): item
            for item in outcomes
            if item.get("action_id")
        }
        resolved: list[dict[str, Any]] = []
        successes: list[bool] = []
        actual: dict[str, Any]
        for row in predictions:
            index = int(row["action_index"])
            if index >= len(plan.actions):
                status = "UNKNOWN"
                actual = {"reason": "planned action index missing"}
            else:
                action = plan.actions[index]
                outcome = outcome_by_action.get(action.action_id)
                if outcome is None:
                    status = "UNKNOWN"
                    actual = {"reason": "no outcome", "action_id": action.action_id}
                else:
                    success = bool(outcome.get("success"))
                    successes.append(success)
                    status = "CONFIRMED" if success else "REFUTED"
                    actual = {
                        "action_id": action.action_id,
                        "tool": action.tool,
                        "success": success,
                        "status": outcome.get("status"),
                        "evaluation": outcome.get("evaluation", {}),
                    }
            with self.db.transaction() as connection:
                connection.execute(
                    "UPDATE cognitive_predictions SET status=?,actual_json=?,resolved_at=? WHERE prediction_id=?",
                    (status, json.dumps(actual, ensure_ascii=False, sort_keys=True), utc_now(), row["prediction_id"]),
                )
            resolved.append({"prediction_id": row["prediction_id"], "status": status, "actual": actual})

        calibration = self._calibrate(trace, successes, plan, outcomes)
        outcome_record = {
            "predictions": resolved,
            "calibration": calibration,
            "successful_actions": sum(successes),
            "observed_actions": len(successes),
        }
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE cognitive_traces SET status='RESOLVED',outcome_json=?,resolved_at=? WHERE trace_id=?",
                (json.dumps(outcome_record, ensure_ascii=False, sort_keys=True), utc_now(), trace["trace_id"]),
            )
            self.ledger.append(
                "cognitive_trace_resolved",
                {"trace_id": trace["trace_id"], "cycle_id": cycle_id, "plan_id": plan.plan_id, "outcome": outcome_record},
                connection,
            )
        return {
            "trace_id": trace["trace_id"],
            "selected_key": trace["selected_key"],
            "memory_changed_decision": bool(trace["memory_changed_decision"]),
            **outcome_record,
        }

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM cognitive_traces ORDER BY created_at DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        )
        return [
            {
                "trace_id": row["trace_id"],
                "cycle_id": row["cycle_id"],
                "selected_key": row["selected_key"],
                "counterfactual_key": row["counterfactual_key"],
                "memory_changed_decision": bool(row["memory_changed_decision"]),
                "memory_delta": float(row["memory_delta"]),
                "plan_id": row["plan_id"],
                "status": row["status"],
                "decomposition": json.loads(row["decomposition_json"]),
                "alternatives": json.loads(row["alternatives_json"]),
                "outcome": json.loads(row["outcome_json"]) if row["outcome_json"] else None,
                "created_at": row["created_at"],
                "resolved_at": row["resolved_at"],
            }
            for row in rows
        ]

    def summary(self, limit: int = 500) -> dict[str, Any]:
        rows = self.db.query_all(
            "SELECT status,memory_changed_decision,memory_delta,outcome_json FROM cognitive_traces ORDER BY created_at DESC LIMIT ?",
            (max(1, min(5000, int(limit))),),
        )
        prediction_counts = {"confirmed": 0, "refuted": 0, "unknown": 0}
        for row in rows:
            outcome = json.loads(row["outcome_json"] or "{}")
            for item in outcome.get("predictions", []):
                key = str(item.get("status", "UNKNOWN")).lower()
                if key in prediction_counts:
                    prediction_counts[key] += 1
        memory_changed = sum(bool(row["memory_changed_decision"]) for row in rows)
        return {
            "traces": len(rows),
            "resolved": sum(row["status"] == "RESOLVED" for row in rows),
            "memory_changed_decisions": memory_changed,
            "memory_change_rate": memory_changed / len(rows) if rows else 0.0,
            "mean_memory_delta": sum(float(row["memory_delta"]) for row in rows) / len(rows) if rows else 0.0,
            "predictions": prediction_counts,
        }

    def integrity(self) -> tuple[bool, dict[str, int]]:
        checks = {
            "orphan_hypotheses": """
                SELECT COUNT(*) AS n FROM cognitive_hypotheses h
                LEFT JOIN cognitive_traces t ON t.trace_id=h.trace_id
                WHERE t.trace_id IS NULL
            """,
            "orphan_predictions": """
                SELECT COUNT(*) AS n FROM cognitive_predictions p
                LEFT JOIN cognitive_traces t ON t.trace_id=p.trace_id
                LEFT JOIN cognitive_hypotheses h ON h.hypothesis_id=p.hypothesis_id
                WHERE t.trace_id IS NULL OR h.hypothesis_id IS NULL
            """,
            "resolved_without_outcome": """
                SELECT COUNT(*) AS n FROM cognitive_traces
                WHERE status='RESOLVED' AND outcome_json IS NULL
            """,
        }
        counts: dict[str, int] = {}
        for name, sql in checks.items():
            row = self.db.query_one(sql)
            counts[name] = int(row["n"]) if row else 0
        return all(value == 0 for value in counts.values()), counts

    def _rank(self, context: dict[str, Any], *, include_memories: bool) -> list[HypothesisCandidate]:
        candidates = self._candidates(context, include_memories=include_memories)
        for candidate in candidates:
            candidate.score = self._score(candidate, context, include_memories=include_memories)
            if not include_memories:
                candidate.memory_ids = []
        unique: dict[str, HypothesisCandidate] = {}
        for candidate in candidates:
            previous = unique.get(candidate.key)
            if previous is None or candidate.score > previous.score:
                unique[candidate.key] = candidate
        return sorted(unique.values(), key=lambda item: (item.score, item.key), reverse=True)[: self.maximum_hypotheses]

    def _candidates(
        self, context: dict[str, Any], *, include_memories: bool
    ) -> list[HypothesisCandidate]:
        candidates: list[HypothesisCandidate] = []
        for item in context.get("workspace", []):
            if item.get("item_type") != "event":
                continue
            event_id = str(item.get("reference_id", ""))
            event = item.get("payload", {})
            observation = event.get("payload", {}).get("observation", {})
            if not isinstance(observation, dict):
                continue
            kind = str(observation.get("kind", ""))
            subject = str(observation.get("subject", "") or "unknown")
            predicate = str(observation.get("predicate", "") or kind or "state")
            value = observation.get("value")
            confidence = float(observation.get("confidence", 0.5))
            support = [str(value) for value in (event_id, observation.get("observation_id")) if value]
            goal_id = self._best_goal_id(context, f"{subject} {predicate} {value}")
            if kind == "filesystem_change" and value in {"created", "modified"} and Path(subject).suffix.lower() in TEXT_SUFFIXES:
                candidates.append(HypothesisCandidate(
                    key=f"inspect_changed_text:{subject}", subject=subject,
                    claim="A changed text artifact should be inspected before any response.",
                    rationale="Direct filesystem evidence identifies a bounded readable artifact.",
                    base_score=0.70 + 0.20 * confidence,
                    actions=[self._action("read_file", {"path": subject, "max_bytes": 524288}, "Inspect the observed changed text artifact", "File content or bounded binary preview", ["output contains path", "output contains text or binary marker"], goal_id)],
                    support_ids=support, goal_id=goal_id, cause_predicate=predicate,
                ))
            elif kind == "external_event" and isinstance(value, dict) and value.get("action") == "inspect_path":
                target = str(value.get("path", ""))
                if target:
                    candidates.append(HypothesisCandidate(
                        key=f"inspect_requested_path:{target}", subject=target,
                        claim="The explicit human path request should be inspected read-only.",
                        rationale="A direct external request supplies the target and allowed read intent.",
                        base_score=0.94,
                        actions=[self._action("list_directory", {"path": target, "limit": 200}, "Inspect the explicitly requested path", "Bounded directory listing", ["output contains items"], goal_id)],
                        support_ids=support, goal_id=goal_id, cause_predicate=predicate,
                    ))
            elif kind in {"sensor_error", "service_health", "process_change"}:
                outbox = Path(str(context.get("paths", {}).get("outbox", ".")))
                note_path = outbox / f"cognition-{event_id or digest_json(observation)[:12]}.json"
                candidates.append(HypothesisCandidate(
                    key=f"preserve_attention:{kind}:{subject}", subject=subject,
                    claim=f"The observed {kind} requires evidence preservation before repair.",
                    rationale="The observation may affect continuity, but repair authority is not implied.",
                    base_score=0.62 + 0.20 * confidence,
                    actions=[self._action("emit_note", {"path": str(note_path), "title": f"WLS cognitive attention: {kind}", "body": json.dumps(observation, ensure_ascii=False, sort_keys=True)[:4000]}, "Preserve the observed anomaly for later diagnosis", "Internal evidence note written to the WLS outbox", ["output contains path"], goal_id)],
                    support_ids=support, goal_id=goal_id, cause_predicate=predicate,
                ))
            else:
                candidates.append(HypothesisCandidate(
                    key=f"defer_unsupported_event:{kind}:{subject}", subject=subject,
                    claim="The current event does not justify a concrete external action.",
                    rationale="Evidence is retained while unsupported action is withheld.",
                    base_score=0.42 + 0.15 * confidence,
                    actions=[self._noop("Unsupported event remains explicit", goal_id)],
                    support_ids=support, goal_id=goal_id, cause_predicate=predicate,
                ))

        if include_memories:
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
                if float(memory.get("confidence", 0.0)) < 0.9:
                    continue
                content = memory.get("content", {})
                rule = content.get("decision_rule") if isinstance(content, dict) else None
                if not isinstance(rule, dict):
                    continue
                candidate_id = str(rule.get("source_candidate_id", ""))
                candidate_row = self.db.query_one(
                    "SELECT status FROM evolution_candidates WHERE candidate_id=?",
                    (candidate_id,),
                )
                if candidate_row is None or candidate_row["status"] != "PROMOTED":
                    continue
                effect = str(rule.get("effect", ""))
                tool = str(rule.get("tool", ""))
                if effect == "avoid_tool" and tool:
                    candidates.append(
                        HypothesisCandidate(
                            key=f"memory_avoid_tool:{memory_id}:{tool}",
                            subject=tool,
                            claim=f"A promoted procedural memory requires avoiding {tool}.",
                            rationale=(
                                "A prior failure was experimentally recovered, human-promoted, "
                                "and persisted as a high-confidence decision rule."
                            ),
                            base_score=0.88,
                            actions=[
                                self._noop(
                                    f"Promoted procedural memory blocked {tool}", None
                                )
                            ],
                            support_ids=[value for value in (memory_id, candidate_id) if value],
                            memory_ids=[memory_id] if memory_id else [],
                            cause_predicate="promoted_memory_rule",
                        )
                    )
                elif effect == "replace_acceptance" and tool:
                    acceptance = [str(value) for value in rule.get("acceptance", [])]
                    if not acceptance:
                        continue
                    for original in list(candidates):
                        matching = [
                            action
                            for action in original.actions
                            if str(action.get("tool")) == tool
                        ]
                        if not matching:
                            continue
                        actions = [dict(action) for action in original.actions]
                        for action in actions:
                            if str(action.get("tool")) == tool:
                                action["acceptance"] = acceptance
                        candidates.append(
                            HypothesisCandidate(
                                key=f"memory_contract:{memory_id}:{original.key}",
                                subject=original.subject,
                                claim=(
                                    f"A promoted procedural memory supplies the validated "
                                    f"acceptance contract for {tool}."
                                ),
                                rationale=(
                                    "The candidate preserves the grounded action but replaces "
                                    "its failed contract with the human-promoted validated contract."
                                ),
                                base_score=max(0.82, original.base_score + 0.08),
                                actions=actions,
                                support_ids=list(
                                    dict.fromkeys(
                                        [*original.support_ids, memory_id, candidate_id]
                                    )
                                ),
                                memory_ids=[memory_id] if memory_id else [],
                                fact_ids=list(original.fact_ids),
                                goal_id=original.goal_id,
                                cause_predicate="promoted_memory_rule",
                            )
                        )

        for skill in context.get("matching_skills", []):
            if skill.get("status") not in {"APPROVED", "PROMOTED"} or not skill.get("steps"):
                continue
            skill_id = str(skill.get("skill_id"))
            goal_id = self._best_goal_id(context, f"{skill.get('name', '')} {skill.get('description', '')}")
            actions = []
            for step in skill.get("steps", []):
                actions.append({
                    "tool": str(step["tool"]),
                    "arguments": dict(step.get("arguments", {})),
                    "purpose": f"[skill:{skill_id}] {step.get('purpose', skill.get('description', 'Run validated skill'))}",
                    "expected_result": str(step.get("expected_result", "Step completes under its tool contract")),
                    "risk": str(skill.get("risk", "READ")),
                    "goal_id": goal_id,
                    "skill_id": skill_id,
                    "acceptance": [str(value) for value in step.get("acceptance", [])],
                })
            candidates.append(HypothesisCandidate(
                key=f"reuse_promoted_skill:{skill_id}", subject=str(skill.get("name", skill_id)),
                claim="A validated promoted skill matches the present evidence.",
                rationale="The skill lifecycle and match record provide a bounded reusable method.",
                base_score=0.68 + 0.20 * float(skill.get("success_rate", 0.0)) + min(0.08, 0.01 * int(skill.get("use_count", 0))),
                actions=actions, support_ids=[skill_id], goal_id=goal_id, cause_predicate="promoted_skill_match",
            ))

        for goal in context.get("goals", []):
            title = str(goal.get("title", ""))
            description = str(goal.get("description", ""))
            goal_id = str(goal.get("goal_id", "")) or None
            goal_target = self._extract_path(f"{title} {description}")
            if goal_target and any(word in f"{title} {description}".lower() for word in ("inspect", "read", "list", "check")):
                candidates.append(HypothesisCandidate(
                    key=f"goal_path_inspection:{goal_id}:{goal_target}", subject=goal_target,
                    claim="The active goal can advance through a bounded read-only path inspection.",
                    rationale="The goal contains an inspection intent and a concrete path.",
                    base_score=0.58 + 0.25 * float(goal.get("priority", 0.5)),
                    actions=[self._action("list_directory", {"path": goal_target, "limit": 200}, f"Advance goal: {title}", "Bounded directory listing", ["output contains items"], goal_id)],
                    support_ids=[goal_id] if goal_id else [], goal_id=goal_id, cause_predicate="active_goal",
                ))

        if context.get("unknowns"):
            candidates.append(HypothesisCandidate(
                key="preserve_explicit_unknowns", subject="world_model",
                claim="Unresolved contradictions should remain explicit until new evidence arrives.",
                rationale="Unknowns are evidence of uncertainty, not permission to invent a resolution.",
                base_score=0.60, actions=[self._noop("Await disambiguating evidence", None)],
                support_ids=[str(value) for value in context.get("unknowns", [])], cause_predicate="unresolved_contradiction",
            ))
        candidates.append(HypothesisCandidate(
            key="deliberate_safe_noop", subject="runtime",
            claim="No additional external action is justified.",
            rationale="Safety and evidence thresholds prefer deliberate inactivity over fabricated work.",
            base_score=0.35, actions=[self._noop("No supported action", None)], cause_predicate="insufficient_evidence",
        ))
        return candidates

    def _score(self, candidate: HypothesisCandidate, context: dict[str, Any], *, include_memories: bool) -> float:
        score = candidate.base_score
        query = tokens(f"{candidate.subject} {candidate.claim} {candidate.rationale}")
        if include_memories:
            for memory in context.get("memories", []):
                overlap = self._overlap(query, tokens(json.dumps(memory.get("content", {}), ensure_ascii=False, sort_keys=True)))
                if overlap <= 0:
                    continue
                score += 0.22 * overlap * float(memory.get("importance", 0.5)) * float(memory.get("confidence", 0.5))
                memory_id = str(memory.get("memory_id", ""))
                if memory_id:
                    candidate.memory_ids.append(memory_id)
        for fact in context.get("world_facts", []):
            overlap = self._overlap(query, tokens(f"{fact.get('subject')} {fact.get('predicate')} {fact.get('value')}"))
            if overlap <= 0:
                continue
            score += 0.12 * overlap * float(fact.get("confidence", 0.5))
            fact_id = str(fact.get("fact_id", ""))
            if fact_id:
                candidate.fact_ids.append(fact_id)
        for goal in context.get("goals", []):
            overlap = self._overlap(query, tokens(f"{goal.get('title')} {goal.get('description')}"))
            if overlap > 0:
                score += 0.15 * overlap * float(goal.get("priority", 0.5))
        penalty = 0.0
        available = set(context.get("available_tools", []))
        for action in candidate.actions:
            penalty = max(penalty, {
                RiskLevel.READ.value: 0.0,
                RiskLevel.REVERSIBLE_WRITE.value: 0.12,
                RiskLevel.HIGH.value: 0.35,
                RiskLevel.IRREVERSIBLE.value: 0.80,
            }.get(str(action.get("risk", "READ")), 0.40))
            if action.get("tool") not in available:
                penalty = max(penalty, 0.50)
                candidate.opposing_ids.append(f"unavailable_tool:{action.get('tool')}")
        penalty *= 1.0 + 0.5 * float(context.get("affect", {}).get("safety_tension", 0.0))
        return max(0.0, min(1.0, score - penalty))

    def _create_predictions(self, trace_id: str, hypothesis_id: str, actions: list[dict[str, Any]], confidence: float, created_at: str) -> None:
        with self.db.transaction() as connection:
            for index, action in enumerate(actions):
                connection.execute(
                    """
                    INSERT INTO cognitive_predictions(
                        prediction_id,trace_id,hypothesis_id,action_index,expected_json,
                        confidence,status,actual_json,created_at,resolved_at
                    ) VALUES (?,?,?,?,?,?,'OPEN',NULL,?,NULL)
                    """,
                    (
                        new_id("cogpred"), trace_id, hypothesis_id, index,
                        json.dumps({"tool": action.get("tool"), "accepted": True, "acceptance": action.get("acceptance", [])}, ensure_ascii=False, sort_keys=True),
                        confidence, created_at,
                    ),
                )

    def _temporalize(self, candidate: HypothesisCandidate, cycle_id: str) -> dict[str, Any]:
        source_ids = list(dict.fromkeys([*candidate.support_ids, cycle_id]))
        entity_id = self.temporal_world.upsert_entity(
            "cognitive_subject", candidate.subject, source_ids=source_ids,
            attributes={"latest_claim": candidate.claim}, confidence=candidate.score,
        )
        cause_relation_id = self.temporal_world.assert_relation(
            entity_id, candidate.cause_predicate, source_ids,
            value=candidate.claim, epistemic_status="INFERRED", confidence=candidate.score,
        )
        tool = str(candidate.actions[0].get("tool")) if candidate.actions else "noop"
        causal_hypothesis_id = self.temporal_world.add_causal_hypothesis(
            candidate.cause_predicate, f"action:{tool}:accepted", source_ids,
        )
        return {
            "entity_id": entity_id,
            "cause_relation_id": cause_relation_id,
            "causal_hypothesis_id": causal_hypothesis_id,
            "effect_predicate": f"action:{tool}:accepted",
        }

    def _calibrate(self, trace: Any, successes: list[bool], plan: Plan, outcomes: list[dict[str, Any]]) -> dict[str, Any]:
        if not successes:
            return {"updated": False, "reason": "no observable action outcome"}
        hypothesis = self.db.query_one(
            "SELECT temporal_json FROM cognitive_hypotheses WHERE hypothesis_id=?",
            (trace["selected_hypothesis_id"],),
        )
        if hypothesis is None:
            return {"updated": False, "reason": "selected hypothesis missing"}
        temporal = json.loads(hypothesis["temporal_json"])
        if not temporal:
            return {"updated": False, "reason": "temporal evidence unavailable"}
        observed = all(successes)
        source_ids = [str(item.get("action_id")) for item in outcomes if item.get("action_id")]
        effect_relation_id = self.temporal_world.assert_relation(
            temporal["entity_id"], temporal["effect_predicate"], source_ids or [plan.plan_id],
            value=observed, epistemic_status="OBSERVED", confidence=1.0,
        )
        trial = self.temporal_world.record_causal_trial(
            temporal["causal_hypothesis_id"], temporal["cause_relation_id"],
            observed=observed, source_ids=source_ids or [plan.plan_id], effect_relation_id=effect_relation_id,
        )
        return {"updated": True, "observed": observed, "effect_relation_id": effect_relation_id, "causal_trial": trial}

    @staticmethod
    def _decomposition(candidate: HypothesisCandidate, actions: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "goal_id": candidate.goal_id,
            "horizon": "receding",
            "steps": [
                {"stage": "EVIDENCE", "status": "CURRENT", "actions": [item.get("tool") for item in actions]},
                {"stage": "PREDICT", "status": "OPEN", "criterion": "planned action satisfies its acceptance contract"},
                {"stage": "REVISE", "status": "PENDING_OUTCOME", "criterion": "causal confidence updates from observed outcome"},
                {"stage": "CONTINUE_OR_STOP", "status": "NEXT_CYCLE", "criterion": "new evidence must justify another bounded action"},
            ],
        }

    @staticmethod
    def _action(tool: str, arguments: dict[str, Any], purpose: str, expected_result: str, acceptance: list[str], goal_id: str | None) -> dict[str, Any]:
        return {
            "tool": tool,
            "arguments": arguments,
            "purpose": purpose,
            "expected_result": expected_result,
            "risk": RiskLevel.READ.value,
            "goal_id": goal_id,
            "skill_id": None,
            "acceptance": acceptance,
        }

    @classmethod
    def _noop(cls, reason: str, goal_id: str | None) -> dict[str, Any]:
        return cls._action("noop", {"reason": reason}, "Record a deliberate no-op instead of inventing activity", "No external change", ["output ok is true"], goal_id)

    @staticmethod
    def _overlap(left: set[str], right: set[str]) -> float:
        if not left or not right:
            return 0.0
        return len(left & right) / max(1, len(left | right))

    @staticmethod
    def _extract_path(text: str) -> str | None:
        for pattern in (r"[A-Za-z]:[\\/][^\s\"']+", r"(?:^|\s)(/[^\s\"']+)"):
            match = re.search(pattern, text)
            if match:
                return (match.group(1) if match.lastindex else match.group(0)).strip()
        return None

    def _best_goal_id(self, context: dict[str, Any], text: str) -> str | None:
        query = tokens(text)
        best: tuple[float, str] | None = None
        for goal in context.get("goals", []):
            goal_id = str(goal.get("goal_id", ""))
            if not goal_id:
                continue
            score = self._overlap(query, tokens(f"{goal.get('title', '')} {goal.get('description', '')}")) * float(goal.get("priority", 0.5))
            if best is None or score > best[0]:
                best = (score, goal_id)
        return best[1] if best and best[0] > 0 else None

    @staticmethod
    def _digest_context(context: dict[str, Any]) -> dict[str, Any]:
        return {
            "cycle_id": context.get("cycle_id"),
            "workspace": context.get("workspace", []),
            "goals": context.get("goals", []),
            "memory_ids": [item.get("memory_id") for item in context.get("memories", [])],
            "world_fact_ids": [item.get("fact_id") for item in context.get("world_facts", [])],
            "matching_skill_ids": [item.get("skill_id") for item in context.get("matching_skills", [])],
            "drives": context.get("drives", {}),
            "affect": context.get("affect", {}),
            "budget": context.get("budget", {}),
            "unknowns": context.get("unknowns", []),
        }
