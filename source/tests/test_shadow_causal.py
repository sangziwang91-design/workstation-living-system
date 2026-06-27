from __future__ import annotations

import json

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.shadow_causal import ShadowCausalAnalyzer
from wls.shadow_causal_plugin import _resolve_without_affecting_canonical


def test_shadow_schema_and_default_bounds(tmp_path):
    db = Database(tmp_path / "state" / "wls.db")
    ledger = EvidenceLedger(db, tmp_path / "secrets" / "evidence.key")
    analyzer = ShadowCausalAnalyzer(db, ledger)
    assert analyzer.summary()["shadow_causal_runs"] == 0
    row = db.query_one(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='shadow_causal_candidates'"
    )
    schema = str(row["sql"])
    assert "SHADOW_READ_ONLY" in schema
    assert "'VERIFIED'" not in schema


def test_analysis_dag_is_acyclic():
    dag = ShadowCausalAnalyzer._analysis_dag(
        ["PAST", "PRESENT", "FUTURE"], has_actions=True
    )
    ShadowCausalAnalyzer._assert_acyclic(dag)


def test_missing_outcome_evidence_does_not_resolve():
    class FakeDB:
        def query_one(self, _sql, _params):
            return {
                "proposal_json": json.dumps(
                    {"predictions": [{"action_id": "action_1"}]}
                ),
                "frozen_evidence_seq": 4,
                "status": "FROZEN",
            }

    class FakeAnalyzer:
        def __init__(self):
            self.db = FakeDB()
            self.resolve_calls = 0
            self.errors = []

        def _first_action_outcome_seq(self, _ids, _seq):
            return None

        def resolve(self, **_kwargs):
            self.resolve_calls += 1

        def safe_record_error(self, **kwargs):
            self.errors.append(kwargs)

    analyzer = FakeAnalyzer()
    _resolve_without_affecting_canonical(
        analyzer,
        plan_id="plan_1",
        outcomes=[{"action_id": "action_1", "success": True}],
        phase="TEST",
    )
    assert analyzer.resolve_calls == 0
    assert analyzer.errors == []
