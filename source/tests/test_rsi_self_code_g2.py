"""Deterministic contracts for G2 source transport; not fake-model intelligence evidence."""
from __future__ import annotations

from pathlib import Path
import json
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from measure_rsi_self_code_g2 import (
    ARMS, G1_SOURCE_SHA256, OBJECTIVE, PINNED_C1_IMAGE,
    TransparentFormatPort, verify_parent,
)
from measure_rsi_self_code_model import TARGET, MeteredPort
from wls.rsi_model_port import RsiModelCapabilities, RsiProposalResponse


class DummyPort:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls = 0

    @property
    def model_id(self) -> str:
        return "pinned-dummy-only"

    @property
    def capabilities(self) -> RsiModelCapabilities:
        return RsiModelCapabilities(text_generation=True)

    def propose(self, instruction: str, *, max_output_bytes: int) -> RsiProposalResponse:
        self.calls += 1
        return RsiProposalResponse(
            model_id=self.model_id, text=self.reply,
            input_tokens=321, output_tokens=42,
        )


def test_both_arms_share_identical_goal_and_one_model_budget():
    assert ARMS == ("fresh_restart", "inherited_g1")
    assert "is_validated" in OBJECTIVE
    assert "complete modified allowed Python file" in OBJECTIVE
    assert PINNED_C1_IMAGE.startswith("python@sha256:")


def test_only_complete_fenced_json_is_unwrapped_without_changing_source():
    source = "class SkillCompiler:\n    def test(self): return 1\n"
    fence = chr(96) * 3
    raw = fence + "json\n" + json.dumps({"files": {TARGET: source}}) + "\n" + fence
    mock = DummyPort(raw)
    metered = MeteredPort(mock)
    transport = TransparentFormatPort(metered)
    response = transport.propose("test-only", max_output_bytes=32768)
    assert json.loads(response.text)["files"][TARGET] == source
    assert transport.recovered == [True]
    assert mock.calls == 1
    assert metered.receipts[0]["raw_model_reply"] == raw
    assert metered.receipts[0]["input_tokens"] == 321


@pytest.mark.parametrize("raw_body", [
    "{\"files\": }",
    "{\"files\": {\"elsewhere.py\": \"x\"}}",
    "{\"files\": {\"source/src/wls/skill_compiler.py\": 7}}",
    "{\"files\": {\"source/src/wls/skill_compiler.py\": \"x\", "
    "\"source/src/wls/skill_compiler.py\": \"y\"}}",
])
def test_invalid_model_envelopes_are_not_repaired(raw_body):
    fence = chr(96) * 3
    raw = fence + "json\n" + raw_body + "\n" + fence
    transport = TransparentFormatPort(MeteredPort(DummyPort(raw)))
    response = transport.propose("test-only", max_output_bytes=32768)
    assert response.text == raw
    assert transport.recovered == [False]


def test_nonmatching_historical_parent_fails_closed(tmp_path):
    source = tmp_path / "g1.py"
    report = tmp_path / "receipt.json"
    source.write_text("this was never produced by Qwen\n", encoding="utf-8")
    report.write_text(json.dumps({
        "authentic_model_run_id": 42486361106,
        "arms": {"guided": {"candidate_source_sha256": G1_SOURCE_SHA256}},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="not pinned"):
        verify_parent(source, report, b"baseline")


def test_historical_symlink_is_not_admitted(tmp_path):
    target = tmp_path / "real"
    target.write_text("x", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation unavailable")
    with pytest.raises(ValueError, match="symlinks"):
        verify_parent(link, target, b"baseline")
