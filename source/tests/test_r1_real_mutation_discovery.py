"""Falsifiable R1 mutation discovery tests, including a real subprocess check."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "make_mutation_tasks.py"


@pytest.fixture
def engine():
    spec = importlib.util.spec_from_file_location("wls_r1_real_mutation", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_mutation_is_exactly_one_ast_operator_on_one_source_line(engine) -> None:
    original = (
        "def grade(x: int) -> int:\n"
        "    if x >= 5 and x != 9:\n"
        "        return 1\n"
        "    return 0\n"
    ).encode()
    found = engine.candidates("probe.py", original)
    assert len(found) >= 3
    assert len({x["mutant_sha256"] for x in found}) == len(found)
    assert found == engine.candidates("probe.py", original)
    for mutant in found:
        raw = mutant["mutated_content"]
        assert engine.sha(raw) == mutant["mutant_sha256"]
        before, after = original.splitlines(), raw.splitlines()
        assert len(before) == len(after)
        assert sum(a != b for a, b in zip(before, after)) == 1
        assert mutant["mutation_line"] == 2
        assert mutant["source_sha256"] == engine.sha(original)


def test_string_comparisons_and_comments_do_not_create_fictitious_mutants(engine) -> None:
    source = b'def value():\n    example = "== and != <= >= "\n    # >= and <= != ==\n    return example\n'
    assert engine.candidates("no-expressions.py", source) == []


def test_real_pytest_fails_on_mutated_disposable_package(engine, tmp_path: Path) -> None:
    root = tmp_path / "trusted-public-worktree"
    temp = tmp_path / "private-temporary-probe"
    source = root / "source" / "src" / "wls"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("")
    (source / "probe.py").write_text(
        "def grade(x):\n    return 1 if x >= 3 else 0\n"
    )
    tests = root / "source/tests"
    tests.mkdir(parents=True)
    (tests / "test_probe.py").write_text(
        "from wls.probe import grade\n"
        "def test_grade():\n    assert grade(3) == 1\n"
    )
    copied = temp / "src/wls"
    copied.mkdir(parents=True)
    (copied / "__init__.py").write_text("")
    original = (source / "probe.py").read_bytes()
    (copied / "probe.py").write_bytes(original)
    first = engine.pytest_once(root, temp, copied.parent, "source/tests/test_probe.py", 30)
    assert first == (0, "PASS")
    mutated = next(
        x for x in engine.candidates("probe.py", original)
        if x["operator_from"] == ">=" and x["operator_to"] == ">"
    )
    (copied / "probe.py").write_bytes(mutated["mutated_content"])
    repeated = [
        engine.pytest_once(root, temp, copied.parent, "source/tests/test_probe.py", 30)
        for _ in range(3)
    ]
    assert repeated == [(1, "PYTEST_ASSERTION_FAILED")] * 3
    # A mutation to a disposable package never touched the trusted input.
    assert (source / "probe.py").read_bytes() == original
    (copied / "probe.py").write_bytes(original)
    assert engine.pytest_once(
        root, temp, copied.parent, "source/tests/test_probe.py", 30
    ) == (0, "PASS")


def test_bad_limits_and_missing_canonical_package_fail_closed(engine, tmp_path):
    with pytest.raises(ValueError, match="budget"):
        engine.run(tmp_path, 0, 5, 10)
    with pytest.raises(ValueError, match="minimum"):
        engine.run(tmp_path, 10, -1, 10)
    with pytest.raises(ValueError, match="timeout"):
        engine.run(tmp_path, 10, 5, 0)
    with pytest.raises(ValueError, match="canonical package"):
        engine.run(tmp_path, 10, 5, 10)


def test_runner_rejects_secret_bearing_environment(engine, tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "test-only-secret")
    monkeypatch.setenv("GH_TOKEN", "test-only-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-secret")
    safe = engine.safe_env(tmp_path, tmp_path)
    assert "GITHUB_TOKEN" not in safe
    assert "GH_TOKEN" not in safe
    assert safe["OPENAI_API_KEY"] == ""
    assert "test-only-secret" not in repr(safe)
