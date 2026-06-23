from __future__ import annotations

from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import digest_json, new_id, utc_now


def ensure_temporal_world_tables(db: Database) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS world_entities_v2 (
            entity_id TEXT PRIMARY KEY,
            entity_type TEXT NOT NULL,
            canonical_name TEXT NOT NULL,
            aliases_json TEXT NOT NULL,
            attributes_json TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_type, canonical_name)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS temporal_relations (
            relation_id TEXT PRIMARY KEY,
            subject_id TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_id TEXT,
            value_json TEXT,
            epistemic_status TEXT NOT NULL,
            confidence REAL NOT NULL,
            valid_from TEXT NOT NULL,
            valid_to TEXT,
            source_ids_json TEXT NOT NULL,
            conflict_group_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(subject_id) REFERENCES world_entities_v2(entity_id),
            FOREIGN KEY(object_id) REFERENCES world_entities_v2(entity_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_temporal_relations_subject_predicate
        ON temporal_relations(subject_id, predicate, valid_to)
        """,
        """
        CREATE TABLE IF NOT EXISTS causal_hypotheses (
            hypothesis_id TEXT PRIMARY KEY,
            cause_predicate TEXT NOT NULL,
            effect_predicate TEXT NOT NULL,
            expected_lag_seconds REAL,
            alpha REAL NOT NULL,
            beta REAL NOT NULL,
            confidence REAL NOT NULL,
            source_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(cause_predicate, effect_predicate)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS causal_trials (
            trial_id TEXT PRIMARY KEY,
            hypothesis_id TEXT NOT NULL,
            cause_relation_id TEXT NOT NULL,
            effect_relation_id TEXT,
            observed INTEGER NOT NULL,
            source_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY(hypothesis_id) REFERENCES causal_hypotheses(hypothesis_id),
            FOREIGN KEY(cause_relation_id) REFERENCES temporal_relations(relation_id),
            FOREIGN KEY(effect_relation_id) REFERENCES temporal_relations(relation_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS action_affordances (
            affordance_id TEXT PRIMARY KEY,
            entity_id TEXT NOT NULL,
            action_name TEXT NOT NULL,
            tool TEXT NOT NULL,
            prerequisites_json TEXT NOT NULL,
            expected_effects_json TEXT NOT NULL,
            risk TEXT NOT NULL,
            reversibility TEXT NOT NULL,
            confidence REAL NOT NULL,
            source_ids_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(entity_id, action_name),
            FOREIGN KEY(entity_id) REFERENCES world_entities_v2(entity_id)
        )
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class TemporalCausalWorld:
    """Evidence-linked temporal entities, relations, causal trials, and affordances."""

    EPISTEMIC_STATUSES = {"OBSERVED", "USER_REPORTED", "INFERRED", "PREDICTED"}

    def __init__(self, db: Database, ledger: EvidenceLedger) -> None:
        self.db = db
        self.ledger = ledger
        ensure_temporal_world_tables(db)

    def upsert_entity(
        self,
        entity_type: str,
        canonical_name: str,
        source_ids: list[str],
        aliases: list[str] | None = None,
        attributes: dict[str, Any] | None = None,
        confidence: float = 1.0,
    ) -> str:
        if not entity_type.strip() or not canonical_name.strip():
            raise ValueError("entity_type and canonical_name are required")
        if not source_ids:
            raise ValueError("entity requires evidence source_ids")
        confidence = self._probability(confidence)
        aliases = sorted({str(item) for item in aliases or [] if str(item).strip()})
        attributes = dict(attributes or {})
        existing = self.db.query_one(
            "SELECT * FROM world_entities_v2 WHERE entity_type=? AND canonical_name=?",
            (entity_type.strip(), canonical_name.strip()),
        )
        now = utc_now()
        if existing:
            merged_aliases = sorted(
                set(json.loads(existing["aliases_json"])) | set(aliases)
            )
            merged_attributes = dict(json.loads(existing["attributes_json"]))
            merged_attributes.update(attributes)
            merged_sources = list(
                dict.fromkeys(
                    [*json.loads(existing["source_ids_json"]), *source_ids]
                )
            )
            entity_id = str(existing["entity_id"])
            with self.db.transaction() as connection:
                connection.execute(
                    """
                    UPDATE world_entities_v2
                    SET aliases_json=?,attributes_json=?,confidence=?,source_ids_json=?,updated_at=?
                    WHERE entity_id=?
                    """,
                    (
                        json.dumps(merged_aliases, ensure_ascii=False),
                        json.dumps(merged_attributes, ensure_ascii=False, sort_keys=True),
                        max(float(existing["confidence"]), confidence),
                        json.dumps(merged_sources, ensure_ascii=False),
                        now,
                        entity_id,
                    ),
                )
                self.ledger.append(
                    "world_entity_updated",
                    {"entity_id": entity_id, "source_ids": source_ids},
                    connection,
                )
            return entity_id
        entity_id = new_id("entity")
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO world_entities_v2(
                    entity_id,entity_type,canonical_name,aliases_json,attributes_json,
                    confidence,source_ids_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    entity_id,
                    entity_type.strip(),
                    canonical_name.strip(),
                    json.dumps(aliases, ensure_ascii=False),
                    json.dumps(attributes, ensure_ascii=False, sort_keys=True),
                    confidence,
                    json.dumps(source_ids, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "world_entity_created",
                {
                    "entity_id": entity_id,
                    "entity_type": entity_type,
                    "canonical_name": canonical_name,
                    "source_ids": source_ids,
                },
                connection,
            )
        return entity_id

    def assert_relation(
        self,
        subject_id: str,
        predicate: str,
        source_ids: list[str],
        *,
        object_id: str | None = None,
        value: Any = None,
        epistemic_status: str = "OBSERVED",
        confidence: float = 1.0,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> str:
        if not predicate.strip() or not source_ids:
            raise ValueError("predicate and source_ids are required")
        if (object_id is None) == (value is None):
            raise ValueError("provide exactly one of object_id or value")
        if epistemic_status not in self.EPISTEMIC_STATUSES:
            raise ValueError(f"invalid epistemic_status: {epistemic_status}")
        if self.db.query_one(
            "SELECT entity_id FROM world_entities_v2 WHERE entity_id=?", (subject_id,)
        ) is None:
            raise KeyError(subject_id)
        if object_id and self.db.query_one(
            "SELECT entity_id FROM world_entities_v2 WHERE entity_id=?", (object_id,)
        ) is None:
            raise KeyError(object_id)
        now = utc_now()
        valid_from = valid_from or now
        value_json = (
            None if object_id else json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
        active = self.db.query_all(
            """
            SELECT * FROM temporal_relations
            WHERE subject_id=? AND predicate=? AND valid_to IS NULL
            ORDER BY created_at DESC
            """,
            (subject_id, predicate.strip()),
        )
        conflict_ids = [
            str(row["relation_id"])
            for row in active
            if (row["object_id"], row["value_json"]) != (object_id, value_json)
        ]
        conflict_group_id = None
        if conflict_ids:
            conflict_group_id = "conflict_" + digest_json(
                {"subject_id": subject_id, "predicate": predicate, "ids": conflict_ids}
            )[:24]
        relation_id = new_id("relation")
        with self.db.transaction() as connection:
            if conflict_group_id:
                connection.execute(
                    """
                    UPDATE temporal_relations SET conflict_group_id=?
                    WHERE relation_id IN (
                        SELECT relation_id FROM temporal_relations
                        WHERE subject_id=? AND predicate=? AND valid_to IS NULL
                    )
                    """,
                    (conflict_group_id, subject_id, predicate.strip()),
                )
            connection.execute(
                """
                INSERT INTO temporal_relations(
                    relation_id,subject_id,predicate,object_id,value_json,epistemic_status,
                    confidence,valid_from,valid_to,source_ids_json,conflict_group_id,created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    relation_id,
                    subject_id,
                    predicate.strip(),
                    object_id,
                    value_json,
                    epistemic_status,
                    self._probability(confidence),
                    valid_from,
                    valid_to,
                    json.dumps(source_ids, ensure_ascii=False),
                    conflict_group_id,
                    now,
                ),
            )
            self.ledger.append(
                "temporal_relation_asserted",
                {
                    "relation_id": relation_id,
                    "subject_id": subject_id,
                    "predicate": predicate,
                    "epistemic_status": epistemic_status,
                    "conflict_group_id": conflict_group_id,
                    "source_ids": source_ids,
                },
                connection,
            )
        return relation_id

    def add_causal_hypothesis(
        self,
        cause_predicate: str,
        effect_predicate: str,
        source_ids: list[str],
        expected_lag_seconds: float | None = None,
        prior_alpha: float = 1.0,
        prior_beta: float = 1.0,
    ) -> str:
        if not cause_predicate.strip() or not effect_predicate.strip() or not source_ids:
            raise ValueError("causal hypothesis requires predicates and evidence")
        if prior_alpha <= 0 or prior_beta <= 0:
            raise ValueError("causal priors must be positive")
        existing = self.db.query_one(
            "SELECT hypothesis_id FROM causal_hypotheses WHERE cause_predicate=? AND effect_predicate=?",
            (cause_predicate.strip(), effect_predicate.strip()),
        )
        if existing:
            return str(existing["hypothesis_id"])
        hypothesis_id = new_id("hypothesis")
        now = utc_now()
        confidence = prior_alpha / (prior_alpha + prior_beta)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO causal_hypotheses(
                    hypothesis_id,cause_predicate,effect_predicate,expected_lag_seconds,
                    alpha,beta,confidence,source_ids_json,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    hypothesis_id,
                    cause_predicate.strip(),
                    effect_predicate.strip(),
                    expected_lag_seconds,
                    prior_alpha,
                    prior_beta,
                    confidence,
                    json.dumps(source_ids, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "causal_hypothesis_created",
                {
                    "hypothesis_id": hypothesis_id,
                    "cause_predicate": cause_predicate,
                    "effect_predicate": effect_predicate,
                    "source_ids": source_ids,
                },
                connection,
            )
        return hypothesis_id

    def record_causal_trial(
        self,
        hypothesis_id: str,
        cause_relation_id: str,
        observed: bool,
        source_ids: list[str],
        effect_relation_id: str | None = None,
    ) -> dict[str, Any]:
        if not source_ids:
            raise ValueError("causal trial requires evidence source_ids")
        row = self.db.query_one(
            "SELECT * FROM causal_hypotheses WHERE hypothesis_id=?", (hypothesis_id,)
        )
        if row is None:
            raise KeyError(hypothesis_id)
        if self.db.query_one(
            "SELECT relation_id FROM temporal_relations WHERE relation_id=?",
            (cause_relation_id,),
        ) is None:
            raise KeyError(cause_relation_id)
        if effect_relation_id and self.db.query_one(
            "SELECT relation_id FROM temporal_relations WHERE relation_id=?",
            (effect_relation_id,),
        ) is None:
            raise KeyError(effect_relation_id)
        alpha = float(row["alpha"]) + int(observed)
        beta = float(row["beta"]) + int(not observed)
        confidence = alpha / (alpha + beta)
        trial_id = new_id("trial")
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO causal_trials(
                    trial_id,hypothesis_id,cause_relation_id,effect_relation_id,
                    observed,source_ids_json,created_at
                ) VALUES (?,?,?,?,?,?,?)
                """,
                (
                    trial_id,
                    hypothesis_id,
                    cause_relation_id,
                    effect_relation_id,
                    int(observed),
                    json.dumps(source_ids, ensure_ascii=False),
                    now,
                ),
            )
            connection.execute(
                "UPDATE causal_hypotheses SET alpha=?,beta=?,confidence=?,updated_at=? WHERE hypothesis_id=?",
                (alpha, beta, confidence, now, hypothesis_id),
            )
            self.ledger.append(
                "causal_trial_recorded",
                {
                    "trial_id": trial_id,
                    "hypothesis_id": hypothesis_id,
                    "observed": observed,
                    "confidence": confidence,
                    "source_ids": source_ids,
                },
                connection,
            )
        return {
            "trial_id": trial_id,
            "hypothesis_id": hypothesis_id,
            "alpha": alpha,
            "beta": beta,
            "confidence": confidence,
        }

    def forecast(
        self,
        subject_id: str,
        cause_predicate: str,
        minimum_confidence: float = 0.6,
    ) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            """
            SELECT * FROM causal_hypotheses
            WHERE cause_predicate=? AND confidence>=?
            ORDER BY confidence DESC
            """,
            (cause_predicate, self._probability(minimum_confidence)),
        )
        return [
            {
                "subject_id": subject_id,
                "hypothesis_id": row["hypothesis_id"],
                "predicted_predicate": row["effect_predicate"],
                "confidence": row["confidence"],
                "expected_lag_seconds": row["expected_lag_seconds"],
                "epistemic_status": "PREDICTED",
            }
            for row in rows
        ]

    def register_affordance(
        self,
        entity_id: str,
        action_name: str,
        tool: str,
        source_ids: list[str],
        prerequisites: list[dict[str, Any]] | None = None,
        expected_effects: list[dict[str, Any]] | None = None,
        risk: str = "READ",
        reversibility: str = "none",
        confidence: float = 0.5,
    ) -> str:
        if not action_name.strip() or not tool.strip() or not source_ids:
            raise ValueError("affordance requires action, tool, and evidence")
        if self.db.query_one(
            "SELECT entity_id FROM world_entities_v2 WHERE entity_id=?", (entity_id,)
        ) is None:
            raise KeyError(entity_id)
        existing = self.db.query_one(
            "SELECT affordance_id FROM action_affordances WHERE entity_id=? AND action_name=?",
            (entity_id, action_name.strip()),
        )
        affordance_id = (
            str(existing["affordance_id"]) if existing else new_id("affordance")
        )
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO action_affordances(
                    affordance_id,entity_id,action_name,tool,prerequisites_json,
                    expected_effects_json,risk,reversibility,confidence,source_ids_json,
                    created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(entity_id,action_name) DO UPDATE SET
                    tool=excluded.tool,
                    prerequisites_json=excluded.prerequisites_json,
                    expected_effects_json=excluded.expected_effects_json,
                    risk=excluded.risk,
                    reversibility=excluded.reversibility,
                    confidence=excluded.confidence,
                    source_ids_json=excluded.source_ids_json,
                    updated_at=excluded.updated_at
                """,
                (
                    affordance_id,
                    entity_id,
                    action_name.strip(),
                    tool.strip(),
                    json.dumps(prerequisites or [], ensure_ascii=False, sort_keys=True),
                    json.dumps(expected_effects or [], ensure_ascii=False, sort_keys=True),
                    risk,
                    reversibility,
                    self._probability(confidence),
                    json.dumps(source_ids, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            self.ledger.append(
                "action_affordance_registered",
                {
                    "affordance_id": affordance_id,
                    "entity_id": entity_id,
                    "action_name": action_name,
                    "tool": tool,
                    "source_ids": source_ids,
                },
                connection,
            )
        return affordance_id

    def summary(self) -> dict[str, int]:
        queries = {
            "entities": "SELECT COUNT(*) AS count FROM world_entities_v2",
            "relations": "SELECT COUNT(*) AS count FROM temporal_relations",
            "causal_hypotheses": "SELECT COUNT(*) AS count FROM causal_hypotheses",
            "causal_trials": "SELECT COUNT(*) AS count FROM causal_trials",
            "affordances": "SELECT COUNT(*) AS count FROM action_affordances",
        }
        counts = {}
        for name, query in queries.items():
            row = self.db.query_one(query)
            counts[name] = int(row["count"]) if row else 0
        conflicted = self.db.query_one(
            "SELECT COUNT(*) AS count FROM temporal_relations WHERE conflict_group_id IS NOT NULL"
        )
        counts["conflicted_relations"] = (
            int(conflicted["count"]) if conflicted else 0
        )
        return counts

    @staticmethod
    def _probability(value: float) -> float:
        value = float(value)
        if not 0.0 <= value <= 1.0:
            raise ValueError("value must be within [0, 1]")
        return value
