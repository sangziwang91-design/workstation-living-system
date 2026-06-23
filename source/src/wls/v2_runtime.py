from __future__ import annotations

from typing import Any

from .adaptive_growth import SkillExperimentRunner
from .bounded_recovery import FailureRecoveryEngine
from .evidence_gates import EvidenceBoundLearningSystem, EvidenceBoundSkillLibrary
from .runtime import LivingSystem


def ensure_experiment_tables(db) -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS skill_experiments (
            experiment_id TEXT PRIMARY KEY,
            skill_id TEXT NOT NULL,
            skill_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            baseline_json TEXT NOT NULL,
            result_json TEXT,
            artifact_path TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_skill_experiments_skill_time
        ON skill_experiments(skill_id, started_at DESC)
        """,
        """
        CREATE TABLE IF NOT EXISTS recovery_experiments (
            experiment_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL,
            manifest_json TEXT NOT NULL,
            baseline_json TEXT NOT NULL,
            result_json TEXT,
            artifact_path TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            FOREIGN KEY(candidate_id) REFERENCES evolution_candidates(candidate_id)
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_recovery_experiments_candidate_time
        ON recovery_experiments(candidate_id, started_at DESC)
        """,
    ]
    with db.transaction() as connection:
        for statement in statements:
            connection.execute(statement)


class LivingSystemV2(LivingSystem):
    """Imported runtime plus evidence-bound growth and recovery components."""

    def __init__(self, config) -> None:
        super().__init__(config)
        ensure_experiment_tables(self.db)
        self.skills = EvidenceBoundSkillLibrary(self.db, self.ledger)
        self.learning = EvidenceBoundLearningSystem(
            self.db, self.ledger, self.memories, self.skills
        )
        self.sleep.skills = self.skills
        self.sleep.learning = self.learning
        self.skill_experiments = SkillExperimentRunner(
            self.db, self.ledger, self.skills, config
        )
        self.recoveries = FailureRecoveryEngine(
            self.db, self.ledger, config, self.learning
        )

    def status(self) -> dict[str, Any]:
        value = super().status()
        value["skill_experiments"] = self.skill_experiments.list_experiments(limit=10)
        value["recovery_experiments"] = self.recoveries.list_experiments(limit=10)
        return value
