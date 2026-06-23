from __future__ import annotations

from collections import defaultdict
from typing import Any
import json

from .db import Database
from .evidence import EvidenceLedger
from .schemas import CandidateStatus, RiskLevel, SkillDefinition, utc_now
from .text import tokens


class SkillLibrary:
    """Versioned declarative skills. Skills cannot contain arbitrary Python code."""

    def __init__(self, db: Database, ledger: EvidenceLedger):
        self.db = db
        self.ledger = ledger

    def add(self, skill: SkillDefinition) -> str:
        self.validate_definition(skill)
        with self.db.transaction() as connection:
            connection.execute(
                """
                INSERT INTO skills(
                    skill_id,name,version,definition_json,status,success_rate,use_count,created_at,updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    skill.skill_id,
                    skill.name,
                    skill.version,
                    json.dumps(skill.to_dict(), ensure_ascii=False, sort_keys=True),
                    skill.status.value,
                    skill.success_rate,
                    skill.use_count,
                    skill.created_at,
                    utc_now(),
                ),
            )
            self.ledger.append(
                "skill_created",
                {
                    "skill_id": skill.skill_id,
                    "name": skill.name,
                    "version": skill.version,
                    "status": skill.status.value,
                },
                connection,
            )
        return skill.skill_id

    def validate_definition(self, skill: SkillDefinition) -> None:
        if not skill.steps:
            raise ValueError("skill requires at least one step")
        if len(skill.steps) > 20:
            raise ValueError("skill exceeds 20 steps")
        for index, step in enumerate(skill.steps):
            if not isinstance(step, dict) or not isinstance(step.get("tool"), str):
                raise ValueError(f"invalid skill step {index}")
            if not isinstance(step.get("arguments", {}), dict):
                raise ValueError(f"invalid skill arguments {index}")
            if step["tool"] in {"delete_file", "publish_external"}:
                raise ValueError(
                    "irreversible tools cannot be embedded in learned skills"
                )

    def active(self) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM skills WHERE status IN (?,?) ORDER BY name,version DESC",
            (CandidateStatus.APPROVED.value, CandidateStatus.PROMOTED.value),
        )
        return [json.loads(row["definition_json"]) for row in rows]

    def match(self, text: str, limit: int = 5) -> list[dict[str, Any]]:
        tokens = self._tokens(text)
        scored = []
        for skill in self.active():
            trigger = set(skill.get("trigger_terms", []))
            overlap = len(tokens & trigger) / max(1, len(trigger))
            score = 0.7 * overlap + 0.3 * float(skill.get("success_rate", 0.0))
            if score > 0:
                scored.append((score, skill))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{**skill, "match_score": score} for score, skill in scored[:limit]]

    def transition(
        self,
        skill_id: str,
        target: CandidateStatus,
        evidence: dict[str, Any],
        human_approved: bool = False,
    ) -> None:
        row = self.db.query_one("SELECT * FROM skills WHERE skill_id=?", (skill_id,))
        if row is None:
            raise KeyError(skill_id)
        current = CandidateStatus(row["status"])
        allowed = {
            CandidateStatus.PROPOSED: {
                CandidateStatus.SANDBOXED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.SANDBOXED: {
                CandidateStatus.VALIDATED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.VALIDATED: {
                CandidateStatus.APPROVED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.APPROVED: {
                CandidateStatus.PROMOTED,
                CandidateStatus.REJECTED,
            },
            CandidateStatus.PROMOTED: {CandidateStatus.ROLLED_BACK},
        }
        if target not in allowed.get(current, set()):
            raise ValueError(
                f"invalid skill transition: {current.value}->{target.value}"
            )
        if (
            target
            in {
                CandidateStatus.APPROVED,
                CandidateStatus.PROMOTED,
                CandidateStatus.ROLLED_BACK,
            }
            and not human_approved
        ):
            raise PermissionError(f"{target.value} requires human approval")
        definition = json.loads(row["definition_json"])
        definition["status"] = target.value
        definition.setdefault("transition_evidence", []).append(
            {"target": target.value, "evidence": evidence, "at": utc_now()}
        )
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skills SET status=?,definition_json=?,updated_at=? WHERE skill_id=?",
                (
                    target.value,
                    json.dumps(definition, ensure_ascii=False, sort_keys=True),
                    utc_now(),
                    skill_id,
                ),
            )
            self.ledger.append(
                "skill_transition",
                {
                    "skill_id": skill_id,
                    "from": current.value,
                    "to": target.value,
                    "evidence": evidence,
                },
                connection,
            )

    def record_use(self, skill_id: str, success: bool) -> None:
        row = self.db.query_one("SELECT * FROM skills WHERE skill_id=?", (skill_id,))
        if row is None:
            raise KeyError(skill_id)
        definition = json.loads(row["definition_json"])
        old_count = int(row["use_count"])
        old_rate = float(row["success_rate"])
        new_count = old_count + 1
        new_rate = (old_rate * old_count + int(success)) / new_count
        definition["use_count"] = new_count
        definition["success_rate"] = new_rate
        with self.db.transaction() as connection:
            connection.execute(
                "UPDATE skills SET definition_json=?,success_rate=?,use_count=?,updated_at=? WHERE skill_id=?",
                (
                    json.dumps(definition, ensure_ascii=False, sort_keys=True),
                    new_rate,
                    new_count,
                    utc_now(),
                    skill_id,
                ),
            )
            self.ledger.append(
                "skill_outcome",
                {"skill_id": skill_id, "success": success, "success_rate": new_rate},
                connection,
            )

    def propose_from_action_sequences(self, minimum_repeats: int = 3) -> list[str]:
        rows = self.db.query_all(
            """
            SELECT plan_id,tool,arguments_json,purpose,risk,status,action_id
            FROM actions WHERE status='SUCCEEDED' ORDER BY plan_id,started_at,action_id
            """
        )
        plans: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            plans[str(row["plan_id"])].append(row)
        signatures: dict[str, list[list[Any]]] = defaultdict(list)
        for actions in plans.values():
            signature = "|".join(str(row["tool"]) for row in actions)
            signatures[signature].append(actions)
        created: list[str] = []
        existing_names = {
            str(row["name"]) for row in self.db.query_all("SELECT name FROM skills")
        }
        for signature, groups in signatures.items():
            if len(groups) < minimum_repeats:
                continue
            name = "learned_" + "_then_".join(signature.split("|"))[:80]
            if name in existing_names:
                continue
            first = groups[0]
            steps = [
                {
                    "tool": row["tool"],
                    "arguments": json.loads(row["arguments_json"]),
                    "purpose": row["purpose"],
                }
                for row in first
            ]
            triggers = sorted(
                self._tokens(" ".join(str(row["purpose"]) for row in first))
            )[:20]
            source_ids = [str(row["action_id"]) for group in groups for row in group]
            skill = SkillDefinition(
                name=name,
                description=f"Learned from {len(groups)} repeated successful action sequences.",
                trigger_terms=triggers,
                steps=steps,
                risk=RiskLevel.READ
                if all(row["risk"] == "READ" for row in first)
                else RiskLevel.REVERSIBLE_WRITE,
                status=CandidateStatus.PROPOSED,
                source_episode_ids=source_ids,
            )
            created.append(self.add(skill))
        return created

    @staticmethod
    def _tokens(text: str) -> set[str]:
        return tokens(text)
