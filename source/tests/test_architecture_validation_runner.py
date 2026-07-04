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


def test_architecture_validation_runner_selects_p48() -> None:
    result = run_validation({"P48"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P48"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p49() -> None:
    result = run_validation({"P49"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P49"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p50() -> None:
    result = run_validation({"P50"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P50"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p51() -> None:
    result = run_validation({"P51"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P51"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p52() -> None:
    result = run_validation({"P52"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P52"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p53() -> None:
    result = run_validation({"P53"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P53"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p54() -> None:
    result = run_validation({"P54"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P54"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p55() -> None:
    result = run_validation({"P55"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P55"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p56() -> None:
    result = run_validation({"P56"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P56"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p57() -> None:
    result = run_validation({"P57"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P57"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p58() -> None:
    result = run_validation({"P58"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P58"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p59() -> None:
    result = run_validation({"P59"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P59"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p60() -> None:
    result = run_validation({"P60"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P60"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p61() -> None:
    result = run_validation({"P61"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P61"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p62() -> None:
    result = run_validation({"P62"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P62"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"


def test_architecture_validation_runner_selects_p63() -> None:
    result = run_validation({"P63"})

    assert result["task_id"] == "WLS-LIVING-AGENT-OS-CAPABILITIES-001"
    assert [item["pass_id"] for item in result["results"]] == ["P63"]
    assert result["results"][0]["verdict"] == "ADMIT_SHADOW_ONLY"
