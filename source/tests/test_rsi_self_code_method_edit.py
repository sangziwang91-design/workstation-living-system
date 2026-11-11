"""Static safety checks of bounded model-supplied WLS method edits, not model IQ."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from measure_rsi_self_code_method_edit import (
    _method_span, _apply_method_edit, _prompt, _model_spec, MethodEditPort,
    QWEN_7B_SHA256,
)
from measure_rsi_self_code_model import MeteredPort, TARGET
from wls.rsi_model_port import ModelProtocolError, RsiModelCapabilities, RsiProposalResponse


def _fixture() -> str:
    return (Path(__file__).resolve().parent / "fixtures"
            / "rsi_skill_compiler_20261108.py.txt").read_text(encoding="utf-8")


def _valid_edit() -> dict[str, str]:
    return {
        "is_validated": (
            "    def is_validated(self) -> bool:\n"
            "        return bool(self.test_manifest) and "
            "len(self.acceptance_evidence) == len(self.test_manifest)\n"
        ),
        "validate": (
            "    def validate(self, compiled, test_results):\n"
            "        if set(test_results) != set(compiled.test_manifest) or "
            "any(type(v) is not bool for v in test_results.values()):\n"
            "            raise ValueError('incomplete or invalid outcomes')\n"
            "        evidence = [digest_json({'test': key, 'passed': value}) "
            "for key, value in test_results.items() if value]\n"
            "        return CompiledSkill(package=compiled.package, "
            "body_checksum=compiled.body_checksum, "
            "test_manifest=compiled.test_manifest, acceptance_evidence=evidence)\n"
        ),
    }


def test_edit_uses_only_the_two_actual_frozen_method_spans():
    source = _fixture()
    for name in ("is_validated", "validate"):
        begin, end = _method_span(source, name)
        assert source[begin:end].startswith("    def " + name + "(")
    prompt = _prompt(source)
    assert "is_validated" in prompt and "validate" in prompt
    assert "independent tests" in prompt
    changed = _apply_method_edit(source, json.dumps(_valid_edit()))
    assert changed != source
    assert "class SkillCompiler:" in changed
    ast.parse(changed)


@pytest.mark.parametrize("payload", [
    {},
    {"is_validated": "hello", "validate": "goodbye"},
    {"is_validated": "    def is_validated(self):\n        pass", "validate": 42},
    {"is_validated": "    def wrong(self):\n        pass", "validate": "    def validate(self):\n        pass"},
    {"is_validated": "    def is_validated(self):\n        pass\n    def injected(self):\n        pass",
     "validate": "    def validate(self):\n        pass"},
    {"is_validated": "    def is_validated(self):\n        return 1",
     "validate": "    def validate(self):\n        return 1",
     "unauthorized": "x"},
])
def test_malformed_or_overbroad_model_edits_are_rejected(payload):
    with pytest.raises((ValueError, ModelProtocolError)):
        _apply_method_edit(_fixture(), json.dumps(payload))


def test_duplicate_json_keys_do_not_get_normalized_into_valid_patch():
    raw = ('{"is_validated":"x","is_validated":"y",'
           '"validate":"z"}')
    with pytest.raises(ModelProtocolError):
        _apply_method_edit(_fixture(), raw)


class FakeRawMethodModel:
    def __init__(self, answer: str):
        self.answer = answer
        self.calls = 0

    @property
    def model_id(self):
        return "offline-fake-transport-only"

    @property
    def capabilities(self):
        return RsiModelCapabilities(text_generation=True)

    def propose(self, instruction: str, *, max_output_bytes: int):
        self.calls += 1
        assert "Original CompiledSkill fields:" in instruction
        return RsiProposalResponse(
            model_id=self.model_id, text=self.answer,
            input_tokens=70, output_tokens=58,
        )


def test_original_raw_model_receipt_is_preserved_while_reconstructing_source():
    text = json.dumps(_valid_edit())
    stub = FakeRawMethodModel(text)
    meter = MeteredPort(stub)
    port = MethodEditPort(meter, _fixture())
    built = port.propose(
        "WLS candidate-only goal. Parent source files (JSON data): x; "
        "source/src/wls/skill_compiler.py",
        max_output_bytes=32768,
    )
    fields = json.loads(built.text)["files"]
    assert set(fields) == {TARGET}
    assert fields[TARGET] != _fixture()
    assert port.translation["changed"] is True
    assert meter.receipts[0]["raw_model_reply"] == text
    assert stub.calls == 1


def test_non_json_answer_keeps_raw_receipt_but_never_becomes_candidate():
    stub = FakeRawMethodModel("I will fix this next.")
    meter = MeteredPort(stub)
    port = MethodEditPort(meter, _fixture())
    with pytest.raises(ModelProtocolError):
        port.propose(
            "WLS candidate-only Parent source files: " + TARGET,
            max_output_bytes=32768,
        )
    assert port.translation["status"] == "REJECTED_LOCAL_EDIT"
    assert meter.receipts[0]["raw_model_reply"] == stub.answer


def test_larger_offline_model_is_exactly_sha_pinned_and_default_preserved():
    small = _model_spec("qwen1.5b")
    large = _model_spec("qwen7b")
    assert small[0] == "Qwen2.5-Coder-1.5B-Instruct-Q4_K_M"
    assert large[0] == "Qwen2.5-Coder-7B-Instruct-Q4_K_M"
    assert large[2] == QWEN_7B_SHA256
    assert len(QWEN_7B_SHA256) == 64
    assert "huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF" in large[1]
    assert large[3] >= 4_000_000_000 and large[4] <= 5_000_000_000
    assert large[0] != small[0] and large[2] != small[2]
    with pytest.raises(ValueError, match="verified pinned"):
        _model_spec("unverified")


def test_mechanism_guidance_changes_model_prompt_not_source_or_scorer():
    original = _fixture()
    stub = FakeRawMethodModel(json.dumps(_valid_edit()))
    baseline = MethodEditPort(MeteredPort(stub), original)
    guided = MethodEditPort(
        MeteredPort(stub), original,
        prior_failure_summary="Original failure is reproducible.",
        mechanism_guidance=(
            "A digest string is not a boolean; inspect the frozen manifest "
            "and check every required successful digest."
        ),
    )
    assert baseline.source == guided.source == original
    assert baseline.prompt != guided.prompt
    assert guided.prompt.startswith("Public code-semantics constraints")
    assert "every required successful digest" in guided.prompt
    assert "Original failure is reproducible." in guided.prompt
    assert "_PROBE_BEHAVIOR" not in guided.prompt


def test_mechanism_guidance_fails_on_excessive_length():
    stub = FakeRawMethodModel(json.dumps(_valid_edit()))
    with pytest.raises(ValueError, match="mechanism guidance"):
        MethodEditPort(
            MeteredPort(stub), _fixture(), mechanism_guidance="x" * 2201,
        )
