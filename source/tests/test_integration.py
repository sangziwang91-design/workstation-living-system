from __future__ import annotations

from pathlib import Path
import json

import pytest

from wls.acceptance import AcceptanceOracle, AcceptanceVerdict, schema_check, threshold_check
from wls.anti_repeat import AntiRepeatGuard
from wls.benchmark import BenchmarkCase, BenchmarkSuite
from wls.compaction import SessionCompactor
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.fault_injection import FaultInjection, FaultInjector, FaultKind, FaultOutcome
from wls.graph_recovery import GraphRecoveryEngine, SideEffectRecord
from wls.merge_node import MergeArtifact, MergeNode
from wls.result_promotion import ResultCandidate, ResultPromotionGate
from wls.reviewer import HeterogeneousReviewer, ReviewOpinion
from wls.schemas import TaskNodeStatus, new_id, utc_now
from wls.security import (
    SecurityFirewall,
    credential_leak_check,
    host_allowlist_check,
    path_escape_check,
    prompt_injection_check,
)
from wls.stores import EventStore


@pytest.fixture
def worktree(tmp_path):
    wt = tmp_path / "worktree"
    wt.mkdir()
    (wt / "main.py").write_text("def hello():\n    return 'world'\n")
    (wt / "test_main.py").write_text("def test_hello():\n    assert True\n")
    return wt


@pytest.fixture
def db_and_ledger(tmp_path):
    db = Database(tmp_path / "test.db")
    secret = tmp_path / "secret.key"
    ledger = EvidenceLedger(db=db, secret_path=secret)
    events = EventStore(db, ledger)
    return db, ledger, events


class TestAgenticHarnessIntegration:
    """End-to-end: admit_and_compile → acquire_ready_leases → complete_node → audit."""

    def test_full_pipeline(self, db_and_ledger):
        db, ledger, events = db_and_ledger
        from wls.agentic_harness import AgenticHarness

        harness = AgenticHarness(db, ledger)
        admission = harness.admit_and_compile(
            "Implement a hello world function in Python",
        )
        assert admission["intent"]
        assert admission["graph"]

        graph_id = admission["graph"]["graph_id"]
        leases = harness.acquire_ready_leases(graph_id, worker_id="readonly-inspector")
        assert len(leases) == 1

        lease = leases[0]
        result = harness.complete_node(
            graph_id=graph_id,
            node_id=lease.node_id,
            lease_id=lease.lease_id,
            result={"status": "success", "output": "def hello(): return 'world'"},
        )
        assert result["graph_id"] == graph_id

        audit = harness.audit_graph_process(graph_id, reason="integration-test")
        assert "issues" in audit

    def test_duplicate_domain_blocked(self, db_and_ledger):
        db, ledger, events = db_and_ledger
        from wls.agentic_harness import AgenticHarness

        harness = AgenticHarness(db, ledger)
        admission = harness.admit_and_compile("Write a hello world function")
        graph_id = admission["graph"]["graph_id"]

        leases1 = harness.acquire_ready_leases(graph_id, worker_id="readonly-inspector")
        assert len(leases1) == 1

        leases2 = harness.acquire_ready_leases(graph_id, worker_id="planner-shadow")
        assert len(leases2) == 0


class TestCodingWorkerIntegration:
    """Coding worker factory + contract + adapter chain."""

    def test_factory_status(self):
        from wls.coding_workers import CodingWorkerFactory

        factory = CodingWorkerFactory()
        status = factory.status()
        assert len(status) == 3
        for name, info in status.items():
            assert isinstance(info["available"], bool)

    def test_contract_validates(self, worktree):
        from wls.coding_adapter import CodingTaskContract

        contract = CodingTaskContract(
            task_id="t1", base_sha="abc", worktree=worktree,
            changed_files=["main.py"], tests=["pytest"], rollback=["git checkout"],
        )
        contract.validate()

    def test_contract_rejects_escape(self, worktree):
        from wls.coding_adapter import CodingTaskContract

        contract = CodingTaskContract(
            task_id="t1", base_sha="abc", worktree=worktree,
            changed_files=["../outside.py"], tests=["test"], rollback=["revert"],
        )
        with pytest.raises(ValueError, match="escape"):
            contract.validate()

    def test_candidate_receipt(self, worktree):
        from wls.coding_adapter import CodingTaskContract

        contract = CodingTaskContract(
            task_id="t1", base_sha="abc", worktree=worktree,
            changed_files=["main.py"], tests=["pytest"], rollback=["git checkout"],
        )
        receipt = contract.candidate_receipt()
        assert receipt.task_id == "t1"
        assert receipt.status == "CANDIDATE_ONLY"
        assert len(receipt.changed_files) == 1


