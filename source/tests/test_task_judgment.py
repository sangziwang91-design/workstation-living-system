"""Real decision outputs on varied public task descriptions and counterexamples.

This suite tests a deterministic WLS judgment policy, not language-model RSI.
Independent private/held-out evaluation is intentionally not present here.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "source/scripts/score_task_judgment.py"
DATA = ROOT / "source/verification/WLS_TASK_JUDGMENT_PUBLIC_V1.json"
spec = importlib.util.spec_from_file_location("wls_task_judgment_score", SCRIPT)
assert spec and spec.loader
scorer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scorer)
PUBLIC = scorer.read_taskpack(DATA)


@pytest.mark.parametrize("case", PUBLIC["cases"], ids=lambda c: c["input"]["task_id"])
def test_each_labeled_readiness_decision(case: dict) -> None:
    single = {"schema": scorer.SCHEMA, "cases": [case]}
    result = scorer.evaluate(single)
    assert result["correct"] == 1, result["cases"]


def test_full_public_taskpack_recomputes_every_label() -> None:
    result = scorer.evaluate(PUBLIC)
    assert result["total"] >= 50
    assert result["correct"] == result["total"], [
        case for case in result["cases"] if not case["passed"]
    ]
    assert result["model_calls"] == 0
    assert result["private_holdout_gain"] == "UNMEASURED"
    assert result["recursive_improvement"] == "UNPROVEN"


def test_readiness_is_not_authority_to_execute() -> None:
    sample = PUBLIC["cases"][0]["input"]
    from wls.task_judgment import TaskJudgmentEngine
    decision = TaskJudgmentEngine().judge(scorer.to_input(sample))
    assert decision.authority == "judgment_candidate_only"
    assert decision.to_dict()["claim_ceiling"].endswith("not_execution_or_model_intelligence")


def test_hint_cannot_reduce_owner_declared_risk() -> None:
    from wls.schemas import RiskLevel
    from wls.task_judgment import TaskJudgmentEngine
    case = deepcopy(PUBLIC["cases"][27]["input"])
    case["model_kind_hint"] = "deterministic"
    case["declared_risk"] = RiskLevel.IRREVERSIBLE.value
    got = TaskJudgmentEngine().judge(scorer.to_input(case))
    assert got.owner_gate
    assert got.effective_risk == RiskLevel.IRREVERSIBLE


@pytest.mark.parametrize("change", [
    lambda d: d["cases"].append(d["cases"][0]),
    lambda d: d["cases"][0]["input"].update(required_capabilities="wrong-shape"),
    lambda d: d["cases"][0]["input"].update(side_effect_class="fictional"),
    lambda d: d["cases"][0]["input"].update(secret_injection=True),
])
def test_invalid_taskpack_cannot_be_accepted(change):
    data = deepcopy(PUBLIC)
    change(data)
    with pytest.raises((ValueError, TypeError)):
        scorer.evaluate(data)


def test_cli_writes_recomputable_report(tmp_path):
    path = tmp_path / "result.json"
    exit_code = scorer.main.__code__.co_argcount
    assert exit_code == 0
    result = scorer.evaluate(PUBLIC)
    path.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    reopened = json.loads(path.read_text(encoding="utf-8"))
    assert reopened["total"] == len(PUBLIC["cases"])
    assert reopened["status"] == "PUBLIC_CALIBRATION_PASS"


def test_canonical_cli_exposes_judgment_without_creating_wls_home(
    tmp_path, capsys, monkeypatch,
):
    from wls.cli import main
    home = tmp_path / "uninitialized"
    monkeypatch.setenv("WLS_HOME", str(home))
    result = main([
        "task-judge", "--request", "Fix a Python regression",
        "--tag", "coding", "--oracle", "deterministic_test",
        "--capability", "code_workspace", "--capability", "test_runner",
    ])
    captured = json.loads(capsys.readouterr().out)
    assert result == 0
    assert captured["status"] == "READY_TO_PLAN"
    assert captured["authority"] == "judgment_candidate_only"
    assert not home.exists()


def test_cli_refuses_unavailable_code_tools(capsys):
    from wls.cli import main
    exit_code = main([
        "task-judge", "--request", "Inspect a code patch",
        "--tag", "coding", "--oracle", "deterministic_test",
        "--capability", "test_runner",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["status"] == "CAPABILITY_MISSING"
    assert "code_workspace" in payload["missing_capabilities"]


@pytest.mark.parametrize("task_text,expected_risk", [
    ("Compare published evaluation datasets in parallel", "READ"),
    ("Publish evaluation datasets to the public", "IRREVERSIBLE"),
    ("Validate JSON schema of a fixture", "READ"),
    ("Fix a schema validation bug", "REVERSIBLE_WRITE"),
    ("Investigate external source documentation", "READ"),
    ("Read a prepared email without sending", "READ"),
    ("Send a client email now", "HIGH"),
])
def test_action_word_boundary_counterfactuals(task_text, expected_risk):
    from wls.task_admission import TaskAdmissionClassifier
    assert TaskAdmissionClassifier().admit(task_text).risk_floor.value == expected_risk
