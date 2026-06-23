from __future__ import annotations

from typing import Any
import json

from .adaptive_growth import SkillExperimentRunner
from .evidence_gates import EvidenceBoundSkillLibrary
from .evolution_loop import VerifiedEvolutionLoop
from .experiments import IsolatedToolHarness, write_json_atomic
from .schemas import CandidateStatus, RiskLevel


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
