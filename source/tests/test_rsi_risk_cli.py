"""GitHub-hosted, no-network proof of the WLS-native RSI risk-policy command."""

from __future__ import annotations

import json

import pytest
from wls.cli import main
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.rsi_artifact_gate import RsiArtifactGate
from wls.rsi_model_port import RsiModelCapabilities, RsiProposalResponse
from wls.task_admission import RsiRiskAdmissionEvaluator


class LocalModelFixture:
    model_id = "fixture"
    capabilities = RsiModelCapabilities()
    calls = 0

    def propose(self, instruction: str, *, max_output_bytes: int) -> RsiProposalResponse:
        self.calls += 1
        assert "Parent source files" in instruction
        assert "independently measured" in instruction
        return RsiProposalResponse(
            model_id=self.model_id,
            text=json.dumps({"files": {"agent/strategy.json": json.dumps({
                "risk_terms": {
                    "REVERSIBLE_WRITE": ["修改", "提交修复", "运行本地", "回滚提交"],
                    "HIGH": ["密钥", "批量发送外部"],
                    "IRREVERSIBLE": ["发布到公开", "删除生产"],
                }
            })}}),
        )


@pytest.fixture
def gate(tmp_path):
    db = Database(tmp_path / "wls.sqlite")
    ledger = EvidenceLedger(db, tmp_path / "ledger.key")
    archive = RsiArtifactGate(
        tmp_path / "artifacts", ledger,
        allowed_files=frozenset({"agent/strategy.json"}),
    )
    yield archive
    db.close_all()


def admit(gate: RsiArtifactGate, name: str, source: str) -> None:
    gate.register(
        artifact_id=name, parent_id=None, generation=0, branch=0,
        files={"agent/strategy.json": source.encode("utf-8")},
        policy_digest="c" * 64,
        evaluator_digest=RsiRiskAdmissionEvaluator.digest(),
    )


def test_fixed_risk_evaluator_proves_data_only_improvement(gate):
    admit(gate, "seed", '{"risk_terms":{}}')
    admit(gate, "candidate", json.dumps({
        "risk_terms": {
            "REVERSIBLE_WRITE": ["修改", "提交修复", "运行本地", "回滚提交"],
            "HIGH": ["密钥", "批量发送外部"],
            "IRREVERSIBLE": ["发布到公开", "删除生产"],
        }
    }))
    baseline = RsiRiskAdmissionEvaluator.evaluate("seed", gate)
    improved = RsiRiskAdmissionEvaluator.evaluate("candidate", gate)
    assert improved.primary > baseline.primary
    assert improved.gates["invalid_strategy"] == 0.0
    assert improved.primary <= 1.0
    assert baseline.evaluator_digest == improved.evaluator_digest


@pytest.mark.parametrize("bad", [
    '{"risk_terms":{"HIGH":["x"]}}',
    '{"risk_terms":{"HIGH":["secret"],"READ":["safe"]}}',
    '{"risk_terms":{"HIGH":["secret"],"IRREVERSIBLE":"bad"}}',
    '{"risk_terms":{"HIGH":["secret"]},"verdict":"KEEP"}',
    '{"risk_terms":{"HIGH":["secret"]},"test_score": NaN}',
    "not json",
])
def test_risk_evaluator_rejects_insecure_or_malformed_strategy(gate, bad):
    admit(gate, "bad", bad)
    score = RsiRiskAdmissionEvaluator.evaluate("bad", gate)
    assert score.gates["invalid_strategy"] == 1.0


def test_cli_status_is_read_only_and_no_inference(monkeypatch, tmp_path, capsys):
    cfg = tmp_path / "config.json"
    result = main(["--config", str(cfg), "rsi-risk", "--run-id", "trial", "--mode", "status"])
    assert result == 0
    data = json.loads(capsys.readouterr().out)
    assert data["status"] == "NOT_STARTED"


def test_cli_run_needs_explicit_model_authorization(tmp_path, capsys):
    cfg = tmp_path / "config.json"
    result = main([
        "--config", str(cfg), "rsi-risk", "--run-id", "trial",
        "--mode", "run", "--model-id", "fixture", "--base-url", "https://fake.test",
    ])
    assert result != 0


def test_cli_real_control_flow_uses_wls_sqlite_and_recovers_status(
    monkeypatch, tmp_path, capsys,
):
    fixture = LocalModelFixture()
    monkeypatch.setattr(
        "wls.rsi_model_port.OpenAICompatibleProposalPort",
        lambda **kwargs: fixture,
    )
    cfg = tmp_path / "config.json"
    args = [
        "--config", str(cfg), "rsi-risk", "--run-id", "ci-live-loop",
        "--mode", "run", "--model-id", fixture.model_id,
        "--base-url", "https://fake.test", "--confirm-model-usage",
        "--generations", "2", "--branches", "1",
    ]
    assert main(args) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "COMPLETE"
    assert result["generation"] == 2
    assert result["score"] > 0.5
    assert result["candidates"] == 3
    assert fixture.calls == 2
    assert main([
        "--config", str(cfg), "rsi-risk", "--run-id", "ci-live-loop",
    ]) == 0
    reopened = json.loads(capsys.readouterr().out)
    assert reopened["status"] == "COMPLETE"
    assert reopened["score"] == result["score"]
    # An independent DB reopen shows the receipt survived the CLI process.
    from wls.config import load_config

    real = load_config(cfg)
    db = Database(real.db_path)
    try:
        ledger = EvidenceLedger(db, real.secret_path)
        assert ledger.verify()[0]
        assert db.get_runtime("rsi_pilot:ci-live-loop")["generation"] == 2
    finally:
        db.close_all()
