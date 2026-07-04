from __future__ import annotations

import sys


from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_ROOT = REPO_ROOT / "source" / "scripts"
SOURCE_ROOT = REPO_ROOT / "source" / "src"
for path in (str(SCRIPTS_ROOT), str(SOURCE_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

from build_capability_inventory import build_inventory  # noqa: E402
from wls.schemas import digest_json  # noqa: E402


def test_capability_inventory_binds_authorities_workers_and_validation() -> None:
    inventory = build_inventory()

    assert inventory["inventory_id"] == "WLS-CAPABILITY-INVENTORY-DL001"
    assert inventory["authority_model"]["single_subject"] == "wls.runtime.LivingSystem"
    assert inventory["authority_model"]["duplicate_authority_allowed"] is False
    assert inventory["capability_registry"]["summary"]["count"] >= 19
    assert all(
        item["declares_authority"] is False
        for item in inventory["capability_registry"]["capabilities"]
    )
    assert inventory["worker_registry"]["primary_code_worker_id"] == "chatgpt_interactive"
    assert "test_life_campaign_30.py" in inventory["validation_surfaces"]["tests"]
    assert inventory["validation_surfaces"]["base_campaign_rounds"][0] == "R01"
    assert inventory["validation_surfaces"]["base_campaign_rounds"][-1] == "R30"
    assert inventory["validation_surfaces"]["longitudinal_extension_rounds"] == [
        f"R{index:02d}" for index in range(31, 41)
    ]
    assert inventory["validation_surfaces"]["architecture_passes"][-1] == "P70"
    assert "no live deployment" in inventory["claim_ceiling"]

    expected_digest = digest_json(
        {
            "authority_model": inventory["authority_model"],
            "capability_registry": inventory["capability_registry"],
            "worker_registry": inventory["worker_registry"],
            "validation_surfaces": inventory["validation_surfaces"],
        }
    )
    assert inventory["inventory_digest"] == expected_digest
