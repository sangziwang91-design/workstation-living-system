"""WLS-private memory and credentials must not be model-readable by path."""

from __future__ import annotations

from pathlib import Path

import pytest

from wls.config import default_config
from wls.policy import PolicyEngine
from wls.schemas import ActionSpec, RiskLevel
from wls.tools import ToolRegistry


def action(tool: str, path: Path, risk: RiskLevel = RiskLevel.READ) -> ActionSpec:
    return ActionSpec(
        tool=tool,
        arguments={"path": str(path)},
        purpose="regression probe of private state isolation",
        expected_result="deny private state, allow only task data",
        risk=risk,
    )


def test_default_read_roots_only_include_task_data(tmp_path: Path) -> None:
    cfg = default_config(tmp_path / "home")
    expected = {
        str(cfg.inbox_path), str(cfg.outbox_path), str(cfg.sandbox_path),
    }
    assert set(cfg.tool_policy["allowed_read_roots"]) == expected
    assert str(cfg.home_path) not in expected
    assert str(cfg.home_path.parent) not in expected


@pytest.mark.parametrize(
    "relative",
    [
        "secrets/evidence.key",
        "secrets/approval.key",
        "secrets/server.token",
        "state/wls.db",
        "logs/history.jsonl",
        "snapshots/backup.sqlite",
    ],
)
def test_legacy_broad_read_root_cannot_expose_private_runtime(
    tmp_path: Path, relative: str,
) -> None:
    cfg = default_config(tmp_path / "home")
    cfg.ensure_directories()
    target = cfg.home_path / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"private regression data (not a real credential)")
    # Model-controlled reads are still forbidden for *persisted older config*
    # even if the owner once permitted the entire parent directory.
    cfg.tool_policy["allowed_read_roots"] = [str(cfg.home_path.parent)]
    policy = PolicyEngine(cfg)
    for tool in ("read_file", "inspect_asset"):
        with pytest.raises(PermissionError, match="protected WLS private runtime path"):
            policy.validate_arguments(action(tool, target))
        outcome = ToolRegistry(policy).execute(action(tool, target))
        assert outcome.success is False
        assert not outcome.output
        assert "protected WLS private runtime path" in (outcome.error or "")


@pytest.mark.parametrize("relative", ["secrets", "state", "logs", "snapshots"])
def test_legacy_broad_list_directory_cannot_discover_private_state(
    tmp_path: Path, relative: str,
) -> None:
    cfg = default_config(tmp_path / "home")
    cfg.ensure_directories()
    cfg.tool_policy["allowed_read_roots"] = [str(cfg.home_path.parent)]
    policy = PolicyEngine(cfg)
    with pytest.raises(PermissionError, match="protected WLS private runtime path"):
        policy.validate_arguments(action("list_directory", cfg.home_path / relative))


def test_explicit_broad_write_root_does_not_allow_private_state_tampering(
    tmp_path: Path,
) -> None:
    cfg = default_config(tmp_path / "home")
    cfg.ensure_directories()
    cfg.tool_policy["allowed_write_roots"] = [str(cfg.home_path.parent)]
    for tool in ("write_file", "delete_file", "emit_note"):
        with pytest.raises(PermissionError, match="protected WLS private runtime path"):
            PolicyEngine(cfg).validate_arguments(
                action(tool, cfg.secret_path, RiskLevel.REVERSIBLE_WRITE)
            )


def test_agent_can_still_read_task_inbox_and_owner_granted_repo(
    tmp_path: Path,
) -> None:
    cfg = default_config(tmp_path / "home")
    cfg.ensure_directories()
    task = cfg.inbox_path / "request.txt"
    task.write_text("allowed task data", encoding="utf-8")
    registry = ToolRegistry(PolicyEngine(cfg))
    outcome = registry.execute(action("read_file", task))
    assert outcome.success is True
    assert outcome.output["text"] == "allowed task data"

    repo = tmp_path / "authorized-repo"
    repo.mkdir()
    instruction = repo / "AGENTS.md"
    instruction.write_text("test repo read scope", encoding="utf-8")
    assert registry.execute(action("read_file", instruction)).success is False
    cfg.tool_policy["allowed_read_roots"].append(str(repo))
    outcome = registry.execute(action("read_file", instruction))
    assert outcome.success is True
    assert outcome.output["text"] == "test repo read scope"


def test_symlink_to_secret_is_denied_even_from_task_inbox(tmp_path: Path) -> None:
    cfg = default_config(tmp_path / "home")
    cfg.ensure_directories()
    cfg.secret_path.write_text("private fixture", encoding="utf-8")
    alias = cfg.inbox_path / "payload.txt"
    try:
        alias.symlink_to(cfg.secret_path)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable on this Windows runner: {exc}")
    with pytest.raises(PermissionError, match="protected WLS private runtime path"):
        PolicyEngine(cfg).validate_arguments(action("read_file", alias))
    assert ToolRegistry(PolicyEngine(cfg)).execute(
        action("read_file", alias)
    ).success is False
