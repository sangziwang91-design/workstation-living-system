"""G1 hosted Windows smoke: architecture tests, not longitudinal autonomy."""
from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/wls-hosted-life-loop.yml"


def test_windows_runner_executes_real_canonical_wls_on_two_python_versions() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = data["jobs"]["hosted-windows-canonical-life"]
    assert job["runs-on"] == "windows-latest"
    assert job["timeout-minutes"] <= 30
    assert set(job["strategy"]["matrix"]["python-version"]) == {"3.11", "3.13"}
    assert data["permissions"] == {"contents": "read"}

    steps = job["steps"]
    assert all(step.get("shell", "pwsh") == "pwsh" for step in steps if "run" in step)
    commands = "\n".join(str(step.get("run", "")) for step in steps)
    assert "python source/scripts/verify_hosted_living_loop.py" in commands
    assert "python -m pytest -q source/tests/test_hosted_living_loop.py" in commands
    assert "wls-windows-hosted-life-receipt.json" in commands
    assert "home_persisted_across_runs" in commands
    assert "autonomous_skill_gain_proven" in commands


def test_windows_runner_has_no_owner_data_or_write_token() -> None:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = data["jobs"]["hosted-windows-canonical-life"]
    assert job["env"]["GITHUB_TOKEN"] == ""
    assert job["env"]["OPENAI_API_KEY"] == ""
    assert job["env"]["ANTHROPIC_API_KEY"] == ""
    assert job.get("permissions", {"contents": "read"}).get("contents") == "read"
    for step in job["steps"]:
        if "uses" in step and step["uses"].startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
        assert "owner_context.json" not in str(step)
        assert "secrets." not in str(step)


def test_windows_evidence_is_only_ephemeral_process_continuity() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "home_persisted_across_runs\u0022] is False" in text
    assert "model_calls\u0022] == 0" in text
    assert "autonomous_skill_gain_proven\u0022] is False" in text
    assert "github-hosted" not in text.lower() or "windows" in text.lower()
