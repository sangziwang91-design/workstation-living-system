from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTION_PAGE_ID = "38940ff6ad6281b6bd69d700c9322d77"
NOTION_PAGE_URL = "https://app.notion.com/p/38940ff6ad6281b6bd69d700c9322d77"


def _load(relative_path: str) -> dict[str, Any]:
    value = json.loads((REPO_ROOT / relative_path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def test_evolution_chain_validator_passes() -> None:
    result = subprocess.run(
        [sys.executable, "source/scripts/validate_evolution_chain.py"],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["success"] is True
    assert report["error_count"] == 0
    assert report["primary_worker"] == "chatgpt_interactive"


def test_chatgpt_is_only_active_code_worker() -> None:
    registry = _load(".evolution/worker_registry.json")
    workers = registry["workers"]
    active_code_workers = [
        worker
        for worker in workers
        if worker["status"] == "ACTIVE"
        and worker["write_authority"] == "BRANCH_AND_PULL_REQUEST"
    ]
    assert [worker["worker_id"] for worker in active_code_workers] == [
        "chatgpt_interactive"
    ]

    pending_ids = {
        worker["worker_id"]
        for worker in workers
        if worker["status"] == "PENDING_NOT_ACTIVATED"
    }
    assert pending_ids == {
        "gemini_candidate",
        "claude_candidate",
        "glm_candidate",
        "deepseek_candidate",
    }


def test_notion_anchor_is_identical_across_contracts() -> None:
    current = _load(".evolution/CURRENT_CHAIN.json")
    sync_contract = _load(".evolution/notion_sync_contract.json")
    verification_packet = _load(
        ".evolution/queue/verification/CHAIN-BOOTSTRAP-001.json"
    )
    blocked_packet = _load(".evolution/queue/blocked/ET004-HANDOFF-001.json")

    anchors = [
        (current["notion_anchor"]["page_id"], current["notion_anchor"]["page_url"]),
        (sync_contract["notion"]["page_id"], sync_contract["notion"]["page_url"]),
        (
            verification_packet["context"]["notion_page_id"],
            verification_packet["context"]["notion_page_url"],
        ),
        (
            blocked_packet["context"]["notion_page_id"],
            blocked_packet["context"]["notion_page_url"],
        ),
    ]
    assert anchors == [(NOTION_PAGE_ID, NOTION_PAGE_URL)] * len(anchors)


def test_existing_et004_pr_is_not_duplicated() -> None:
    current = _load(".evolution/CURRENT_CHAIN.json")
    blocked_packet = _load(".evolution/queue/blocked/ET004-HANDOFF-001.json")

    assert current["current_target"]["pull_request"] == 8
    assert current["current_target"]["collision_policy"] == (
        "DO_NOT_CREATE_DUPLICATE_ET004_IMPLEMENTATION"
    )
    assert blocked_packet["status"] == "BLOCKED"
    assert "PR #8 implementation complete" in blocked_packet["dependencies"]