class TestOffspringIntegration:
    """Offspring draft_birth_contract → lifecycle gates."""

    def test_draft_contract(self, tmp_path):
        from wls.offspring import OffspringRegistry
        from wls.config import RuntimeConfig

        config = RuntimeConfig(home=str(tmp_path / "home"))
        db = Database(tmp_path / "parent.db")
        secret = tmp_path / "secret.key"
        ledger = EvidenceLedger(db=db, secret_path=secret)
        registry = OffspringRegistry(config=config, db=db, ledger=ledger)

        contract = registry.draft_birth_contract(
            parent_head="abc123",
            mission="test offspring lifecycle verification",
            budget={"max_tokens": 1000, "max_seconds": 60, "max_cost_usd": 0.0},
            inheritance_manifest={"allowed_tools": ["read_file"], "inherit_memory": False},
            termination_conditions=["budget_exhausted", "no_gain_stop", "owner_stop"],
            reason="integration test",
        )
        assert contract["offspring_id"]
        assert contract["parent_id"] == "WLS-PRIME"


class TestSecurityAcceptanceIntegration:
    """Security firewall → acceptance oracle → result promotion gate."""

    def test_pipeline(self, tmp_path):
        fw = SecurityFirewall()
        fw.register_rule("path", path_escape_check([str(tmp_path)]))
        fw.register_rule("host", host_allowlist_check(["localhost"]))
        fw.register_rule("prompt", lambda ctx: prompt_injection_check(
            ctx.get("user_input", "")))

        audit = fw.audit("task-1", {
            "resolved_path": str(tmp_path / "safe.py"),
            "host": "localhost",
            "user_input": "What is the weather?",
        })
        assert not audit.has_critical()

        oracle = AcceptanceOracle()
        oracle.register("schema", "schema", lambda ctx: schema_check(
            ctx.get("result", {}), ["status", "output"]))
        oracle.register("threshold", "threshold", lambda ctx: threshold_check(
            "success_rate", ctx.get("success_rate", 0), 0.5, "ge"))

        report = oracle.evaluate("task-1", "node", {
            "result": {"status": "ok", "output": "hello"},
            "success_rate": 0.95,
        })
        assert report.verdict == AcceptanceVerdict.PASS

        gate = ResultPromotionGate()
        candidate = ResultCandidate(
            candidate_id="c1", source_worker="w1", source_scope="test-proj",
            content={"_delivery_ack": True, "status": "ok"},
            content_digest="abc123", evidence_ids=["ev1"],
        )
        decision = gate.review(
            candidate, allowed_scopes=["test-proj"], target_scope="test-proj",
        )
        assert decision.admitted

    def test_credential_leak_blocked(self, tmp_path):
        fw = SecurityFirewall()
        fw.register_rule("cred", lambda ctx: credential_leak_check(
            ctx.get("value", "")))
        audit = fw.audit("task-1", {"value": "Bearer sk-abc123"})
        assert audit.has_critical()


