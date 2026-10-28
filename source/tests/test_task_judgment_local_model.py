"""Frozen interface and no-label-contamination controls for actual local model probe."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location(
    "wls_local_task_model_probe", SCRIPTS / "measure_task_judgment_local_model.py",
)
assert spec and spec.loader
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

DATA = Path(__file__).resolve().parents[1] / "verification/WLS_TASK_JUDGMENT_PUBLIC_V1.json"


@pytest.mark.parametrize("reply,expected", [
    ('{"status":"READY_TO_PLAN"}', "READY_TO_PLAN"),
    ('```json\n{"status":"APPROVAL_REQUIRED"}\n```', "APPROVAL_REQUIRED"),
    ('{"status":"APPROVAL_REQUIRED","score":1}', None),
    ('{"status":"PROMOTED"}', None),
    ('MODEL_OBSERVED', None),
    ('{"status":1}', None),
    ('{"status":"READY_TO_PLAN","status":"APPROVAL_REQUIRED"}', None),
    ('', None),
])
def test_real_model_reply_is_not_grading_authority(reply: str, expected: str | None):
    assert probe.parse_reply(reply) == expected


def test_model_prompt_never_shows_expected_or_scoring_fields():
    data = probe.read_taskpack(DATA)
    selected = next(r for r in data["cases"]
                    if r["input"]["task_id"] == "JT031")
    rendered = probe.model_prompt(selected)
    assert selected["input"]["request"] in rendered
    assert '"expected_status"' not in rendered
    assert '"expected"' not in rendered
    assert '"rule_correct"' not in rendered
    assert '"model_correct"' not in rendered


def test_pin_requires_exact_file_identity(tmp_path: Path):
    path = tmp_path / "tiny.gguf"
    path.write_bytes(b"not an actual model")
    with pytest.raises(ValueError):
        probe.verify_model(path)


def test_model_sample_covers_multiple_judgment_outcomes():
    data = probe.read_taskpack(DATA)
    cases = {r["input"]["task_id"]: r for r in data["cases"]}
    assert set(probe.MODEL_SAMPLE_IDS) <= set(cases)
    labels = {cases[k]["expected"]["status"] for k in probe.MODEL_SAMPLE_IDS}
    assert len(labels) >= 5
