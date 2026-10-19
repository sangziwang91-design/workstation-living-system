"""R2: public repair baseline bounded attempts; this is not a model win."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import make_mutation_tasks as engine  # noqa: E402
import score_r2_three_try_baselines as baseline  # noqa: E402


def test_both_strategies_hard_stop_after_three_real_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = tmp_path / "package/wls"
    isolated.mkdir(parents=True)
    broken = b"def value(x):\n    return 1 if x > 4 else 0\n"
    (isolated / "probe.py").write_bytes(broken)
    calls: list[bytes] = []
    def real_call(root: Path, temp: Path, package: Path,
                  selected: str, timeout: int) -> tuple[int, str]:
        calls.append((isolated / "probe.py").read_bytes())
        assert selected == "source/tests/test_probe.py"
        return 1, "PYTEST_ASSERTION_FAILED"
    monkeypatch.setattr(engine, "pytest_once", real_call)
    for strategy in ("line_order", "seeded_random"):
        calls.clear()
        out = baseline.try_repair(
            tmp_path, tmp_path, isolated, "source/tests/test_probe.py",
            broken, "0" * 64, "r2_test_task", "probe.py", strategy, 15,
        )
        assert len(calls) == 3
        assert out["tests_used"] == 3
        assert out["max_tests"] == 3
        assert out["selected_test_pass"] is False
        assert out["exact_original_restored"] is False
        assert (isolated / "probe.py").read_bytes() == broken


def test_one_real_pytest_fix_outcome_from_mutant_not_manifest_answer(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    isolated = tmp_path / "isolated" / "wls"
    src = root / "source/src/wls"
    tests = root / "source/tests"
    for folder in (src, tests, isolated):
        folder.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (isolated / "__init__.py").write_text("")
    original = b"def grade(x):\n    return 1 if x >= 3 else 0\n"
    (src / "probe.py").write_bytes(original)
    (isolated / "probe.py").write_bytes(original)
    (tests / "test_grade.py").write_text(
        "from wls.probe import grade\n"
        "def test_grade():\n    assert grade(3) == 1\n",
    )
    mutated = next(
        x for x in engine.candidates("probe.py", original)
        if x["operator_from"] == ">=" and x["operator_to"] == ">"
    )
    broken = mutated["mutated_content"]
    (isolated / "probe.py").write_bytes(broken)
    result = baseline.try_repair(
        root, tmp_path / "isolated", isolated, "source/tests/test_grade.py",
        broken, engine.sha(original), "r2_real_pytest_probe", "probe.py",
        "line_order", 25,
    )
    assert result["tests_used"] <= 3
    assert result["selected_test_pass"] is True
    assert result["exact_original_restored"] is True
    assert (isolated / "probe.py").read_bytes() == broken
    assert (src / "probe.py").read_bytes() == original


def test_candidate_list_is_built_from_broken_source_only() -> None:
    broken = b"def f(x):\n    return 1 if x > 3 else 0\n"
    choices = baseline.candidate_pool("probe.py", broken)
    assert len(choices) >= 2
    assert all(row["mutant_sha256"] != engine.sha(broken) for row in choices)
    assert choices == baseline.candidate_pool("probe.py", broken)
    for x in choices:
        assert engine.sha(x["mutated_content"]) == x["mutant_sha256"]
        assert len(x["mutated_content"].splitlines()) == len(broken.splitlines())


def test_seeded_random_order_is_stable_without_answer() -> None:
    candidates = [{"mutant_sha256": str(x), "mutation_line": x}
                  for x in range(15)]
    a = baseline.rank(candidates, "seeded_random", "task-1")
    assert a == baseline.rank(candidates, "seeded_random", "task-1")
    assert {c["mutant_sha256"] for c in a} == {
        c["mutant_sha256"] for c in candidates
    }
    assert baseline.rank(candidates, "line_order", "task-1") == candidates
    with pytest.raises(baseline.BaselineBlocked):
        baseline.rank(candidates, "oracle", "task-1")


def test_untrusted_replay_missing_cases_or_forged_pass_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = {
        "schema": engine.SCHEMA,
        "tasks": [{"id": "r1", "source_path": "source/src/wls/probe.py",
                   "mutant_sha256": "a" * 64}],
        "runner_provenance": {"github_run_id": "111", "github_sha": "b" * 40},
    }
    monkeypatch.setattr(baseline.replay, "read_receipt", lambda raw: receipt)
    valid = {
        "schema": baseline.replay.SCHEMA, "status": "STABLE_REPLAY_PASS",
        "source_run_id": "111", "source_sha": "b" * 40,
        "observed_task_count": 1, "stable_task_count": 1,
        "unstable_task_count": 0, "stable_task_ids": ["r1"],
        "cases": [{"id": "r1", "source_path": "source/src/wls/probe.py",
                   "mutant_sha256": "a" * 64, "status": "STABLE",
                   "second_pass_stable": True,
                   "second_pass_test_invocations": 3}],
    }
    assert baseline.read_replay(baseline.encoded(valid), receipt) == ["r1"]
    for changed in (
        {"source_run_id": "999"},
        {"stable_task_count": 2},
        {"cases": [{**valid["cases"][0], "second_pass_test_invocations": 2}]},
        {"cases": [{**valid["cases"][0], "mutant_sha256": "c" * 64}]},
        {"cases": []},
    ):
        with pytest.raises(baseline.BaselineBlocked):
            baseline.read_replay(baseline.encoded({**valid, **changed}), receipt)
    with pytest.raises(baseline.BaselineBlocked, match="duplicate JSON"):
        baseline.read_replay(b'{"x":1,"x":2}', receipt)


def test_budget_fails_closed_and_source_restores_on_pytest_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated = tmp_path / "wls"
    isolated.mkdir()
    broken = b"def grade(x):\n    return x > 3\n"
    (isolated / "probe.py").write_bytes(broken)
    monkeypatch.setattr(
        engine, "pytest_once",
        lambda *_: (_ for _ in ()).throw(ValueError("failing runner")),
    )
    with pytest.raises(ValueError, match="failing runner"):
        baseline.try_repair(
            tmp_path, tmp_path, isolated, "source/tests/test_probe.py",
            broken, "0" * 64, "r2_fault", "probe.py", "line_order", 15,
        )
    assert (isolated / "probe.py").read_bytes() == broken
    with pytest.raises(baseline.BaselineBlocked, match="runtime"):
        baseline.try_repair(
            tmp_path, tmp_path, isolated, "source/tests/test_probe.py",
            broken, "0" * 64, "r2_fault", "probe.py", "line_order", 0,
        )
