from __future__ import annotations

from wls.merge_node import MergeArtifact, MergeNode, MergedResult


class TestMergeNode:
    def test_single_artifact_passthrough(self):
        node = MergeNode()
        artifacts = [MergeArtifact("a1", "n1", "abc123", "text")]
        result = node.merge(artifacts)
        assert result.passed
        assert result.result_digest == "abc123"
        assert not result.has_conflicts()

    def test_empty_artifacts(self):
        node = MergeNode()
        result = node.merge([])
        assert result.passed
        assert not result.has_conflicts()

    def test_text_merge_concatenates(self):
        node = MergeNode()
        artifacts = [
            MergeArtifact("a1", "n1", "hello", "text"),
            MergeArtifact("a2", "n2", "world", "text"),
        ]
        result = node.merge(artifacts)
        assert result.passed
        assert not result.has_conflicts()
        assert len(result.artifacts_merged) == 2

    def test_dict_merge_no_conflict(self):
        import json
        node = MergeNode()
        a1 = MergeArtifact("a1", "n1", json.dumps({"x": 1}), "json")
        a2 = MergeArtifact("a2", "n2", json.dumps({"y": 2}), "json")
        result = node.merge([a1, a2])
        assert result.passed
        assert not result.has_conflicts()

    def test_dict_merge_with_conflict(self):
        import json
        node = MergeNode()
        a1 = MergeArtifact("a1", "n1", json.dumps({"x": 1}), "json")
        a2 = MergeArtifact("a2", "n2", json.dumps({"x": 2}), "json")
        result = node.merge([a1, a2])
        assert result.has_conflicts()
        assert not result.passed

    def test_merge_result_to_dict(self):
        result = MergedResult("m1", ["a1"], "abc", passed=True)
        d = result.to_dict()
        assert d["merge_id"] == "m1"
        assert d["passed"]
