from __future__ import annotations

from typing import Any
import json

from .learning import LearningSystem
from .schemas import CandidateStatus, digest_json
from .skills import SkillLibrary


class EvidenceBoundSkillLibrary(SkillLibrary):
    """Skill lifecycle whose machine stages require persisted experiment evidence."""

    def transition(
        self,
        skill_id: str,
        target: CandidateStatus,
        evidence: dict[str, Any],
        human_approved: bool = False,
    ) -> None:
        if target in {CandidateStatus.SANDBOXED, CandidateStatus.VALIDATED}:
            self._verify_experiment(skill_id, target, evidence)
        super().transition(skill_id, target, evidence, human_approved)

    def _verify_experiment(
        self, skill_id: str, target: CandidateStatus, evidence: dict[str, Any]
    ) -> None:
        experiment_id = str(evidence.get("experiment_id", ""))
        if not experiment_id:
            raise ValueError(f"{target.value} requires a persisted skill experiment")
        row = self.db.query_one(
            "SELECT * FROM skill_experiments WHERE experiment_id=? AND skill_id=?",
            (experiment_id, skill_id),
        )
        if row is None:
            raise ValueError("skill experiment evidence does not exist")
        if target == CandidateStatus.SANDBOXED:
            manifest = json.loads(row["manifest_json"])
            if evidence.get("manifest_sha256") != digest_json(manifest):
                raise ValueError("skill manifest digest mismatch")
            if row["status"] != "RUNNING":
                raise ValueError("skill sandbox requires a running experiment")
            return
        if row["status"] != "PASSED" or not row["result_json"]:
            raise ValueError("skill validation requires a passed experiment")
        result = json.loads(row["result_json"])
        if evidence.get("result_sha256") != digest_json(result):
            raise ValueError("skill result digest mismatch")
        if not result.get("passed") or int(result.get("regressions", 1)) != 0:
            raise ValueError("skill experiment did not pass without regressions")
        if int(result.get("passed_cases", 0)) < int(result.get("candidate_cases", 1)):
            raise ValueError("not all skill cases passed")


class EvidenceBoundLearningSystem(LearningSystem):
    """Candidate lifecycle with evidence-bound repeated-failure recovery stages."""

    def transition_candidate(
        self,
        candidate_id: str,
        target: CandidateStatus,
        evidence: dict[str, Any],
        human_approved: bool = False,
    ) -> None:
        if target in {CandidateStatus.SANDBOXED, CandidateStatus.VALIDATED}:
            self._verify_recovery(candidate_id, target, evidence)
        super().transition_candidate(candidate_id, target, evidence, human_approved)

    def _verify_recovery(
        self, candidate_id: str, target: CandidateStatus, evidence: dict[str, Any]
    ) -> None:
        candidate = self.db.query_one(
            "SELECT candidate_type FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if candidate is None or candidate["candidate_type"] not in {
            "failure_repair",
            "failure_recovery",
        }:
            return
        experiment_id = str(evidence.get("experiment_id", ""))
        if not experiment_id:
            raise ValueError(f"{target.value} requires a persisted recovery experiment")
        row = self.db.query_one(
            "SELECT * FROM recovery_experiments WHERE experiment_id=? AND candidate_id=?",
            (experiment_id, candidate_id),
        )
        if row is None:
            raise ValueError("recovery experiment evidence does not exist")
        if target == CandidateStatus.SANDBOXED:
            manifest = json.loads(row["manifest_json"])
            if evidence.get("manifest_sha256") != digest_json(manifest):
                raise ValueError("recovery manifest digest mismatch")
            if row["status"] != "RUNNING":
                raise ValueError("recovery sandbox requires a running experiment")
            return
        if row["status"] != "PASSED" or not row["result_json"]:
            raise ValueError("recovery validation requires a passed experiment")
        result = json.loads(row["result_json"])
        if evidence.get("result_sha256") != digest_json(result):
            raise ValueError("recovery result digest mismatch")
        if not result.get("passed") or int(result.get("regressions", 1)) != 0:
            raise ValueError("recovery experiment did not pass without regressions")
        if float(result.get("candidate_pass_rate", 0.0)) <= float(
            result.get("baseline_pass_rate", 1.0)
        ):
            raise ValueError("recovery candidate did not improve frozen baseline")
