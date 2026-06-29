from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from wls.examiner import AnchorBank, Constitution, ExaminerStore, ExaminerSystem, ExaminerVersion, StandaloneDatabase, Verdict, current_implementation_digest
from wls.examiner.fixtures import RULE_SEQUENCE, hidden_holdout_cases, make_control, make_defect
from wls.examiner.system import IdFactory


ROOT = Path(__file__).resolve().parents[2]
CONSTITUTION = ROOT / ".evolution/examiner/constitution.json"


def build_system(tmp_path: Path, rules=RULE_SEQUENCE, epoch="EPOCH-0010"):
    constitution = Constitution.load(str(CONSTITUTION))
    holdout = AnchorBank(hidden_holdout_cases(), "holdout", "test")
    version = ExaminerVersion(
        version_id=f"examiner-{epoch}",
        epoch_id=epoch,
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=tuple(rules),
        created_at="2026-06-28T00:00:00+00:00",
    )
    store = ExaminerStore(StandaloneDatabase(tmp_path / "examiner.db"))
    system = ExaminerSystem(
        constitution,
        store,
        version,
        clock=lambda: "2026-06-28T00:00:00+00:00",
        id_factory=IdFactory("test", deterministic=True),
    )
    return system, holdout


def test_final_rules_classify_entire_holdout(tmp_path: Path):
    system, holdout = build_system(tmp_path)
    results = [system.evaluate(case.candidate, record=False).verdict for case in holdout.cases]
    assert results == [case.expected for case in holdout.cases]


def test_each_rule_repairs_its_orthogonal_defect(tmp_path: Path):
    constitution = Constitution.load(str(CONSTITUTION))
    holdout = AnchorBank(hidden_holdout_cases(), "holdout", "test")
    store = ExaminerStore(StandaloneDatabase(tmp_path / "orthogonal.db"))
    baseline = ExaminerVersion(
        version_id="baseline",
        epoch_id="EPOCH-0000",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=(),
        created_at="2026-06-28T00:00:00+00:00",
    )
    system = ExaminerSystem(constitution, store, baseline, id_factory=IdFactory("orth", deterministic=True))
    enabled: list[str] = []
    working = baseline
    for index, rule in enumerate(RULE_SEQUENCE, 1):
        defect = make_defect(rule, 0)
        assert system.evaluate(defect.candidate, version=working, record=False).verdict == Verdict.ALLOW
        enabled.append(rule)
        challenger = ExaminerVersion(
            version_id=f"candidate-{index}",
            epoch_id=f"CANDIDATE-{index:04d}",
            constitution_digest=constitution.digest,
            anchor_manifest_digest=holdout.digest,
            implementation_digest=current_implementation_digest(),
            enabled_rules=tuple(enabled),
            parent_version_id=working.version_id,
            created_at="2026-06-28T00:00:00+00:00",
        )
        assert system.evaluate(defect.candidate, version=challenger, record=False).verdict == defect.expected
        working = challenger


def test_critical_failure_cannot_be_averaged_away(tmp_path: Path):
    system, _ = build_system(tmp_path)
    candidate = make_defect("R001_SCOPE_INTEGRITY", 7).candidate
    candidate = replace(candidate, metadata={**candidate.metadata, "positive_score": 0.999})
    result = system.evaluate(candidate, record=False)
    assert result.verdict == Verdict.BLOCK
    assert any(f.severity.value == "CRITICAL" for f in result.findings)


def test_good_control_remains_allowed(tmp_path: Path):
    system, _ = build_system(tmp_path)
    assert system.evaluate(make_control(99).candidate, record=False).verdict == Verdict.ALLOW


def test_version_constitution_digest_is_immutable(tmp_path: Path):
    system, _ = build_system(tmp_path)
    bad = replace(system.active_version, version_id="bad", constitution_digest="0" * 64)
    with pytest.raises(RuntimeError, match="constitution digest mismatch"):
        system.evaluate(make_control(1).candidate, version=bad, record=False)


def test_version_implementation_digest_is_immutable(tmp_path: Path):
    system, _ = build_system(tmp_path)
    bad = replace(
        system.active_version,
        version_id="bad-implementation",
        implementation_digest="0" * 64,
    )
    with pytest.raises(RuntimeError, match="implementation digest mismatch"):
        system.evaluate(make_control(2).candidate, version=bad, record=False)
