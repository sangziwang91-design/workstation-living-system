from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "source" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from run_architecture_validation import run_validation  # noqa: E402


def test_architecture_validation_runner_selects_p43() -> None:
    result = run_validation({"P43"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P43"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p44() -> None:
    result = run_validation({"P44"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P44"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p45() -> None:
    result = run_validation({"P45"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P45"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p46() -> None:
    result = run_validation({"P46"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P46"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p47() -> None:
    result = run_validation({"P47"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P47"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"
