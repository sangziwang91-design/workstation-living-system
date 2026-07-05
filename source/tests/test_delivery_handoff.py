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
    assert "run_life_campaign_30.ps1" in payload["owner_commands"]["R01_R05"]
    assert "-StartRound R01" in payload["owner_commands"]["R01_R05"]
    assert "-EndRound R05" in payload["owner_commands"]["R01_R05"]
    assert "--execute" in payload["owner_commands"]["R01_R40"]
    assert payload["boundaries"]["live_install_modified"] is False
    assert payload["boundaries"]["deploy_executed"] is False
