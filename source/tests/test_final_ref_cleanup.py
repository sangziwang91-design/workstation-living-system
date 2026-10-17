"""Negative controls for final merged-PR ref cleanup. No live GitHub calls."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "finalize_merged_pr_refs.py"


@pytest.fixture
def closer():
    spec = importlib.util.spec_from_file_location("wls_final_ref_cleanup_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_exact_three_evaluated_pr_heads_and_self(closer) -> None:
    assert closer.SOURCES == {
        "refactor/s1a-proven-unreferenced-modules-20261011": 107,
        "feat/r1-frozen-contract-preflight-20261011": 118,
        "feat/r1-real-windows-mutation-discovery-20261016": 119,
    }
    assert closer.SELF not in closer.SOURCES
    assert "main" not in closer.SOURCES


def test_deleting_remote_ref_rechecks_exact_sha(closer, monkeypatch) -> None:
    calls = []
    def fake_gh(path, *, method="GET"):
        calls.append((path, method))
        return {"object": {"sha": "a" * 40}}
    monkeypatch.setattr(closer, "gh", fake_gh)
    with pytest.raises(closer.RefCleanupBlocked, match="moved"):
        closer.delete_exact("refactor/s1a-proven-unreferenced-modules-20261011", "b" * 40)
    assert len(calls) == 1 and calls[0][1] == "GET"
    closer.delete_exact("refactor/s1a-proven-unreferenced-modules-20261011", "a" * 40)
    assert calls[-1][1] == "DELETE"


def test_no_pr_trigger_or_invalid_origin_can_pass(closer, monkeypatch) -> None:
    for event, ref in [("pull_request", "refs/heads/main"),
                       ("push", "refs/heads/something-other-than-main")]:
        monkeypatch.setenv("GITHUB_REPOSITORY", closer.REPO)
        monkeypatch.setenv("GITHUB_EVENT_NAME", event)
        monkeypatch.setenv("GITHUB_REF", ref)
        with pytest.raises(closer.RefCleanupBlocked, match="trusted main"):
            closer.preflight()


def test_unreviewed_new_branch_blocks_every_prune(closer, monkeypatch) -> None:
    monkeypatch.setenv("GITHUB_REPOSITORY", closer.REPO)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setattr(closer, "command", lambda *args: "a" * 40)
    monkeypatch.setattr(
        closer, "gh",
        lambda path, **kwargs: [{"name": "main"}, {"name": "unreviewed"}],
    )
    with pytest.raises(closer.RefCleanupBlocked, match="unexpected branch"):
        closer.preflight()


def test_cleanup_disallows_arbitrary_github_api_scope(closer) -> None:
    with pytest.raises(closer.RefCleanupBlocked):
        closer.gh("repos/other/other/git/ref/main")
    with pytest.raises(closer.RefCleanupBlocked):
        closer.gh(f"repos/{closer.REPO}/git/ref/main", method="PATCH")
