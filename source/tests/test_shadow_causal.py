from __future__ import annotations

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.shadow_causal import ShadowCausalAnalyzer


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