class TestMemoryCompactionIntegration:
    """Memory projection → session compaction → resume."""

    def test_pipeline(self, tmp_path):
        db = Database(tmp_path / "test.db")

        compactor = SessionCompactor()
        packet = compactor.compact(
            "task-mc-1",
            baseline={"task": "mem compact test"},
            checkpoint={"phase": "done"},
            recent={"last": "run"},
            evidence_refs=["ev1"],
            unresolved=[],
            acceptance={"oracle": "PASS"},
            dependencies={"p1": "completed"},
        )
        assert packet.task_id == "task-mc-1"
        assert packet.acceptance_state["oracle"] == "PASS"

        can_resume = compactor.verify_resume(packet, {"phase": "done"})
        assert can_resume

        layers = compactor.compact_layers("task-mc-1", {
            "immutable_baseline": {"task": "test"},
            "structured_checkpoint": {"phase": "done"},
        })
        assert len(layers) == 2


class TestFaultRecoveryIntegration:
    """Fault injection → graph recovery → anti-repeat suppression."""

    def test_pipeline(self):
        injector = FaultInjector()
        injector.register_handler(
            FaultKind.PROCESS_KILL,
            lambda inj: FaultOutcome(
                outcome_id=new_id("fo"), injection_id=inj.injection_id,
                kind=inj.kind, target=inj.target,
                survived=True, recovery_action="RESTARTED",
                duration_seconds=0.5, state_corrupted=False,
            ),
        )
        injector.register_handler(
            FaultKind.STALE_LEASE,
            lambda inj: FaultOutcome(
                outcome_id=new_id("fo"), injection_id=inj.injection_id,
                kind=inj.kind, target=inj.target,
                survived=True, recovery_action="RECOVERED",
                duration_seconds=0.2, state_corrupted=False,
            ),
        )

        o1 = injector.inject(FaultInjection("i1", FaultKind.PROCESS_KILL, "runtime"))
        o2 = injector.inject(FaultInjection("i2", FaultKind.STALE_LEASE, "worker-1"))
        assert o1.survived
        assert o2.survived
        assert injector.summary()["survival_rate"] == 1.0

        engine = GraphRecoveryEngine()
        se = SideEffectRecord(
            record_id="se1", graph_id="g1", node_id="n1",
            side_effect_class="write", idempotency_key="ik1",
            status="UNKNOWN", dispatched_at=utc_now(),
        )
        decisions = engine.recover_graph("g1", {
            "n1": TaskNodeStatus.LEASED.value,
            "n2": TaskNodeStatus.SUCCEEDED.value,
            "n3": TaskNodeStatus.PENDING.value,
        }, {"n1": se})
        assert len(decisions) == 3

        guard = AntiRepeatGuard(max_repeat_count=3)
        for _ in range(3):
            guard.record("process_kill_runtime")
        assert guard.should_suppress("process_kill_runtime")


class TestMergeReviewBenchmarkIntegration:
    """Merge node → heterogeneous review → benchmark comparison."""

    def test_pipeline(self):
        import json as _json

        node = MergeNode()
        a1 = MergeArtifact("a1", "w-a", _json.dumps({"x": 1, "y": 2}), "json")
        a2 = MergeArtifact("a2", "w-b", _json.dumps({"x": 1, "z": 3}), "json")
        merged = node.merge([a1, a2])
        assert not merged.has_conflicts()

        reviewer = HeterogeneousReviewer()
        reviewer.register("r1", lambda ctx: ReviewOpinion(
            "r1", "PASS", 0.9, "merge correct", []))
        reviewer.register("r2", lambda ctx: ReviewOpinion(
            "r2", "PASS", 0.85, "no conflicts", []))
        review = reviewer.review("merge-1", {})
        assert review.consensus_verdict == "PASS"

        suite = BenchmarkSuite("integration", discriminator="v1")
        suite.add(BenchmarkCase("c1", "merge", "integ", input_data={}),
                  lambda d: {"passed": merged.passed,
                             "metrics": {"conflicts": len(merged.conflicts)}})
        suite.add(BenchmarkCase("c2", "review", "integ", input_data={}),
                  lambda d: {"passed": not review.escalated,
                             "metrics": {"opinions": len(review.opinions)}})
        report = suite.run()
        assert report.success_rate() == 1.0
