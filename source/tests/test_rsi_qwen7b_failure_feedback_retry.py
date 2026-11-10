"""Fail-closed provenance contract tests; no stub result counts as model gain."""
from __future__ import annotations

from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from measure_rsi_qwen7b_failure_feedback_retry import (
    IMAGE, GRADE_SHA, MODEL_ID, QWEN_7B_SHA256,
    PUBLIC_FAILURE_SUMMARY, read_genuine_failed_7b, run,
)


@pytest.mark.parametrize("layout", ["missing", "garbled", "false_model"])
def test_missing_or_fabricated_7b_history_is_rejected(tmp_path, layout):
    if layout == "garbled":
        (tmp_path / "rsi-qwen7b-method-observed.json").write_text("{", encoding="utf-8")
    elif layout == "false_model":
        (tmp_path / "rsi-qwen7b-method-observed.json").write_text(
            '{"status":"REAL_MODEL_MEASURED","model_calls_observed":1}',
            encoding="utf-8",
        )
    with pytest.raises((ValueError, OSError)):
        read_genuine_failed_7b(tmp_path)


def test_previous_failure_summary_is_public_behavior_not_scoring_code():
    assert "all([])" in PUBLIC_FAILURE_SUMMARY
    assert "digest strings" in PUBLIC_FAILURE_SUMMARY
    assert "independent C1 grader" not in PUBLIC_FAILURE_SUMMARY
    assert "assert not " not in PUBLIC_FAILURE_SUMMARY


def test_retry_path_uses_one_real_port_only_and_keeps_unproven_claim(monkeypatch, tmp_path):
    import measure_rsi_qwen7b_failure_feedback_retry as study

    monkeypatch.setattr(study, "read_genuine_failed_7b",
                        lambda _: {"source_action": 42809565828})
    issued = []

    def measured(model_file, image, root, *, model_variant, prior_failure_summary):
        issued.append((image, model_variant, prior_failure_summary))
        return {
            "status": "REAL_MODEL_MEASURED",
            "model_id": MODEL_ID,
            "model_sha256": QWEN_7B_SHA256,
            "model_calls_observed": 1,
            "evaluator_digest": GRADE_SHA,
            "image": IMAGE,
            "source_changed": True,
            "independently_passing_changed_source": False,
        }

    monkeypatch.setattr(study, "canonical_method_run", measured)
    out = run(
        prior_root=tmp_path / "historic",
        model_file=tmp_path / "no-model-needed-for-stub",
        root=tmp_path / "output",
        image=IMAGE,
    )
    assert len(issued) == 1
    assert issued[0] == (IMAGE, "qwen7b", PUBLIC_FAILURE_SUMMARY)
    assert out["actual_model_calls_this_run"] == 1
    assert out["retry_source_changed"] is True
    assert out["retry_behavior_and_regression_passed"] is False
    assert out["prior_parent_used_for_code"] is False
    assert out["rsi"] == "UNPROVEN"
