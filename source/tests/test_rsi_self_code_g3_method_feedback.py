"""Deterministic protocol tests for G3; not evidence of real model intelligence."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from wls.rsi_model_port import RsiModelCapabilities, RsiProposalResponse

from measure_rsi_self_code_g3_method_feedback import (
    IMAGE, GRADE_SHA, LARGER_MODEL_SHA256, OBSERVED_FAILURE,
    load_authentic_prior, run,
)
from measure_rsi_self_code_method_edit import MethodEditPort


class StubModel:
    model_id = "test-transport-only"
    capabilities = RsiModelCapabilities(text_generation=True)

    def __init__(self):
        self.instructions = []

    def propose(self, instruction, *, max_output_bytes):
        self.instructions.append(instruction)
        return RsiProposalResponse(
            model_id=self.model_id, text="{}",
            input_tokens=3, output_tokens=2,
        )


def test_g3_feedback_is_separate_from_model_source_and_oracle():
    original = (
        Path(__file__).parent / "fixtures" /
        "rsi_skill_compiler_20261108.py.txt"
    ).read_text(encoding="utf-8")
    stub = StubModel()
    baseline = MethodEditPort(stub, original)
    guided = MethodEditPort(stub, original, prior_failure_summary=OBSERVED_FAILURE)
    assert baseline.prompt != guided.prompt
    assert guided.prompt.startswith(baseline.prompt)
    assert "Previous outcome observation" in guided.prompt
    assert "Previous outcome observation" not in baseline.prompt
    assert "independent behavior check" in guided.prompt
    assert "assert not outcome.is_validated()" not in guided.prompt


@pytest.mark.parametrize("setup", ("missing", "duplicate", "bad_schema"))
def test_g3_rejects_unproven_prior_source(tmp_path, setup):
    root = tmp_path / "prior"
    root.mkdir()
    if setup == "duplicate":
        for sub in ("first", "second"):
            (root / sub).mkdir()
            (root / sub / "rsi-method-edit-observed.json").write_text("{}")
    elif setup == "bad_schema":
        (root / "rsi-method-edit-observed.json").write_text(
            '{"schema":"NOT_REAL_MODEL_EVIDENCE"}',
        )
    with pytest.raises(ValueError):
        load_authentic_prior(root)


def test_matched_one_call_per_arm_and_no_fake_gain(monkeypatch, tmp_path):
    import measure_rsi_self_code_g3_method_feedback as g3

    frozen = b"frozen-model-parent"
    prior = b"failed-model-parent"
    monkeypatch.setattr(g3, "_pin_source", lambda: frozen)
    monkeypatch.setattr(g3, "load_authentic_prior", lambda _: (
        prior, {"authentic_run_id": 42552809929},
    ))
    calls = []

    def measured(_model_file, image, folder, *, parent_source, prior_failure_summary):
        assert image == IMAGE
        calls.append((folder.name, parent_source, prior_failure_summary))
        return {
            "status": "REAL_MODEL_MEASURED",
            "model_calls_observed": 1,
            "model_sha256": LARGER_MODEL_SHA256,
            "evaluator_digest": GRADE_SHA,
            "parent_source_sha256": sha256(parent_source).hexdigest(),
            "candidate_sha256": sha256(parent_source).hexdigest(),
            "source_changed": False,
            "independently_passing_changed_source": False,
        }

    monkeypatch.setattr(g3, "method_run", measured)
    result = run(
        previous_root=tmp_path / "not-read",
        model_file=tmp_path / "not-loaded",
        image=IMAGE,
        archive_root=tmp_path / "output",
    )
    assert [(a, b) for a, b, _ in calls] == [
        ("fresh_original", frozen), ("inherited_failed_patch", prior),
    ]
    assert calls[0][2] == ""
    assert "Previous model-authored" in calls[1][2]
    assert result["model_calls_observed"] == 2
    assert result["independently_passing_arms"] == 0
    assert result["matched_comparison"]["net_inherited_minus_fresh"] == 0
    assert result["rsi"] == "UNPROVEN"
