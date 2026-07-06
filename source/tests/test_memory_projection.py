from __future__ import annotations

import json

import pytest

from wls.db import Database
from wls.memory_projection import MemoryProjection, MemoryProjectionEngine, WorkingSetCache
from wls.schemas import new_id, utc_now


@pytest.fixture
def db_with_memories(tmp_path):
    db = Database(tmp_path / "test.db")
    from wls.evidence import EvidenceLedger

    for i in range(5):
        db.execute(
            """INSERT INTO memories(
                memory_id, memory_type, content_json, normalized_text,
                importance, confidence, source_ids_json, tags_json,
                created_at, last_accessed_at, active
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)""",
            (
                f"mem_{i}",
                "episodic",
                json.dumps({"summary": f"memory {i}", "text": f"content {i}"}),
                f"content {i}",
                0.5 + i * 0.1,
                0.8,
                json.dumps(["src_1"]),
                json.dumps(["tag_a"]),
                utc_now(),
                utc_now(),
            ),
        )
    return db


class TestMemoryProjectionEngine:
    def test_project_returns_projections(self, db_with_memories):
        engine = MemoryProjectionEngine(db_with_memories)
        projs = engine.project(limit=10)
        assert len(projs) == 5
        assert all(isinstance(p, MemoryProjection) for p in projs)

    def test_filter_by_type(self, db_with_memories):
        engine = MemoryProjectionEngine(db_with_memories)
        projs = engine.project(memory_type="episodic")
        assert len(projs) == 5
        projs = engine.project(memory_type="procedural")
        assert len(projs) == 0

    def test_min_importance_filter(self, db_with_memories):
        engine = MemoryProjectionEngine(db_with_memories)
        projs = engine.project(min_importance=0.8)
        assert len(projs) <= 3

    def test_limit(self, db_with_memories):
        engine = MemoryProjectionEngine(db_with_memories)
        projs = engine.project(limit=2)
        assert len(projs) == 2

    def test_build_working_set_token_budget(self, db_with_memories):
        engine = MemoryProjectionEngine(db_with_memories)
        cache = engine.build_working_set(max_tokens=100)
        assert isinstance(cache, WorkingSetCache)
        assert cache.token_count <= 100
        assert cache.cache_digest

    def test_working_set_invalidation(self):
        cache = WorkingSetCache(
            cache_id="ws1",
            projections=[
                MemoryProjection(
                    memory_id="m1", memory_type="episodic", summary="a",
                    importance=0.5, confidence=0.8, active=True,
                    validity="VALID", scope="", dependencies=[],
                    provenance_digest="abc"),
            ],
        )
        assert cache.invalidate_on({"m1"})
        assert not cache.invalidate_on({"m2"})

    def test_projection_to_dict(self):
        p = MemoryProjection(
            memory_id="m1", memory_type="episodic", summary="test",
            importance=0.9, confidence=0.8, active=True,
            validity="VALID", scope="", dependencies=[],
            provenance_digest="abc",
        )
        d = p.to_dict()
        assert d["memory_id"] == "m1"
        assert d["validity"] == "VALID"

    def test_empty_database(self, tmp_path):
        db = Database(tmp_path / "empty.db")
        engine = MemoryProjectionEngine(db)
        projs = engine.project()
        assert projs == []
