"""Deterministic adversarial coverage of the read-only R2 public diagnostic."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import diagnose_r2_paired_outcomes as diagnostic  # noqa: E402


def test_paired_diagnostic_rejects_moved_public_baseline() -> None:
    """All public summary and baseline proof limits are exercised in CI."""
    diagnostic.self_test()

def test_frozen_public_cohort_manifest_matches_immutable_run() -> None:
    path = Path(__file__).parent / "fixtures/r2_public_20261108_freeze.json"
    frozen = json.loads(path.read_text(encoding="utf-8"))
    assert frozen["source_sha"] == "04dfb7347d227a1ec88fdee9e99883e187244f25"
    assert frozen["source_run_id"] == "42456184114"
    assert frozen["seed"] == 20261019
    assert frozen["shared_candidate_pool_limit"] == 16
    assert len(frozen["mutations"]) == 12
    assert len({m["task_id"] for m in frozen["mutations"]}) == 12
    content = json.dumps(
        frozen, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")
    assert sha256(content).hexdigest() == (
        "9461263f2a36fdf72e2325ac259689334b348530d63ff3a7a1f3cb974226dd39"
    )
