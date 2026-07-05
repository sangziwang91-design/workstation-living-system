from __future__ import annotations

import json
import subprocess
import sys


def test_build_delivery_handoff_outputs_owner_commands() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "source/scripts/build_delivery_handoff.py",
            "--readiness-status",
            "CANDIDATE_READY",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["artifact_type"] == "WLS_DELIVERY_HANDOFF_PACKAGE"
    assert payload["readiness_summary"]["overall_status"] == "CANDIDATE_READY"
    assert payload["release_state_summary"]["overall_status"] == "CANDIDATE_READY"
    assert payload["release_state_summary"]["required_receipts"] == [
        "delivery_handoff",
        "release_state_audit",
    ]
    assert payload["delivery_gap_summary"]["overall_status"] == (
        "CANDIDATE_READY_WITH_OWNER_HOST_GATES"
    )
    assert "WLS_CODEX_FINAL_ROUTE" in payload["delivery_gap_summary"][
        "source_packages_absorbed"
    ]
    assert "real_browser_e2e" in payload["delivery_gap_summary"]["owner_host_gates"]
    assert "run_life_campaign_30.ps1" in payload["owner_commands"]["R01_R05"]
    assert "-StartRound R01" in payload["owner_commands"]["R01_R05"]
    assert "-EndRound R05" in payload["owner_commands"]["R01_R05"]
    assert "--execute" in payload["owner_commands"]["R01_R40"]
    assert payload["test_results"][0]["name"] == "architecture_validation_p81_p85"
    assert payload["boundaries"]["live_install_modified"] is False
    assert payload["boundaries"]["deploy_executed"] is False
