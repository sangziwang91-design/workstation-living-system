"""R1 replay must run real tests and refuse synthetic/moved evidence."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import make_mutation_tasks as engine  # noqa: E402
import verify_r1_mutation_replay as audit  # noqa: E402


@pytest.fixture
def observed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "worktree"
    module = root / "source/src/wls"
    module.mkdir(parents=True)
    (module / "__init__.py").write_text("", encoding="utf-8")
    (module / "probe.py").write_text(
        "def result(x: int) -> int:\n"
        "    return 1 if x >= 3 else 0\n",
        encoding="utf-8",
    )
    tests = root / "source/tests"
    tests.mkdir(parents=True)
    (tests / "test_probe.py").write_text(
        "from wls.probe import result\n"
        "def test_bound():\n"
        "    assert result(3) == 1\n",
        encoding="utf-8",
    )
    # These are synthetic local fixtures even when pytest itself runs on
    # GitHub-hosted Windows; they are NOT GitHub-attested task receipts.
    monkeypatch.setenv("GITHUB_ACTIONS", "false")
    monkeypatch.setattr(
        engine, "BASELINE_TESTS",
        {"probe.py": "source/tests/test_probe.py"},
    )
    report = engine.run(root, probes=12, minimum=1, seconds=25)
    assert report["status"] == "QUALIFIED_REAL_MUTATIONS"
    assert report["observed_task_count"] >= 1
    return root, report


def test_real_windows_style_receipt_replayed_through_three_new_pytest_processes(
    observed,
) -> None:
    root, original = observed
    data = audit.read_receipt(json.dumps(original).encode("utf-8"))
    out = audit.replay(root, data, timeout=25)
    assert out["stable_task_count"] == original["observed_task_count"]
    assert out["unstable_task_count"] == 0
    assert all(case["second_pass_test_invocations"] == 3 for case in out["cases"])
    assert out["model_calls"] == 0
    assert out["repair_success_rate"] == "UNMEASURED"
    assert (root / "source/src/wls/probe.py").read_text() == (
        "def result(x: int) -> int:\n"
        "    return 1 if x >= 3 else 0\n"
    )


@pytest.mark.parametrize("defect", [
    "fake_three_trials", "fake_task_hash", "fake_task_id", "bad_test",
    "bad_mutant_hash", "duplicate_json_key", "wrong_run_id",
])
def test_receipt_forgery_or_modified_identity_rejected(
    observed, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    root, sample = observed
    payload = copy.deepcopy(sample)
    first = payload["tasks"][0]
    if defect == "fake_three_trials":
        first["test_results"][0]["exit_code"] = 0
    elif defect == "fake_task_hash":
        first["source_sha256"] = "0" * 64
    elif defect == "fake_task_id":
        first["id"] = "forged_id"
    elif defect == "bad_test":
        first["selected_test"] = "source/tests/other.py"
    elif defect == "bad_mutant_hash":
        first["mutant_sha256"] = "f" * 64
    elif defect == "wrong_run_id":
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_RUN_ID", "333333")
        monkeypatch.setenv("GITHUB_SHA", "b" * 40)
        monkeypatch.setenv("RUNNER_OS", "Windows")
        monkeypatch.setenv("RUNNER_ENVIRONMENT", "github-hosted")
        with pytest.raises(audit.EvidenceBlocked, match="receipt source"):
            audit.read_receipt(json.dumps(payload).encode())
        return
    elif defect == "duplicate_json_key":
        with pytest.raises(audit.EvidenceBlocked, match="duplicate JSON"):
            audit.read_receipt(b'{"schema":"x","schema":"y"}')
        return
    if defect != "fake_task_hash":
        payload["task_digest"] = engine.sha(
            json.dumps(payload["tasks"], sort_keys=True).encode("utf-8")
        )
    if defect == "fake_task_hash":
        # A stale digest is rejected even before file contents are read.
        with pytest.raises(audit.EvidenceBlocked, match="digest"):
            audit.read_receipt(json.dumps(payload).encode())
    else:
        checked = audit.read_receipt(json.dumps(payload).encode())
        with pytest.raises(audit.EvidenceBlocked):
            audit.replay(root, checked, timeout=25)


def test_changed_original_source_blocks_replay(
    observed,
) -> None:
    root, receipt = observed
    checked = audit.read_receipt(json.dumps(receipt).encode())
    (root / "source/src/wls/probe.py").write_text(
        "def result(x: int) -> int:\n    return 8\n", encoding="utf-8"
    )
    with pytest.raises(audit.EvidenceBlocked, match="source changed"):
        audit.replay(root, checked, timeout=25)


def test_timeout_and_invalid_receipt_fail_closed(observed) -> None:
    root, receipt = observed
    checked = audit.read_receipt(json.dumps(receipt).encode())
    with pytest.raises(audit.EvidenceBlocked, match="timeout"):
        audit.replay(root, checked, timeout=0)
    with pytest.raises(audit.EvidenceBlocked, match="missing or oversized"):
        audit.read_receipt(b"")
    with pytest.raises(audit.EvidenceBlocked, match="observed task"):
        audit.read_receipt(json.dumps({**receipt, "tasks": []}).encode())
