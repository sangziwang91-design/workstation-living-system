from __future__ import annotations

from typing import Any
import json

from .adaptive_growth import SkillExperimentRunner
from .evidence_gates import EvidenceBoundSkillLibrary
from .evolution_loop import VerifiedEvolutionLoop
from .experiments import IsolatedToolHarness, write_json_atomic
from .schemas import (
    CandidateStatus,
    RiskLevel,
    SkillDefinition,
    digest_json,
    utc_now,
)


class QuarantiningSkillLibrary(EvidenceBoundSkillLibrary):
    """Use one version per skill name and quarantine degraded deployments.

    Degradation does not silently perform the irreversible lifecycle rollback.
    Instead the skill name is removed from matching until an owner explicitly
    rolls the deployment back. A prior version becomes available only after the
    newer version reaches ROLLED_BACK and therefore leaves the active query.
    """

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM skills WHERE status IN (?,?) ORDER BY name,version DESC",
            (CandidateStatus.APPROVED.value, CandidateStatus.PROMOTED.value),
        )
        has_deployments = (
            self.db.query_one(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='skill_deployments'"
            )
            is not None
        )
        selected: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for row in rows:
            name = str(row["name"])
            if name in seen_names:
                continue
            seen_names.add(name)
            if has_deployments and str(row["status"]) == CandidateStatus.PROMOTED.value:
                deployment = self.db.query_one(
                    """
                    SELECT status FROM skill_deployments
                    WHERE skill_id=? ORDER BY promoted_at DESC LIMIT 1
                    """,
                    (str(row["skill_id"]),),
                )
                if deployment is not None and str(deployment["status"]) == "DEGRADED":
                    continue
            selected.append(json.loads(row["definition_json"]))
        return selected


class VerifiedSkillExperimentRunner(SkillExperimentRunner):
    """Replay explicit retained-failure cases including their fixtures."""

    def _cases(
        self, definition: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[Any]]:
        source_rows: list[Any] = []
        for action_id in definition.get("source_episode_ids", []):
            row = self.db.query_one(
                "SELECT * FROM actions WHERE action_id=?", (str(action_id),)
            )
            if row is not None:
                source_rows.append(row)
        explicit = definition.get("validation_cases", [])
        if isinstance(explicit, list) and explicit:
            cases = [dict(item) for item in explicit if isinstance(item, dict)]
            if not cases:
                raise ValueError("validation_cases contains no structured cases")
            return cases, source_rows
        return super()._cases(definition)

    def _run_case(
        self, definition: dict[str, Any], case: dict[str, Any], case_root
    ) -> dict[str, Any]:
        harness = IsolatedToolHarness(case_root / "fs", self.allowed_tools)
        overrides = list(case.get("step_arguments", []))
        fixtures = [
            dict(item) for item in case.get("fixtures", []) if isinstance(item, dict)
        ]
        steps = []
        for index, step in enumerate(definition.get("steps", [])):
            arguments = dict(step.get("arguments", {}))
            if index < len(overrides) and isinstance(overrides[index], dict):
                arguments = dict(overrides[index])
            steps.append(
                harness.execute(
                    tool=str(step.get("tool", "")),
                    arguments=arguments,
                    purpose=str(
                        step.get("purpose", definition.get("description", "skill"))
                    ),
                    acceptance=[str(value) for value in step.get("acceptance", [])],
                    risk=RiskLevel(
                        str(definition.get("risk", RiskLevel.READ.value))
                    ),
                    fixtures=fixtures,
                )
            )
        result = {
            "name": str(case.get("name", case_root.name)),
            "source_plan_id": case.get("source_plan_id"),
            "source_action_id": case.get("source_action_id"),
            "synthetic": bool(case.get("synthetic", False)),
            "passed": bool(steps) and all(item.get("passed") for item in steps),
            "steps": steps,
        }
        write_json_atomic(case_root / "result.json", result)
        return result


