"""R2 Copilot public development experiment: candidate and oracle separation."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import run_r2_copilot_probe as probe  # noqa: E402


def choice_spec() -> dict:
    return {
        "task_ids": ["a", "b"],
        "visible_choice_ids": {"a": ["sha0", "sha1", "sha2", "sha3"], "b": ["sha4"]},
    }


def test_model_rankings_are_bounded_to_public_visible_candidates():
    chosen = probe.parse_choices(
        b'{"tasks":[{"id":"a","choices":["sha2","sha1","sha0"]},'
        b'{"id":"b","choices":[]}]}',
        choice_spec(),
    )
    assert chosen == {"a": ["sha2", "sha1", "sha0"], "b": []}


@pytest.mark.parametrize("raw", [
    b'{"tasks":[{"id":"a","choices":["sha0","sha1","sha2","sha3"]},'
    b'{"id":"b","choices":[]}]}',
    b'{"tasks":[{"id":"a","choices":["oracle"]},'
    b'{"id":"b","choices":[]}]}',
    b'{"tasks":[{"id":"a","choices":["sha1","sha1"]},'
    b'{"id":"b","choices":[]}]}',
    b'{"tasks":[{"id":"a","choices":["sha0"]}]}',
    b'{"tasks":[{"id":"a","choices":["sha0"]},'
    b'{"id":"a","choices":[]},{"id":"b","choices":[]}]}',
    b'{"tasks":[],"grade":"PASS"}',
    b'{"tasks":[],"tasks":[]}',
    b'',
])
def test_rejects_forged_duplicate_moved_and_fourth_choices(raw: bytes):
    with pytest.raises((probe.ProbeBlocked, ValueError)):
        probe.parse_choices(raw, choice_spec())


def test_model_response_markdown_fence_is_not_scoring_authority():
    raw = b'```json\n{"tasks":[{"id":"a","choices":[]},{"id":"b","choices":[]}]}\n```'
    assert probe.parse_choices(raw, choice_spec()) == {"a": [], "b": []}


def test_real_pytest_pass_does_not_count_as_exact_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    root = tmp_path / "repo"
    source = root / "source/src/wls"
    tests = root / "source/tests"
    source.mkdir(parents=True)
    tests.mkdir()
    (source / "__init__.py").write_text("", encoding="utf-8")
    original = b"def f(x):\n    return 1 if x >= 3 else 0\n"
    (source / "probe.py").write_bytes(original)
    (tests / "test_probe.py").write_text(
        "from wls.probe import f\ndef test_f():\n    assert f(3) == 1\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(probe.engine.BASELINE_TESTS, "probe.py",
                        "source/tests/test_probe.py")
    faulty = next(
        row for row in probe.engine.candidates("probe.py", original)
        if row["operator_from"] == ">=" and row["operator_to"] == ">"
    )
    receipt = {"tasks": [{
        "id": faulty["id"], "source_path": faulty["source_path"],
        "selected_test": "source/tests/test_probe.py",
        "source_sha256": probe.engine.sha(original),
        "mutant_sha256": faulty["mutant_sha256"],
    }], "runner_provenance": {"github_sha": "a" * 40, "github_run_id": "1"}}
    choices = probe.baseline.candidate_pool("probe.py", faulty["mutated_content"])
    wrong = next(item for item in choices
                 if item["mutant_sha256"] != probe.engine.sha(original))
    calls: list[int] = []

    def fake_pytest(*args):
        calls.append(1)
        return 0, "PASS"

    monkeypatch.setattr(probe.engine, "pytest_once", fake_pytest)
    result = probe.score(
        root, receipt, [{
            "task_id": faulty["id"], "mutant_sha256": faulty["mutant_sha256"],
            "strategies": {
                "line_order": {"exact_original_restored": False},
                "seeded_random": {"exact_original_restored": False},
            },
        }], {faulty["id"]: [wrong["mutant_sha256"]]},
    )
    assert result["public_tasks_scored"] == 1
    assert result["model_exact_restorations"] == 0
    assert result["total_model_pytest_calls"] == 1
    assert result["cases"][0]["model_selected_test_pass"] is True
    assert result["model_beats_both_public_baselines"] is False
    assert (source / "probe.py").read_bytes() == original


def test_empty_copilot_response_is_model_unavailable_not_success():
    with pytest.raises(probe.ModelUnavailable):
        probe.parse_choices(b"", choice_spec())
