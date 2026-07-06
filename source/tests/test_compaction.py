from __future__ import annotations

from wls.compaction import ResumePacket, SessionCompactor


class TestSessionCompactor:
    def test_compact_creates_packet(self):
        sc = SessionCompactor()
        packet = sc.compact(
            "task-1",
            baseline={"task": "test"},
            checkpoint={"node": "n1", "status": "running"},
            recent={"last_action": "read"},
            evidence_refs=["ev_1"],
            unresolved=["unknown_side_effect_1"],
            acceptance={"oracle": "pass"},
            dependencies={"n1": "n0"},
        )
        assert packet.task_id == "task-1"
        assert len(packet.evidence_refs) == 1
        assert len(packet.unresolved_unknowns) == 1
        assert packet.acceptance_state["oracle"] == "pass"
        assert packet.dependency_state["n1"] == "n0"

    def test_verify_resume_true(self):
        sc = SessionCompactor()
        state = {"node": "ok"}
        packet = sc.compact("t1", checkpoint=state)
        assert sc.verify_resume(packet, state)

    def test_verify_resume_false(self):
        sc = SessionCompactor()
        packet = sc.compact("t1", checkpoint={"old": "data"})
        assert not sc.verify_resume(packet, {"new": "data"})

    def test_compact_layers(self):
        sc = SessionCompactor()
        layers = {
            "immutable_baseline": {"a": 1},
            "structured_checkpoint": {"b": 2},
        }
        records = sc.compact_layers("task-1", layers)
        assert len(records) == 2

    def test_resume_packet_to_dict(self):
        packet = ResumePacket(
            packet_id="p1", task_id="t1",
            baseline_digest="a", checkpoint_digest="b", recent_digest="c",
            evidence_refs=["e1"], unresolved_unknowns=["u1"],
            acceptance_state={"ok": True}, dependency_state={"n1": "done"},
        )
        d = packet.to_dict()
        assert d["task_id"] == "t1"
        assert d["evidence_refs"] == ["e1"]