class VerifiedEvolutionLoopV2(VerifiedEvolutionLoop):
    """Correct retained-failure normalization and risk handling."""

    def materialize_skill(
        self, candidate_id: str, recovery_experiment_id: str
    ) -> dict[str, Any]:
        existing = self.db.query_one(
            "SELECT * FROM evolution_skill_links WHERE candidate_id=?",
            (candidate_id,),
        )
        if existing is not None:
            if str(existing["recovery_experiment_id"]) != recovery_experiment_id:
                raise ValueError("candidate is already linked to another recovery experiment")
            return self._link_view(existing)

        candidate = self.db.query_one(
            "SELECT * FROM evolution_candidates WHERE candidate_id=?",
            (candidate_id,),
        )
        if candidate is None:
            raise KeyError(candidate_id)
        if CandidateStatus(str(candidate["status"])) != CandidateStatus.VALIDATED:
            raise ValueError("candidate must be VALIDATED before skill materialization")
        if str(candidate["candidate_type"]) not in {
            "failure_repair",
            "failure_recovery",
        }:
            raise ValueError("only repeated-failure candidates can materialize a recovery skill")

        experiment = self.db.query_one(
            "SELECT * FROM recovery_experiments WHERE experiment_id=? AND candidate_id=?",
            (recovery_experiment_id, candidate_id),
        )
        if experiment is None:
            raise ValueError("recovery experiment does not exist for candidate")
        result = self._require_passed_recovery(experiment)
        strategy = str(experiment["strategy"])
        source_ids = [str(value) for value in json.loads(candidate["source_ids_json"])]
        source_rows = self._source_actions(source_ids)
        minimum_cases = max(1, int(self.config.failure_recovery_min_cases))
        if len(source_rows) < minimum_cases:
            raise ValueError(
                f"materialization requires at least {minimum_cases} retained failure actions"
            )

        tools = {str(row["tool"]) for row in source_rows}
        if len(tools) != 1:
            raise ValueError("one recovery skill cannot combine multiple tools")
        tool = next(iter(tools))
        recovered = [
            self.recoveries._recover(row, strategy)  # noqa: SLF001
            for row in source_rows
        ]
        arguments = self._generalize_arguments(
            [dict(item["arguments"]) for item in recovered]
        )
        acceptance = self._stable_acceptance(
            [list(item["acceptance"]) for item in recovered]
        )
        risk = self._highest_risk([str(row["risk"]) for row in source_rows])
        problem_key = self._problem_key(candidate)
        name = self._skill_name(tool, problem_key)
        version_row = self.db.query_one(
            "SELECT MAX(version) AS version FROM skills WHERE name=?", (name,)
        )
        version = int(version_row["version"] or 0) + 1 if version_row else 1
        trigger_terms = self._trigger_terms(candidate, source_rows, tool)
        validation_cases = [
            {
                "name": f"retained-failure-{index + 1:03d}",
                "source_action_id": str(row["action_id"]),
                "step_arguments": [dict(recovered_case["arguments"])],
                "fixtures": [
                    dict(value) for value in recovered_case.get("fixtures", [])
                ],
                "synthetic": False,
                "failure_error": str(row["error"] or ""),
            }
            for index, (row, recovered_case) in enumerate(
                zip(source_rows, recovered, strict=True)
            )
        ]
        definition = SkillDefinition(
            name=name,
            description=(
                f"Evidence-bound recovery for {problem_key}; generated only from "
                f"retained runtime failures and recovery experiment {recovery_experiment_id}."
            ),
            trigger_terms=trigger_terms,
            steps=[
                {
                    "tool": tool,
                    "arguments": arguments,
                    "purpose": f"Apply validated recovery for {problem_key}",
                    "acceptance": acceptance,
                }
            ],
            risk=risk,
            source_episode_ids=source_ids,
            version=version,
        )
        skill_id = self.skills.add(definition)
        stored = definition.to_dict()
        stored.update(
            {
                "origin_candidate_id": candidate_id,
                "origin_recovery_experiment_id": recovery_experiment_id,
                "failure_signature": problem_key,
                "validation_cases": validation_cases,
                "frozen_recovery_baseline": json.loads(experiment["baseline_json"]),
                "recovery_result_sha256": digest_json(result),
                "acceptance_thresholds": {
                    "minimum_cases": minimum_cases,
                    "all_cases_must_pass": True,
                    "maximum_regressions": 0,
                    "candidate_rate_must_exceed_baseline": True,
                },
                "materialized_at": utc_now(),
            }
        )
        now = utc_now()
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skills SET definition_json=? WHERE skill_id=?",
                (json.dumps(stored, ensure_ascii=False, sort_keys=True), skill_id),
            )
            connection.execute(
                """
                INSERT INTO evolution_skill_links(
                    candidate_id,recovery_experiment_id,skill_id,state,created_at,updated_at
                ) VALUES (?,?,?,?,?,?)
                """,
                (
                    candidate_id,
                    recovery_experiment_id,
                    skill_id,
                    CandidateStatus.PROPOSED.value,
                    now,
                    now,
                ),
            )
            evidence_id = self.ledger.append(
                "evolution_skill_materialized",
                {
                    "candidate_id": candidate_id,
                    "recovery_experiment_id": recovery_experiment_id,
                    "skill_id": skill_id,
                    "skill_version": version,
                    "definition_sha256": digest_json(stored),
                    "source_action_ids": source_ids,
                },
                connection,
            )
        return {
            "candidate_id": candidate_id,
            "recovery_experiment_id": recovery_experiment_id,
            "skill_id": skill_id,
            "skill_version": version,
            "status": CandidateStatus.PROPOSED.value,
            "evidence_id": evidence_id,
        }

    def _source_actions(self, source_ids: list[str]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for action_id in source_ids:
            row = self.db.query_one(
                "SELECT * FROM actions WHERE action_id=?", (action_id,)
            )
            if row is None:
                raise ValueError(f"retained failure action is missing: {action_id}")
            if str(row["status"]) != "FAILED":
                raise ValueError(
                    f"source action is not a resolved retained failure: {action_id}"
                )
            rows.append(self.recoveries._freeze_action(row))  # noqa: SLF001
        return rows

    @staticmethod
    def _problem_key(candidate) -> str:
        proposal = json.loads(candidate["proposal_json"])
        value = (
            proposal.get("problem_key")
            or proposal.get("problem_signature")
            or candidate["title"]
        )
        return str(value)[:500]

    @staticmethod
    def _highest_risk(values: list[str]) -> RiskLevel:
        order = {
            RiskLevel.READ.value: 0,
            RiskLevel.REVERSIBLE_WRITE.value: 1,
            RiskLevel.HIGH.value: 2,
            RiskLevel.IRREVERSIBLE.value: 3,
        }
        invalid = sorted(set(values) - set(order))
        if invalid:
            raise ValueError(f"unsupported risk values: {invalid}")
        selected = max(values, key=order.__getitem__)
        if selected == RiskLevel.IRREVERSIBLE.value:
            raise ValueError("irreversible failures cannot become learned skills")
        return RiskLevel(selected)
