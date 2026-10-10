"""Negative controls for separately observed canonical Windows actions."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "source/scripts/verify_g1_windows_task_actions.py"
WORKFLOW = ROOT / ".github/workflows/wls-g1-windows-observed-actions.yml"


@pytest.fixture
def probe():
    spec = importlib.util.spec_from_file_location("wls_g1_task_probe", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    sys.path.insert(0, str(SCRIPT.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPT.parent))
    return module


def fake_action(home: Path, *, tool: str = "read_file", accepted: bool = True,
                marker: str = "independent-marker", success: bool = True):
    (home / "state").mkdir(parents=True)
    conn = sqlite3.connect(home / "state/wls.db")
    conn.execute("CREATE TABLE actions(action_id TEXT,tool TEXT,status TEXT,result_json TEXT)")
    conn.execute(
        "INSERT INTO actions VALUES(?,?,?,?)",
        (
            "action_external-1", tool, "SUCCEEDED",
            json.dumps({
                "evaluation": {"accepted": accepted},
                "result": {"success": success, "output": {"text": marker}},
            }),
        ),
    )
    conn.commit()
    conn.close()
    source = home / "source.txt"
    source.write_text("independent-marker", encoding="utf-8")
    return {"outcomes": [{"status": "SUCCEEDED", "success": True}],
            "action_id": "action_external-1"}, source


def test_valid_record_is_not_in_itself_a_live_action_proof(probe, tmp_path: Path):
    home = tmp_path / "home"
    mission, source = fake_action(home)
    receipt = probe.examine_action(
        home, mission, expected_tool="read_file",
        expected_marker="independent-marker", expected_source=source,
    )
    assert receipt["real_tool_executed"] is True
    # This row is deliberately fabricated for negative-control unit tests.
    # Only a real Windows run of execute() exercises a real WLS tool.
    assert receipt["tool"] == "read_file"


@pytest.mark.parametrize("defect", [
    "no_outcomes", "no_success", "replayed", "missing_id", "wrong_tool",
    "failed_evaluator", "bad_source", "wrong_marker", "failed_tool",
])
def test_invalid_external_evidence_is_rejected(probe, tmp_path: Path, defect: str):
    home = tmp_path / defect
    mission, source = fake_action(home, tool="list_directory" if defect == "wrong_tool" else "read_file",
                                  accepted=defect != "failed_evaluator",
                                  marker="other" if defect == "wrong_marker" else "independent-marker",
                                  success=defect != "failed_tool")
    if defect == "no_outcomes":
        mission["outcomes"] = []
    elif defect == "no_success":
        mission["outcomes"][0]["success"] = False
    elif defect == "replayed":
        mission["outcomes"][0]["reused"] = True
    elif defect == "missing_id":
        mission["action_id"] = None
    elif defect == "bad_source":
        source.write_text("no required text", encoding="utf-8")
    with pytest.raises(ValueError):
        probe.examine_action(home, mission, expected_tool="read_file",
                             expected_marker="independent-marker", expected_source=source)


def test_hosted_windows_workflow_is_read_only_and_independent() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    assert data["permissions"] == {"contents": "read"}
    job = data["jobs"]["observed-windows-actions"]
    assert job["runs-on"] == "windows-latest"
    assert job["permissions"] == {"contents": "read"}
    all_steps = "\n".join(str(step.get("run", "")) for step in job["steps"])
    assert "verify_g1_windows_task_actions.py" in all_steps
    assert "test_g1_windows_task_actions.py" in all_steps
    assert "OPENAI_API_KEY" not in all_steps
    assert "model_calls" not in all_steps
    for step in job["steps"]:
        if "uses" in step and str(step["uses"]).startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False


def test_declared_claim_ceiling_is_not_a_skill_proof() -> None:
    s = SCRIPT.read_text(encoding="utf-8")
    assert '"autonomous_goal_count": 0' in s
    assert '"heldout_skill_gain_proven": False' in s
    assert '"real_owner_task_count": 0' in s
