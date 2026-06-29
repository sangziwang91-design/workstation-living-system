from __future__ import annotations

from pathlib import Path

import pytest

from wls.examiner import AnchorBank, Constitution, ExaminerStore, ExaminerSystem, ExaminerVersion, StandaloneDatabase, current_implementation_digest
from wls.examiner.fixtures import RULE_SEQUENCE, hidden_holdout_cases, make_defect
from wls.examiner.system import IdFactory


ROOT = Path(__file__).resolve().parents[2]


def setup(tmp_path: Path):
    constitution = Constitution.load(str(ROOT / ".evolution/examiner/constitution.json"))
    holdout = AnchorBank(hidden_holdout_cases(), "holdout", "test")
    incumbent = ExaminerVersion(
        version_id="v0",
        epoch_id="EPOCH-0000",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=(),
        created_at="2026-06-28T00:00:00+00:00",
    )
    store = ExaminerStore(StandaloneDatabase(tmp_path / "promotion.db"))
    system = ExaminerSystem(constitution, store, incumbent, id_factory=IdFactory("prom", deterministic=True))
    return system, holdout


def test_partial_challenger_is_not_promoted_while_critical_false_passes_remain(tmp_path: Path):
    system, holdout = setup(tmp_path)
    challenger = system.challenger(
        version_id="v1-partial",
        epoch_id="EPOCH-0001",
        add_rules=(RULE_SEQUENCE[0],),
    )
    decision = system.compare_versions(system.active_version, challenger, holdout)
    assert not decision.eligible
    assert "CRITICAL_FALSE_PASS" in decision.reason_codes
    assert decision.wins == 6
    assert decision.losses == 0


def test_final_challenger_is_statistically_eligible_but_still_owner_gated(tmp_path: Path):
    system, holdout = setup(tmp_path)
    challenger = system.challenger(
        version_id="v10",
        epoch_id="EPOCH-0010",
        add_rules=RULE_SEQUENCE,
    )
    decision = system.compare_versions(system.active_version, challenger, holdout)
    assert decision.eligible
    assert decision.wins == 60
    assert decision.losses == 0
    assert decision.one_sided_p <= 0.05
    promotion_id = system.request_promotion(decision, promotion_id="promotion-owner-gate")
    with pytest.raises(PermissionError, match="owner actor"):
        system.apply_promotion(
            challenger,
            decision,
            owner_actor="",
            owner_approval_ref="",
            promotion_id=promotion_id,
            holdout=holdout,
        )


def test_selective_erasure_preserves_raw_verdict_rows(tmp_path: Path):
    system, holdout = setup(tmp_path)
    defect = make_defect(RULE_SEQUENCE[0], 0).candidate
    system.evaluate(defect, record=True)
    before = system.store.verdict_rows()
    assert len(before) == 1 and before[0]["stale"] == 0

    challenger = system.challenger(
        version_id="v10",
        epoch_id="EPOCH-0010",
        add_rules=RULE_SEQUENCE,
    )
    decision = system.compare_versions(system.active_version, challenger, holdout)
    promotion_id = system.request_promotion(decision, promotion_id="promotion-1")
    stale = system.apply_promotion(
        challenger,
        decision,
        owner_actor="owner",
        owner_approval_ref="approval-1",
        promotion_id=promotion_id,
        holdout=holdout,
    )
    assert stale == 1
    after = system.store.verdict_rows()
    assert len(after) == 1
    assert after[0]["stale"] == 1
    assert after[0]["result_json"] == before[0]["result_json"]


def test_anchor_manifest_swap_is_rejected(tmp_path: Path):
    system, holdout = setup(tmp_path)
    challenger = system.challenger(
        version_id="v1",
        epoch_id="EPOCH-0001",
        add_rules=(RULE_SEQUENCE[0],),
    )
    challenger = ExaminerVersion(
        **{**challenger.to_dict(), "anchor_manifest_digest": "f" * 64}
    )
    with pytest.raises(RuntimeError, match="challenger anchor manifest"):
        system.compare_versions(system.active_version, challenger, holdout)


class FailingLedger:
    def append(self, event_type, payload, connection=None):
        if event_type == "examiner_epoch_opened" and payload.get("epoch_id") == "EPOCH-ROLLBACK":
            raise RuntimeError("injected ledger failure")
        return "evidence-ok"


def test_promotion_is_atomic_when_ledger_write_fails(tmp_path: Path):
    constitution = Constitution.load(str(ROOT / ".evolution/examiner/constitution.json"))
    holdout = AnchorBank(hidden_holdout_cases(), "holdout", "test")
    incumbent = ExaminerVersion(
        version_id="atomic-v0",
        epoch_id="EPOCH-ATOMIC-0",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=(),
        created_at="2026-06-28T00:00:00+00:00",
    )
    store = ExaminerStore(
        StandaloneDatabase(tmp_path / "atomic.db"),
        ledger=FailingLedger(),
    )
    system = ExaminerSystem(
        constitution,
        store,
        incumbent,
        id_factory=IdFactory("atomic", deterministic=True),
    )
    # Record one incumbent verdict so stale rollback is observable.
    system.evaluate(make_defect(RULE_SEQUENCE[0], 9).candidate)
    challenger = system.challenger(
        version_id="atomic-v10",
        epoch_id="EPOCH-ROLLBACK",
        add_rules=RULE_SEQUENCE,
    )
    decision = system.compare_versions(system.active_version, challenger, holdout)
    promotion_id = system.request_promotion(decision, promotion_id="atomic-promotion")
    with pytest.raises(RuntimeError, match="injected ledger failure"):
        system.apply_promotion(
            challenger,
            decision,
            owner_actor="owner",
            owner_approval_ref="approval",
            promotion_id=promotion_id,
            holdout=holdout,
        )

    active = store.active_epoch()
    assert active is not None
    assert active["epoch_id"] == incumbent.epoch_id
    assert active["status"] == "FROZEN"
    assert store.db.query_one(
        "SELECT epoch_id FROM examiner_epochs WHERE epoch_id=?", (challenger.epoch_id,)
    ) is None
    assert store.verdict_rows()[0]["stale"] == 0
    promotion = store.db.query_one(
        "SELECT status FROM examiner_promotions WHERE promotion_id=?", (promotion_id,)
    )
    assert promotion is not None and promotion["status"] == "AWAITING_OWNER"
