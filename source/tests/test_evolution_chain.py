from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load(relative_path: str) -> dict[str, Any] | None:
    full = REPO_ROOT / relative_path
    if not full.exists():
        return None
    value = json.loads(full.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_evolution_chain_validator_passes() -> None:
    validator = REPO_ROOT / "source" / "scripts" / "validate_evolution_chain.py"
    if not validator.exists():
        import pytest
        pytest.skip("validator script not found")
    result = subprocess.run(
        [sys.executable, str(validator)],
        cwd=str(REPO_ROOT),
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 and "Notion" in (result.stdout + result.stderr):
        import pytest
        pytest.skip("Notion-dependent validator requires online context")
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["success"] is True


def test_current_chain_schema_v2() -> None:
    current = _load(".evolution/CURRENT_CHAIN.json")
    assert current is not None
    assert current["schema_version"] == "2.0"
    assert current["status"] == "ACTIVE_SINGLE_TRUNK_ET004_COMPLETE"
    assert current["canonical_baseline"]["development_version"] == "0.9.0.dev2"
    assert "EVOLUTION-TARGET-004" in current["canonical_baseline"]["completed_targets"]


def test_et004_completed_target_in_chain() -> None:
    current = _load(".evolution/CURRENT_CHAIN.json")
    assert current is not None
    targets = current.get("canonical_baseline", {}).get("completed_targets", [])
    assert "EVOLUTION-TARGET-004" in targets


def test_current_target_matches_mainline_state() -> None:
    current = _load(".evolution/CURRENT_CHAIN.json")
    assert current is not None
    next_target = current.get("current_target", {})
    assert next_target.get("target_id") == "MAINLINE-TARGET-001"
    assert next_target.get("state") == "SOURCE_CONSOLIDATED_INSTALL_VERIFICATION_PENDING"


def test_chatgpt_is_only_active_code_worker() -> None:
    registry = _load(".evolution/worker_registry.json")
    if registry is None:
        import pytest
        pytest.skip("worker_registry.json not found")
    workers = registry["workers"]
    active_code_workers = [
        worker
        for worker in workers
        if worker["status"] == "ACTIVE"
        and worker["write_authority"] == "BRANCH_AND_PULL_REQUEST"
    ]
    assert [worker["worker_id"] for worker in active_code_workers] == [
        "chatgpt_interactive"
    ]


def test_et004_is_not_duplicated() -> None:
    current = _load(".evolution/CURRENT_CHAIN.json")
    assert current is not None
    targets = current.get("canonical_baseline", {}).get("completed_targets", [])
    et004_count = sum(1 for t in targets if t == "EVOLUTION-TARGET-004")
    assert et004_count == 1
    next_target = current.get("current_target", {}).get("target_id", "")
    assert next_target != "EVOLUTION-TARGET-004"
