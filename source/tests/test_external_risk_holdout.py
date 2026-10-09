"""Synthetic tests only; Nuomi's private holdout data never enters GitHub."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "source/scripts/run_external_risk_holdout.py"


@pytest.fixture
def scorer():
    spec = importlib.util.spec_from_file_location("trusted_external_holdout", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def fixtures(tmp_path: Path, *, regressions: int = 0):
    """Public synthetic labels; inputs deliberately lack proprietary tasks."""
    cases = []
    for i in range(60):
        cases.append({
            "id": f"synthetic-{i:03d}",
            "request": f"quartzbananae24 neutral observation number {i}",
            "expected_risk": "READ" if i < regressions else "HIGH",
        })
    question_path = tmp_path / "nuomi-private-placeholder.json"
    question_path.write_text(json.dumps({
        "schema": "wls.risk_holdout.v1", "cases": cases,
    }), encoding="utf-8")
    base = tmp_path / "baseline.json"
    cand = tmp_path / "candidate.json"
    base.write_text(json.dumps({"risk_terms": {}}), encoding="utf-8")
    cand.write_text(json.dumps({
        "risk_terms": {"HIGH": ["quartzbananae24"]}
    }), encoding="utf-8")
    return question_path, base, cand


def invoke(question_path: Path, base: Path, candidate: Path,
           *options: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(ROOT),
         "--cases", str(question_path), "--baseline", str(base),
         "--candidate", str(candidate), *options],
        capture_output=True, text=True, check=False,
    )
    return proc.returncode, json.loads(proc.stdout)


def frozen_options(scorer, case_path: Path):
    from hashlib import sha256
    return (
        "--expected-case-sha256", sha256(case_path.read_bytes()).hexdigest(),
        "--expected-evaluator-sha256", scorer.evaluator_digest(ROOT),
    )


def test_genuine_paired_improvement_is_candidate_only(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    code, report = invoke(case, base, cand, *frozen_options(scorer, case))
    assert code == 0
    assert report["status"] == "QUALIFIED_CANDIDATE_ONLY"
    assert report["metric"]["n"] == 60
    assert report["metric"]["paired_corrected"] == 60
    assert report["metric"]["paired_regressed"] == 0
    assert report["metric"]["candidate_wilson_95"][0] > 0.93
    assert report["authority"] == "Nuomi_review_required_no_live_promotion"
    assert "quartzbananae24" not in json.dumps(report)


def test_one_private_task_regression_blocks_even_large_gain(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path, regressions=1)
    code, report = invoke(case, base, cand, *frozen_options(scorer, case))
    assert code == 1
    assert report["status"] == "NO_MEASURED_GAIN"
    assert report["metric"]["paired_regressed"] == 1
    assert report["metric"]["delta"] > 0


def test_changed_grading_standard_never_counts_as_improvement(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    manifest = list(frozen_options(scorer, case))
    manifest[-1] = "0" * 64
    code, response = invoke(case, base, cand, *manifest)
    assert code == 3
    assert response["status"] == "STANDARD_MOVED"
    assert response["eligible"] is False
    assert "metric" not in response


def test_changed_private_cases_never_counts_as_improvement(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    args = frozen_options(scorer, case)
    case.write_text(case.read_text(encoding="utf-8") + " ", encoding="utf-8")
    code, response = invoke(case, base, cand, *args)
    assert code == 3
    assert response["status"] == "STANDARD_MOVED"


def test_absent_manifest_is_unmeasured(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    code, response = invoke(case, base, cand)
    assert code == 2
    assert response["status"] == "UNMEASURED"


def test_duplicate_cases_and_invalid_labels_fail_closed(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    args = frozen_options(scorer, case)
    data = json.loads(case.read_text(encoding="utf-8"))
    data["cases"][1]["id"] = data["cases"][0]["id"]
    case.write_text(json.dumps(data), encoding="utf-8")
    code, response = invoke(case, base, cand, *frozen_options(scorer, case))
    assert code == 2
    assert response["status"] == "UNMEASURED"
    data["cases"][1]["id"] = "unique-new"
    data["cases"][0]["expected_risk"] = "NOT_REAL"
    case.write_text(json.dumps(data), encoding="utf-8")
    code, response = invoke(case, base, cand, *frozen_options(scorer, case))
    assert code == 2
    assert response["status"] == "UNMEASURED"


def test_manifest_only_does_not_publish_private_case_data(tmp_path):
    case, base, cand = fixtures(tmp_path)
    code, report = invoke(case, base, cand, "--manifest-only")
    assert code == 0
    assert report["status"] == "MANIFEST_ONLY"
    assert "cases" not in report
    assert "quartzbananae24" not in json.dumps(report)


def test_private_cases_inside_repo_are_rejected(scorer, tmp_path):
    base = tmp_path / "baseline.json"
    base.write_text('{"risk_terms": {}}', encoding="utf-8")
    proc = subprocess.run([
        sys.executable, str(SCRIPT), "--repo", str(ROOT),
        "--cases", str(ROOT / "source/tests/test_ci_feedback.py"),
        "--baseline", str(base), "--candidate", str(base),
        "--manifest-only",
    ], capture_output=True, text=True, check=False)
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "UNMEASURED"


def test_small_private_holdout_is_unmeasured_even_when_perfect(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    data = json.loads(case.read_text(encoding="utf-8"))
    data["cases"] = data["cases"][:4]
    case.write_text(json.dumps(data), encoding="utf-8")
    code, result = invoke(case, base, cand, *frozen_options(scorer, case))
    assert code == 2
    assert result["status"] == "UNMEASURED"
    assert result["n"] == 4


@pytest.mark.parametrize("minimum", [1, 59])
def test_caller_cannot_lower_sealed_minimum(scorer, tmp_path, minimum):
    case, base, cand = fixtures(tmp_path)
    code, result = invoke(
        case, base, cand, *frozen_options(scorer, case),
        "--min-n", str(minimum),
    )
    assert code == 2
    assert result["status"] == "UNMEASURED"
    assert result["eligible"] is False


def test_caller_can_raise_sealed_minimum(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    code, result = invoke(
        case, base, cand, *frozen_options(scorer, case), "--min-n", "61",
    )
    assert code == 2
    assert result["status"] == "UNMEASURED"
    assert result["n"] == 60


@pytest.mark.parametrize("module_name", ["wls.schemas", "wls.task_admission"])
def test_imported_grading_code_must_match_hashed_checkout(
    scorer, monkeypatch, tmp_path, module_name,
):
    module = sys.modules[module_name]
    monkeypatch.setattr(module, "__file__", str(tmp_path / "shadow.py"))
    with pytest.raises(ValueError, match="trusted evaluator source"):
        scorer.evaluator_digest(ROOT)


def test_scorer_script_must_match_hashed_checkout(scorer, monkeypatch, tmp_path):
    monkeypatch.setattr(scorer, "__file__", str(tmp_path / "shadow.py"))
    with pytest.raises(ValueError, match="trusted evaluator source"):
        scorer.evaluator_digest(ROOT)


def test_other_checkout_cannot_certify_loaded_code(tmp_path):
    # Identical bytes are not enough: the scorer must execute the hashed checkout.
    copied = tmp_path / "other_checkout"
    for relative in (
        "source/src/wls/task_admission.py",
        "source/src/wls/schemas.py",
        "source/scripts/run_external_risk_holdout.py",
    ):
        destination = copied / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / relative).read_bytes())
    case, base, cand = fixtures(tmp_path)
    proc = subprocess.run(
        [
            sys.executable, str(SCRIPT), "--repo", str(copied),
            "--cases", str(case), "--manifest-only",
        ],
        check=False, capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert json.loads(proc.stdout)["status"] == "UNMEASURED"


def test_repeated_task_text_with_distinct_ids_is_not_independent(scorer, tmp_path):
    case, base, cand = fixtures(tmp_path)
    data = json.loads(case.read_text(encoding="utf-8"))
    data["cases"][1]["request"] = (
        "  " + data["cases"][0]["request"].upper() + "  "
    )
    case.write_text(json.dumps(data), encoding="utf-8")
    code, result = invoke(case, base, cand, *frozen_options(scorer, case))
    assert code == 2
    assert result["status"] == "UNMEASURED"

