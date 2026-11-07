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


def test_copilot_cannot_spend_on_push_or_default_manual_dispatch():
    import yaml
    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github/workflows/wls-r1-real-windows-mutations.yml").read_text()
    )
    job = workflow["jobs"]["copilot-public-probe"]
    assert "github.event_name == 'workflow_dispatch'" in job["if"]
    assert "inputs.authorize_copilot_credits == true" in job["if"]
    assert "--max-ai-credits=1" not in str(job)
    assert "--max-ai-credits=30" in str(job)
    events = workflow.get("on", workflow.get(True))
    assert events["workflow_dispatch"]["inputs"]["authorize_copilot_credits"]["default"] is False


def test_public_r2_local_probe_has_frozen_pinned_identity_and_no_promotion():
    import inspect
    source = inspect.getsource(probe.main)
    assert '"generate-local"' in source
    assert '"offline_qwen_1.5b"' in source
    assert '"promote_source": False' in source
    assert 'model_inference_count' in source
    assert 'args.model_proof.write_bytes(canonical(proof))' in source


def test_local_probe_workflow_never_uses_model_spending_token():
    import yaml
    root = Path(__file__).resolve().parents[2]
    doc = yaml.safe_load(
        (root / ".github/workflows/wls-r1-real-windows-mutations.yml").read_text()
    )
    job = doc["jobs"]["local-model-public-probe"]
    assert job["permissions"]["contents"] == "read"
    assert job["env"]["GITHUB_TOKEN"] == ""
    assert job["env"]["GH_TOKEN"] == ""
    assert "--mode generate-local" in str(job)
    assert "--provider local" in str(job)
    assert "copilot-requests" not in str(job)


def test_actual_model_invalid_json_is_scored_as_zero_not_a_blocker():
    spec = choice_spec()
    for reply in (
        b'{"tasks":[{"id":"a","choices":["oracle"]},{"id":"b","choices":[]}]}',
        b'not json',
        b'{"tasks":[]}',
    ):
        choices, valid, failure = probe.measured_choices(reply, spec)
        assert choices == {"a": [], "b": []}
        assert valid is False
        assert failure in {"ProbeBlocked", "JSONDecodeError"}


def test_actual_model_valid_json_remains_authorized_candidate_selection():
    choices, valid, failure = probe.measured_choices(
        b'{"tasks":[{"id":"a","choices":["sha2"]},{"id":"b","choices":[]}]}',
        choice_spec(),
    )
    assert valid is True and failure is None
    assert choices["a"] == ["sha2"]


def test_no_model_response_cannot_be_misreported_as_scored():
    with pytest.raises(probe.ModelUnavailable):
        probe.measured_choices(b"", choice_spec())


def test_r2_parser_rejects_unhashable_candidate_without_type_error():
    raw = b'{"tasks":[{"id":"a","choices":[[]]},{"id":"b","choices":[]}]}'
    choices, valid, failure = probe.measured_choices(raw, choice_spec())
    assert choices == {"a": [], "b": []}
    assert not valid and failure == "ProbeBlocked"


def test_r2_direct_scorer_rejects_fourth_or_forged_choice_before_pytest(tmp_path):
    for choices in ({"a": ["1", "2", "3", "4"]}, {"a": [123]},
                    {"a": ["same", "same"]}, {}):
        with pytest.raises(probe.ProbeBlocked):
            probe.score(tmp_path, {}, [{"task_id": "a"}], choices)


def test_r2_duplicate_json_keys_in_baseline_fail_closed(tmp_path, monkeypatch):
    # Check that duplicate keys cannot silently overwrite a trusted receipt.
    monkeypatch.setattr(probe.replay, "read_receipt", lambda _: {
        "task_digest": "a", "runner_provenance": {"github_sha": "s", "github_run_id": "1"},
    })
    monkeypatch.setattr(probe.baseline, "read_replay", lambda *args: [])
    monkeypatch.setattr(probe.replay, "checked_source", lambda *args: None)
    duplicate = tmp_path / "baseline.json"
    duplicate.write_text('{"schema":"x","schema":"y"}')
    p = tmp_path / "receipt.json"
    p.write_text("{}")
    with pytest.raises(probe.ProbeBlocked, match="duplicate key"):
        probe.selected_cases(tmp_path, p, p, duplicate)
