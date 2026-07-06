from __future__ import annotations

import json
from subprocess import CompletedProcess

from source.scripts import run_delivery_self_check


def test_delivery_self_check_builder_accepts_consistent_candidate(
    monkeypatch,
) -> None:
    validation_payload = {
        "results": [
            {"pass_id": f"P{index:02d}", "verdict": "ADMIT_SHADOW_ONLY"}
            for index in range(81, 89)
        ]
    }
    handoff_payload = {
        "artifact_type": "WLS_DELIVERY_HANDOFF_PACKAGE",
        "candidate": {"commit": "abc123"},
        "delivery_gap_summary": {
            "overall_status": "CANDIDATE_READY_WITH_OWNER_HOST_GATES"
        },
    }

    def fake_run(command: list[str]) -> CompletedProcess[str]:
        if command[:2] == ["git", "branch"]:
            return CompletedProcess(command, 0, "living-agent-os-capabilities-001\n", "")
        if command[:2] == ["git", "rev-parse"]:
            return CompletedProcess(command, 0, "abc123\n", "")
        if command[:2] == ["git", "status"]:
            return CompletedProcess(command, 0, "", "")
        if any(item.endswith("run_architecture_validation.py") for item in command):
            return CompletedProcess(command, 0, json.dumps(validation_payload), "")
        if any(item.endswith("build_delivery_handoff.py") for item in command):
            return CompletedProcess(command, 0, json.dumps(handoff_payload), "")
        raise AssertionError(command)

    monkeypatch.setattr(run_delivery_self_check, "_run", fake_run)

    payload = run_delivery_self_check.build_delivery_self_check(python_exe="python")

    assert payload["status"] == "PASS"
    assert payload["candidate"]["commit"] == "abc123"
    assert payload["failure_groups"]["missing_passes"] == []
    assert payload["failure_groups"]["boundary_failures"] == []
    assert payload["repository_checks"]["worktree_clean"] is True


def test_delivery_self_check_builder_blocks_dirty_candidate(monkeypatch) -> None:
    validation_payload = {
        "results": [
            {"pass_id": f"P{index:02d}", "verdict": "ADMIT_SHADOW_ONLY"}
            for index in range(81, 88)
        ]
    }
    handoff_payload = {
        "artifact_type": "WLS_DELIVERY_HANDOFF_PACKAGE",
        "candidate": {"commit": "wrong"},
        "delivery_gap_summary": {
            "overall_status": "CANDIDATE_READY_WITH_OWNER_HOST_GATES"
        },
    }

    def fake_run(command: list[str]) -> CompletedProcess[str]:
        if command[:2] == ["git", "branch"]:
            return CompletedProcess(command, 0, "living-agent-os-capabilities-001\n", "")
        if command[:2] == ["git", "rev-parse"]:
            return CompletedProcess(command, 0, "abc123\n", "")
        if command[:2] == ["git", "status"]:
            return CompletedProcess(command, 0, " M source/file.py\n", "")
        if any(item.endswith("run_architecture_validation.py") for item in command):
            return CompletedProcess(command, 0, json.dumps(validation_payload), "")
        if any(item.endswith("build_delivery_handoff.py") for item in command):
            return CompletedProcess(command, 0, json.dumps(handoff_payload), "")
        raise AssertionError(command)

    monkeypatch.setattr(run_delivery_self_check, "_run", fake_run)

    payload = run_delivery_self_check.build_delivery_self_check(python_exe="python")

    assert payload["status"] == "FAIL"
    assert payload["failure_groups"]["missing_passes"] == ["P88"]
    assert payload["failure_groups"]["handoff_failures"] == ["handoff_commit_or_type"]
    assert payload["failure_groups"]["repository_failures"] == ["worktree_clean"]
