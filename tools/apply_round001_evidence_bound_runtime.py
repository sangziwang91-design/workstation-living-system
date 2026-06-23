from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"expected baseline block not found in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


runtime = ROOT / "source" / "src" / "wls" / "runtime.py"
replace_once(
    runtime,
    "from .approval import ApprovalManager\n",
    "from .adaptive_growth import SkillExperimentRunner\nfrom .approval import ApprovalManager\n",
)
replace_once(
    runtime,
    "from .autonomy import AutonomySystem\n",
    "from .autonomy import AutonomySystem\nfrom .bounded_recovery import FailureRecoveryEngine\n",
)
replace_once(
    runtime,
    "from .evidence import EvidenceLedger\nfrom .learning import LearningSystem\n",
    "from .evidence import EvidenceLedger\nfrom .evidence_gates import EvidenceBoundLearningSystem, EvidenceBoundSkillLibrary\n",
)
replace_once(runtime, "from .skills import SkillLibrary\n", "")
replace_once(
    runtime,
    "        self.skills = SkillLibrary(self.db, self.ledger)\n        self.learning = LearningSystem(self.db, self.ledger, self.memories, self.skills)\n",
    "        self.skills = EvidenceBoundSkillLibrary(self.db, self.ledger)\n        self.learning = EvidenceBoundLearningSystem(\n            self.db, self.ledger, self.memories, self.skills\n        )\n",
)
replace_once(
    runtime,
    "        self.attention = AttentionSystem(config.workspace_capacity)\n",
    "        self.skill_experiments = SkillExperimentRunner(\n            self.db, self.ledger, self.skills, config\n        )\n        self.recoveries = FailureRecoveryEngine(\n            self.db, self.ledger, config, self.learning\n        )\n        self.attention = AttentionSystem(config.workspace_capacity)\n",
)

config = ROOT / "source" / "src" / "wls" / "config.py"
replace_once(
    config,
    "    plugin_modules: list[str] = field(default_factory=list)\n",
    "    plugin_modules: list[str] = field(default_factory=list)\n"
    "    skill_validation_min_cases: int = 3\n"
    "    skill_validation_max_cases: int = 20\n"
    "    skill_validation_allowed_tools: list[str] = field(\n"
    "        default_factory=lambda: [\"noop\", \"read_file\", \"list_directory\", \"emit_note\"]\n"
    "    )\n"
    "    recovery_validation_min_occurrences: int = 3\n"
    "    recovery_validation_allowed_tools: list[str] = field(\n"
    "        default_factory=lambda: [\"noop\", \"read_file\", \"list_directory\", \"emit_note\"]\n"
    "    )\n",
)
replace_once(
    config,
    "        names: set[str] = set()\n",
    "        if not 1 <= self.skill_validation_min_cases <= self.skill_validation_max_cases <= 1000:\n"
    "            raise ValueError(\"skill validation cases must satisfy 1 <= min <= max <= 1000\")\n"
    "        if not 2 <= self.recovery_validation_min_occurrences <= 1000:\n"
    "            raise ValueError(\"recovery validation occurrences must be within 2..1000\")\n"
    "        safe_experiment_tools = {\"noop\", \"read_file\", \"list_directory\", \"emit_note\", \"write_file\"}\n"
    "        for label, tools in (\n"
    "            (\"skill_validation_allowed_tools\", self.skill_validation_allowed_tools),\n"
    "            (\"recovery_validation_allowed_tools\", self.recovery_validation_allowed_tools),\n"
    "        ):\n"
    "            if not tools or len(tools) != len(set(tools)):\n"
    "                raise ValueError(f\"{label} must be a non-empty unique list\")\n"
    "            unsupported = sorted(set(tools) - safe_experiment_tools)\n"
    "            if unsupported:\n"
    "                raise ValueError(f\"{label} contains unsupported tools: {unsupported}\")\n"
    "        names: set[str] = set()\n",
)

scenario = ROOT / "source" / "tests" / "test_e2e_scenarios.py"
replace_once(
    scenario,
    "from wls.config import SensorConfig\n",
    "from wls.adaptive_growth import SkillExperimentRunner\nfrom wls.config import SensorConfig\n",
)
old_test = '''def test_11_promoted_skill_changes_future_behavior(runtime_factory):
    runtime = runtime_factory()
    skill = SkillDefinition(
        name="respond_alpha",
        description="Perform a learned no-op when alpha appears.",
        trigger_terms=["alpha"],
        steps=[{"tool": "noop", "arguments": {"learned": True}, "purpose": "apply learned alpha response", "acceptance": ["output ok is true"]}],
        source_episode_ids=["episode_1", "episode_2", "episode_3"],
    )
    runtime.skills.add(skill)
    runtime.skills.transition(skill.skill_id, CandidateStatus.SANDBOXED, {"tests": 1})
    runtime.skills.transition(skill.skill_id, CandidateStatus.VALIDATED, {"passed": True})
    runtime.skills.transition(skill.skill_id, CandidateStatus.APPROVED, {"human": True}, human_approved=True)
    runtime.skills.transition(skill.skill_id, CandidateStatus.PROMOTED, {"release": "test"}, human_approved=True)
    runtime.ingest_event(Event(event_type="external.alpha", source="test", payload={"message": "alpha"}, salience_hint=1.0))
    runtime.run_cycle()
    row = runtime.db.query_one("SELECT use_count,success_rate FROM skills WHERE skill_id=?", (skill.skill_id,))
    assert row["use_count"] == 1
    assert row["success_rate"] == 1.0
'''
new_test = '''def test_11_promoted_skill_changes_future_behavior(runtime_factory):
    runtime = runtime_factory()
    source_action_ids = []
    for index in range(3):
        source_plan = Plan(
            rationale=f"alpha source success {index}",
            actions=[ActionSpec(
                tool="noop",
                arguments={"learned": True},
                purpose="apply learned alpha response",
                expected_result="no-op succeeds",
                acceptance=["output ok is true"],
            )],
        )
        runtime._persist_plan_and_ack_events(f"alpha_source_{index}", source_plan, [])
        assert runtime._execute_plan(source_plan)[0]["success"] is True
        source_action_ids.append(source_plan.actions[0].action_id)
    skill = SkillDefinition(
        name="respond_alpha",
        description="Perform a learned no-op when alpha appears.",
        trigger_terms=["alpha"],
        steps=[{"tool": "noop", "arguments": {"learned": True}, "purpose": "apply learned alpha response", "acceptance": ["output ok is true"]}],
        source_episode_ids=source_action_ids,
    )
    runtime.skills.add(skill)
    experiment = SkillExperimentRunner(
        runtime.db, runtime.ledger, runtime.skills, runtime.config
    ).run(skill.skill_id)
    assert experiment["status"] == "PASSED"
    runtime.skills.transition(skill.skill_id, CandidateStatus.APPROVED, {"human": True}, human_approved=True)
    runtime.skills.transition(skill.skill_id, CandidateStatus.PROMOTED, {"release": "test"}, human_approved=True)
    runtime.ingest_event(Event(event_type="external.alpha", source="test", payload={"message": "alpha"}, salience_hint=1.0))
    runtime.run_cycle()
    row = runtime.db.query_one("SELECT use_count,success_rate FROM skills WHERE skill_id=?", (skill.skill_id,))
    assert row["use_count"] == 1
    assert row["success_rate"] == 1.0
'''
replace_once(scenario, old_test, new_test)

print("Round 001 evidence-bound runtime patch applied.")
