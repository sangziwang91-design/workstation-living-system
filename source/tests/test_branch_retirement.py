"""Fail-closed negative tests for a one-time branch-ref deletion, never deletes here."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/prune_legacy_branches.py"


@pytest.fixture
def cleanup():
    spec = importlib.util.spec_from_file_location("wls_retire_legacy_refs_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_sourced_retirement_inventory_has_120_stale_shas(cleanup) -> None:
    manifest = cleanup.read_manifest()
    assert len(manifest["branches"]) == 120
    assert len(set(row["branch"] for row in manifest["branches"])) == 120
    assert len(set(row["sha"] for row in manifest["branches"])) < 120
    assert set(manifest["expected_open_pr_branches"]) == {
        "refactor/s1a-proven-unreferenced-modules-20261011",
        "feat/r1-frozen-contract-preflight-20261011",
        "feat/r1-real-windows-mutation-discovery-20261016",
    }
    assert {row["disposition"] for row in manifest["branches"]} == {
        "PR_MERGED", "PR_CLOSED_UNMERGED", "NO_PR",
    }
    assert "main" not in [row["branch"] for row in manifest["branches"]]


@pytest.mark.parametrize("defect", [
    "bad_schema", "no_tip", "duplicate_branch", "active_pr_branch",
    "main_branch", "invalid_sha", "wrong_archive", "missing_refs",
])
def test_manifest_cannot_be_redefined_into_deleting_unreviewed_refs(
    cleanup, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    data = cleanup.read_manifest()
    if defect == "bad_schema":
        data["schema"] = "anything"
    elif defect == "no_tip":
        data["branches"][0]["sha"] = "0" * 39
    elif defect == "duplicate_branch":
        data["branches"][0]["branch"] = data["branches"][1]["branch"]
    elif defect == "active_pr_branch":
        data["branches"][0]["branch"] = data["expected_open_pr_branches"][0]
    elif defect == "main_branch":
        data["branches"][0]["branch"] = "main"
    elif defect == "invalid_sha":
        data["branches"][0]["sha"] = "!" * 40
    elif defect == "wrong_archive":
        data["archive_tag"] = "another-tag"
    elif defect == "missing_refs":
        data["branches"].pop()
    file = tmp_path / "manifest.json"
    file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(cleanup, "ROOT", tmp_path)
    monkeypatch.setattr(cleanup, "MANIFEST", "manifest.json")
    with pytest.raises(cleanup.RetirementBlocked):
        cleanup.read_manifest()


def test_no_open_pr_or_head_sha_drift_may_start_cleanup(
    cleanup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = cleanup.read_manifest()
    monkeypatch.setenv("GITHUB_REPOSITORY", cleanup.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setattr(cleanup, "git", lambda *args, **kw: "f" * 40)
    monkeypatch.setattr(cleanup, "ancestor", lambda old, new: True)
    monkeypatch.setattr(cleanup, "open_pr_branches", lambda: set())
    monkeypatch.setattr(cleanup, "all_branches", lambda: {})
    with pytest.raises(cleanup.RetirementBlocked, match="open PR inventory"):
        cleanup.preflight(d)


def test_old_sha_mismatch_never_reaches_archive_or_delete(
    cleanup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    d = cleanup.read_manifest()
    monkeypatch.setenv("GITHUB_REPOSITORY", cleanup.REPO)
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setattr(cleanup, "git", lambda *args, **kw: "f" * 40)
    monkeypatch.setattr(cleanup, "ancestor", lambda old, new: True)
    monkeypatch.setattr(
        cleanup, "open_pr_branches",
        lambda: set(d["expected_open_pr_branches"]),
    )
    state: dict[str, dict[str, Any]] = {
        row["branch"]: {"commit": {"sha": row["sha"]}, "protected": False}
        for row in d["branches"]
    }
    state["main"] = {"commit": {"sha": "f" * 40}, "protected": False}
    state[cleanup.CLEANUP_BRANCH] = {
        "commit": {"sha": "f" * 40}, "protected": False,
    }
    for name in d["expected_open_pr_branches"]:
        state[name] = {"commit": {"sha": "f" * 40}, "protected": False}
    state[d["branches"][0]["branch"]]["commit"]["sha"] = "e" * 40
    monkeypatch.setattr(cleanup, "all_branches", lambda: state)
    monkeypatch.setattr(
        cleanup, "archive_all",
        lambda *_: pytest.fail("archive cannot run before full preflight"),
    )
    with pytest.raises(cleanup.RetirementBlocked, match="SHA changed"):
        cleanup.execute(d)


def test_git_archive_failure_blocks_every_deletion(
    cleanup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = cleanup.read_manifest()
    monkeypatch.setattr(cleanup, "preflight", lambda *_: ({}, data["branches"]))
    monkeypatch.setattr(
        cleanup, "archive_all",
        lambda *_: (_ for _ in ()).throw(
            cleanup.RetirementBlocked("archive tag push rejected")
        ),
    )
    monkeypatch.setattr(cleanup, "retire", lambda *_: pytest.fail("unsafe deletion"))
    with pytest.raises(cleanup.RetirementBlocked, match="archive tag"):
        cleanup.execute(data)


def test_remote_tip_lease_rejects_changed_sha_before_delete(
    cleanup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    def fake_api(path: str, *, method: str = "GET") -> dict[str, Any]:
        calls.append((path, method))
        return {"object": {"sha": "e" * 40}}
    monkeypatch.setattr(cleanup, "api", fake_api)
    with pytest.raises(cleanup.RetirementBlocked, match="moved"):
        cleanup.retire("round/001-cleanup-probe", "f" * 40)
    assert len(calls) == 1
    assert calls[0][1] == "GET"


def test_delete_is_only_called_for_sha_matching_lease(
    cleanup, monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []
    def fake_api(path: str, *, method: str = "GET") -> dict[str, Any]:
        calls.append((path, method))
        return {"object": {"sha": "e" * 40}}
    monkeypatch.setattr(cleanup, "api", fake_api)
    cleanup.retire("round/001-cleanup-probe", "e" * 40)
    assert [method for _, method in calls] == ["GET", "DELETE"]
    assert calls[0][0].endswith("/git/ref/heads/round/001-cleanup-probe")
    assert calls[1][0].endswith("/git/refs/heads/round/001-cleanup-probe")
