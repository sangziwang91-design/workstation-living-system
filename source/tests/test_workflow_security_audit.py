"""Twenty separate workflow trust-audit rounds and negative controls.

These are configuration reviews, not twenty measured RSI generations.
"""
from __future__ import annotations

from pathlib import Path
import re
import os
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
FOLDER = ROOT / ".github" / "workflows"
WORKFLOWS = (
    "apply-evolution-target-001.yml", "audit-public-history.yml",
    "build-evolution-target-002.yml", "ci.yml", "evolution-chain.yml",
    "hosted-rsi-autorepair.yml", "hosted-rsi-local-model.yml",
    "manual-ubuntu-cross-platform.yml", "mypy-baseline.yml",
    "packaging-layout.yml", "patch-mission-resume-regression.yml",
    "patch-mission-status.yml", "rsi-c1-isolation.yml", "rsi-pilot.yml",
    "rsi-scoring-change-boundary.yml", "verify-evolution-target-003.yml",
    "wls-a5-gap-scan.yml", "wls-c2-known-regression-replay.yml",
    "wls-c2-taskpack.yml", "wls-hosted-life-loop.yml",
)
PINNED = {
    "actions/checkout": {"34e114876b0b11c390a56381ad16ebd13914f8d5",
                         "3d3c42e5aac5ba805825da76410c181273ba90b1"},
    "actions/setup-python": {"5fda3b95a4ea91299a34e894583c3862153e4b97",
                             "a26af69be951a213d495a4c3e4e4022e16d87065"},
    "actions/upload-artifact": {"ea165f8d65b6e75b540449e92b4886f43607fa02"},
    "actions/download-artifact": {"d3f86a106a0bac45b974a628896c90dbdf5c8093"},
}


def workflow(name: str) -> tuple[dict, str]:
    raw = (FOLDER / name).read_text(encoding="utf-8")
    doc = yaml.safe_load(raw)
    assert isinstance(doc, dict)
    return doc, raw


@pytest.mark.parametrize(
    "name", WORKFLOWS,
    ids=[f"round-{i:02d}-{name}" for i, name in enumerate(WORKFLOWS, 1)],
)
def test_twenty_workflow_trust_checks(name: str) -> None:
    doc, raw = workflow(name)
    assert doc.get("permissions", {}).get("contents") == "read"
    assert isinstance(doc.get("jobs"), dict) and doc["jobs"]
    events = doc.get("on", doc.get(True, {}))
    assert isinstance(events, dict) and events
    assert "pull_request_target" not in events
    for job in doc["jobs"].values():
        assert "timeout-minutes" in job
    uses = re.findall(r"^\s*-?\s*uses:\s*([^\s#]+)", raw, re.MULTILINE)
    for spec in uses:
        action, sep, sha = spec.partition("@")
        assert sep and sha in PINNED.get(action, set()), (
            f"{name}: unknown, movable or mismatched upstream action {spec}"
        )


def test_inventory_follows_all_tracked_workflows() -> None:
    assert set(WORKFLOWS) == {p.name for p in FOLDER.glob("*.yml")}


def test_scorer_pr_is_readonly_and_checks_trusted_main() -> None:
    doc, raw = workflow("rsi-scoring-change-boundary.yml")
    events = doc.get("on", doc.get(True))
    assert isinstance(events, dict)
    assert "pull_request" in events and "pull_request_target" not in events
    assert doc["permissions"] == {"contents": "read", "pull-requests": "read"}
    assert "github.event.repository.default_branch" in raw
    assert "persist-credentials: false" in raw
    assert "persist-credentials: true" not in raw


def test_life_schedule_not_displaced_by_pr_queues() -> None:
    doc, raw = workflow("wls-hosted-life-loop.yml")
    events = doc.get("on", doc.get(True))
    assert isinstance(events, dict)
    assert {"schedule", "workflow_dispatch", "pull_request", "push"} <= set(events)
    assert "github.event_name" in doc["concurrency"]["group"]
    assert "github.ref" in doc["concurrency"]["group"]
    assert doc["concurrency"]["cancel-in-progress"] is False
    assert doc["permissions"] == {"contents": "read"}
    assert "persist-credentials: true" not in raw


def test_a5_write_authority_never_in_first_evidence_job() -> None:
    doc, raw = workflow("wls-a5-gap-scan.yml")
    first = doc["jobs"]["daily-observed-gap"]
    second = doc["jobs"]["schedule-only-issue-intake"]
    assert first.get("permissions") is None
    assert second["permissions"]["issues"] == "write"
    assert "github.event_name == 'schedule'" in second["if"]
    assert second["needs"] == "daily-observed-gap"
    assert "GH_TOKEN" not in repr(first.get("env", {}))
    assert "ref: main" in raw


def test_model_and_repair_workers_have_no_write_authority() -> None:
    for name, candidate, writer in (
        ("hosted-rsi-autorepair.yml", "candidate", "persist-tested-candidate"),
        ("hosted-rsi-local-model.yml", "real-model-candidate",
         "persist-verified-model-fix"),
    ):
        doc, _ = workflow(name)
        assert "write" not in repr(doc["jobs"][candidate].get("permissions", {}))
        assert doc["jobs"][writer]["permissions"]["contents"] == "write"
        assert doc["jobs"][writer]["needs"] == candidate
        assert "if" in doc["jobs"][writer]


def test_write_token_python_never_imports_code_from_pr_checkout() -> None:
    """Publisher has write token and checks out code from a PR.

    Normal python stdin execution can import arbitrary code from cwd.
    Isolated Python must guard the stdlib-only inline admission scripts.
    """
    for name, job in (
        ("hosted-rsi-autorepair.yml", "persist-tested-candidate"),
        ("hosted-rsi-local-model.yml", "persist-verified-model-fix"),
    ):
        document, _ = workflow(name)
        publisher = document["jobs"][job]
        assert publisher["permissions"]["contents"] == "write"
        scripts = [
            step["run"] for step in publisher["steps"]
            if isinstance(step.get("run"), str) and "python" in step["run"]
        ]
        assert scripts, "publisher admission must be inspected"
        assert any("python -I -S - <<'PY'" in script for script in scripts)
        for script in scripts:
            assert "python - <<'PY'" not in script, (
                "write-token publisher may load a malicious PR-local module"
            )
            assert "python -I -S - <<'PY'" in script


def test_python_isolation_defeats_checkout_and_pythonpath_module_poisoning(
    tmp_path: Path,
) -> None:
    """Prove -I -S disables both cwd and attacker-set PYTHONPATH."""
    (tmp_path / "wls_untrusted_shadow_probe.py").write_text(
        "raise RuntimeError('untrusted code executed')\n", encoding="utf-8"
    )
    code = (
        "import importlib.util\n"
        "assert importlib.util.find_spec('wls_untrusted_shadow_probe') is None\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c", code],
        cwd=tmp_path, env=env, check=False, capture_output=True,
        timeout=15, text=True,
    )
    assert result.returncode == 0, result.stderr
