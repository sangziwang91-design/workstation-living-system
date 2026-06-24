from __future__ import annotations

from wls.goal_ablation import CLAIM_BOUNDARY


def test_evolution_target_004_claims_remain_bounded() -> None:
    assert "does not prove" in CLAIM_BOUNDARY
    assert "production-host long-term autonomy" in CLAIM_BOUNDARY
    assert "AGI" in CLAIM_BOUNDARY
    assert "consciousness" in CLAIM_BOUNDARY
