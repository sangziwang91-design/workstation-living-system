from __future__ import annotations

import io
import json
from dataclasses import dataclass

import pytest
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.rsi_artifact_gate import RsiArtifactGate
from wls.rsi_model_port import (
    ModelProtocolError,
    OpenAICompatibleProposalPort,
    RsiModelCandidateBuilder,
    RsiModelCapabilities,
    RsiProposalRequest,
    RsiProposalResponse,
)

SHA = "c" * 64


@dataclass
class FakeModel:
    text: str
    _id: str = "mock-model"
    calls: int = 0

    @property
    def model_id(self) -> str:
        return self._id

    @property
    def capabilities(self) -> RsiModelCapabilities:
        return RsiModelCapabilities(text_generation=True, context_tokens=4096)

    def propose(self, instruction: str, *, max_output_bytes: int) -> RsiProposalResponse:
        self.calls += 1
        assert "never executed" in instruction
        assert "agent/strategy.json" in instruction
        return RsiProposalResponse(model_id=self._id, text=self.text)


@pytest.fixture
def gateway(tmp_path):
    db = Database(tmp_path / "state.sqlite")
    ledger = EvidenceLedger(db, tmp_path / "evidence.key")
    gate = RsiArtifactGate(
        tmp_path / "artifact-store", ledger,
        allowed_files=frozenset({"agent/strategy.json", "agent/module.py"}),
    )
    gate.register(
        artifact_id="seed", parent_id=None, generation=0, branch=0,
        files={"agent/strategy.json": b'{"a":0}'},
        policy_digest=SHA, evaluator_digest=SHA,
    )
    yield gate, ledger
    db.close_all()


def request(*, model_id: str = "mock-model", generation: int = 1, branch: int = 0) -> RsiProposalRequest:
    return RsiProposalRequest(
        model_id=model_id, parent_id="seed", generation=generation,
        branch=branch, objective="Improve the strategy",
        allowed_files=("agent/strategy.json",),
    )


def builder(model: FakeModel, gate: RsiArtifactGate) -> RsiModelCandidateBuilder:
    return RsiModelCandidateBuilder(
        model, gate, policy_digest=SHA, evaluator_digest=SHA
    )


def test_independent_models_share_one_candidate_contract(gateway):
    gate, ledger = gateway
    for branch, model_id in enumerate(("gpt-style", "claude-style", "local-qwen-style")):
        model = FakeModel(
            json.dumps({"files": {"agent/strategy.json": f'{{"model":"{model_id}"}}'}}),
            _id=model_id,
        )
        candidate = builder(model, gate).propose(
            request(model_id=model_id, branch=branch)
        )
        manifest = gate.verify(candidate)
        assert manifest["parent_id"] == "seed"
        assert model.calls == 1
        assert manifest["files"]["agent/strategy.json"]["bytes"] > 0
    assert ledger.verify()[0]


def test_candidate_scope_rejects_fake_scores(gateway):
    gate, _ = gateway
    model = FakeModel(json.dumps({
        "files": {"agent/strategy.json": '{"a":1}'},
        "verdict": "KEEP", "score": 1.0,
    }))
    with pytest.raises(ModelProtocolError, match="only files"):
        builder(model, gate).propose(request())
    assert not (gate.root / "rsi-g000001-b000").exists()


@pytest.mark.parametrize(
    "payload",
    ["not json", '["file"]', '{"files": {}}', '{"files":{"x.py":"evil"}}',
     '{"files":{"agent/module.py":12}}', '{"files": {"agent/strategy.json": "a"}, "NaN": NaN}'],
)
def test_malformed_model_proposals_fail_closed(gateway, payload):
    gate, _ = gateway
    with pytest.raises(ModelProtocolError):
        builder(FakeModel(payload), gate).propose(request())


def test_model_identity_mismatch_cannot_ask_provider(gateway):
    gate, _ = gateway
    model = FakeModel('{"files":{"agent/strategy.json":"ok"}}')
    with pytest.raises(ModelProtocolError, match="identity changed"):
        builder(model, gate).propose(request(model_id="other"))
    assert model.calls == 0


def test_candidate_source_not_executed_on_host(gateway):
    gate, ledger = gateway
    source = '__import__("os").system("echo unsafe > marker.txt")'
    model = FakeModel(json.dumps({"files": {"agent/module.py": source}}))
    spec = RsiProposalRequest(
        model_id=model.model_id, parent_id="seed", generation=1, branch=0,
        objective="Create source only", allowed_files=("agent/module.py",),
    )
    artifact = builder(model, gate).propose(spec)
    assert gate.verify(artifact)["authority"] == "candidate_only"
    assert not (gate.root / "marker.txt").exists()
    assert ledger.verify()[0]


def test_output_byte_cap_fails_before_storage(gateway):
    gate, _ = gateway
    spec = RsiProposalRequest(
        model_id="mock-model", parent_id="seed", generation=1, branch=0,
        objective="limited", allowed_files=("agent/strategy.json",),
        max_output_bytes=10,
    )
    with pytest.raises(ModelProtocolError, match="size cap"):
        builder(FakeModel('{"files":{"agent/strategy.json":"many bytes"}}'), gate).propose(spec)


def test_wrong_parent_evaluator_fails_before_model_invoked(gateway):
    gate, _ = gateway
    model = FakeModel('{"files":{"agent/strategy.json":"x"}}')
    candidate_builder = RsiModelCandidateBuilder(
        model, gate, policy_digest=SHA, evaluator_digest="d" * 64
    )
    with pytest.raises(ModelProtocolError, match="evaluator"):
        candidate_builder.propose(request())
    assert model.calls == 0


def test_remote_http_must_be_https_and_use_env_secret(monkeypatch):
    with pytest.raises(ValueError, match="HTTPS or loopback"):
        OpenAICompatibleProposalPort(model_id="example", base_url="http://remote.example")
    with pytest.raises(ValueError, match="HTTPS or loopback"):
        OpenAICompatibleProposalPort(model_id="example", base_url="https://user:key@remote.example")
    port = OpenAICompatibleProposalPort(model_id="example", base_url="https://remote.example")
    monkeypatch.delenv("WLS_RSI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="credential not configured"):
        port.propose("hi", max_output_bytes=128)


def test_http_client_uses_fixed_endpoint_and_does_not_log_key(monkeypatch):
    port = OpenAICompatibleProposalPort(
        model_id="remote-model", base_url="https://api.example"
    )
    monkeypatch.setenv("WLS_RSI_API_KEY", "secret-not-for-logs")

    class Response:
        def __enter__(self):
            return io.BytesIO(json.dumps({
                "choices": [{"message": {"content": '{"files":{"agent/strategy.json":"ok"}}'}}],
                "usage": {"prompt_tokens": 25, "completion_tokens": 11},
            }).encode())

        def __exit__(self, *_):
            return False

    def fake_open(req, *, timeout):
        assert req.full_url == "https://api.example/v1/chat/completions"
        assert req.get_header("Authorization") == "Bearer secret-not-for-logs"
        assert timeout == 45
        payload = json.loads(req.data)
        assert payload["model"] == "remote-model"
        return Response()

    monkeypatch.setattr(port._opener, "open", fake_open)
    result = port.propose("produce candidate", max_output_bytes=1024)
    assert result.output_tokens == 11
    assert result.input_tokens == 25
    assert result.model_id == "remote-model"


def test_invalid_model_capability_is_rejected():
    with pytest.raises(ValueError, match="requires text"):
        RsiModelCapabilities(text_generation=False)
    with pytest.raises(ValueError, match="context"):
        RsiModelCapabilities(context_tokens=64)
