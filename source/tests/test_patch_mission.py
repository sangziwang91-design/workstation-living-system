from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from wls import cli
from wls.config import default_config, save_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, Plan, RiskLevel, digest_json


def test_patch_mission_inspects_repo_instructions_with_real_read_action(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    config.max_actions_per_cycle = 4
    config.tool_policy["allowed_read_roots"] = [str(config.home_path)]
    runtime = LivingSystem(config)

    result = runtime.start_patch_mission(
        repo_path=repo,
        mission="Fix the failing greeting test",
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,goal_id FROM actions WHERE action_id=?",
        (result["action_id"],),
    )
    goal = runtime.goals.get(result["goal_id"])

    assert result["status"] == "STARTED"
    assert result["authority"]["writes_canonical_repo"] is False
    assert result["authority"]["push_or_pr_created"] is False
    assert result["repo_map"]["instruction_files"] == ["CONTRIBUTING.md", "README.md"]
    assert result["repo_map"]["test_hints"]
    assert result["next_action_candidate"]["risk_class"] == "read"
    assert result["outcomes"][0]["success"] is True
    assert "Run pytest before proposing a patch" in result["outcomes"][0]["output"]["text"]
    assert row["tool"] == "read_file"
    assert row["status"] == "SUCCEEDED"
    assert row["risk"] == "READ"
    assert json.loads(row["arguments_json"])["path"].endswith("CONTRIBUTING.md")
    assert goal is not None
    assert goal.source == "patch-mission"
    assert str(repo) in goal.description
    assert str(repo) in runtime.config.tool_policy["allowed_read_roots"]
    assert runtime.patch_missions()[0]["mission_id"] == result["mission_id"]
    runtime.db.close_all()

    restarted = LivingSystem(config)
    assert str(repo) in restarted.config.tool_policy["allowed_read_roots"]
    assert restarted.patch_missions()[0]["mission_id"] == result["mission_id"]


def test_patch_mission_without_instruction_file_lists_repo_root(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)

    result = runtime.start_patch_mission(
        repo_path=repo,
        mission="Understand where to patch startup behavior",
    )

    assert result["outcomes"][0]["success"] is True
    assert result["outcomes"][0]["output"]["items"][0]["name"] == "main.py"
    assert result["next_action_candidate"]["tool"] == "list_directory"


def test_patch_mission_step_inspects_target_file_with_real_read_action(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Fix test_demo",
        execute_first_action=False,
    )

    step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="inspect-file",
        target="tests/test_demo.py",
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json FROM actions WHERE action_id=?",
        (step["action_id"],),
    )

    assert step["mode"] == "inspect-file"
    assert step["outcomes"][0]["success"] is True
    assert "def test_demo" in step["outcomes"][0]["output"]["text"]
    assert row["tool"] == "read_file"
    assert row["status"] == "SUCCEEDED"
    assert row["risk"] == "READ"
    assert json.loads(row["arguments_json"])["path"].endswith("tests\\test_demo.py") or json.loads(row["arguments_json"])["path"].endswith("tests/test_demo.py")
    assert runtime.patch_missions()[0]["followups"][0]["step_id"] == step["step_id"]


def test_patch_mission_test_probe_waits_for_owner_approval(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Run tests before patching",
        execute_first_action=False,
    )

    step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (step["action_id"],),
    )

    assert step["mode"] == "test"
    assert step["authority"]["test_command_requires_policy"] is True
    assert step["authority"]["owner_approval_required_for_write_or_external"] is True
    assert step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "run_command"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert row["error"] in {"runtime is in read-only mode", "HIGH action requires approval"}
    args = json.loads(row["arguments_json"])
    assert args["command"] == ["python", "-m", "pytest"]
    assert args["cwd"] == str(repo.resolve())


def test_patch_mission_draft_patch_targets_outbox_and_waits_for_approval(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Draft the minimal patch",
        execute_first_action=False,
    )

    step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="draft-patch",
        draft="diff --git a/demo.py b/demo.py\n",
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert step["mode"] == "draft-patch"
    assert step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert str(runtime.config.outbox_path) in args["path"]
    assert str(repo.resolve()) not in args["path"]
    assert not Path(args["path"]).exists()


def test_patch_mission_from_approved_test_result_creates_outbox_draft_action(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Fix the failing greeting test",
        execute_first_action=False,
    )
    test_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        test_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves local pytest probe",
    )
    test_result = runtime.resume_action(test_step["action_id"])

    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=test_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (draft_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert test_result["status"] == "SUCCEEDED"
    assert test_result["output"]["returncode"] == 1
    assert draft_step["mode"] == "from-test-result"
    assert draft_step["source_action_id"] == test_step["action_id"]
    assert draft_step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert draft_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert "tests/test_demo.py" in args["content"]
    assert "Patch synthesis: synthesized_literal_diff" in args["content"]
    assert "Patch target: demo.py" in args["content"]
    assert "--- a/demo.py" in args["content"]
    assert "+++ b/demo.py" in args["content"]
    assert "-    return 'hi'" in args["content"]
    assert "+    return 'hello'" in args["content"]
    assert str(runtime.config.outbox_path) in args["path"]
    assert str(repo.resolve()) not in args["path"]
    assert not Path(args["path"]).exists()
    assert (repo / "demo.py").read_text(encoding="utf-8") == (
        "def greet():\n    return 'hi'\n"
    )


def test_patch_mission_apply_patch_waits_for_owner_then_reruns_green_tests(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Fix and verify the failing greeting test",
        execute_first_action=False,
    )
    test_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        test_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves local pytest probe",
    )
    failing_result = runtime.resume_action(test_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=test_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    draft_result = runtime.resume_action(draft_step["action_id"])

    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (apply_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert failing_result["output"]["returncode"] == 1
    assert draft_result["status"] == "SUCCEEDED"
    assert Path(draft_result["output"]["path"]).exists()
    assert apply_step["mode"] == "apply-patch"
    assert apply_step["source_action_id"] == draft_step["action_id"]
    assert apply_step["authority"]["writes_canonical_repo"] is True
    assert apply_step["authority"]["draft_patch_target"] is None
    assert apply_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert args["path"] == str((repo / "demo.py").resolve())
    assert "return 'hello'" in args["content"]
    assert (repo / "demo.py").read_text(encoding="utf-8") == (
        "def greet():\n    return 'hi'\n"
    )

    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    apply_result = runtime.resume_action(apply_step["action_id"])
    rerun_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        rerun_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves verification pytest",
    )
    rerun_result = runtime.resume_action(rerun_step["action_id"])

    assert apply_result["status"] == "SUCCEEDED"
    assert (repo / "demo.py").read_text(encoding="utf-8") == (
        "def greet():\n    return 'hello'\n"
    )
    assert rerun_result["output"]["returncode"] == 0


def test_patch_mission_pr_summary_uses_diff_and_test_evidence(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Fix and summarize the greeting patch",
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    failing_result = runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    apply_result = runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing pytest verification",
    )
    passing_result = runtime.resume_action(passing_step["action_id"])

    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (summary_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert failing_result["output"]["returncode"] == 1
    assert apply_result["status"] == "SUCCEEDED"
    assert passing_result["output"]["returncode"] == 0
    assert summary_step["mode"] == "pr-summary"
    assert summary_step["source_action_id"] == passing_step["action_id"]
    assert summary_step["authority"]["writes_canonical_repo"] is False
    assert summary_step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert summary_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert str(runtime.config.outbox_path) in args["path"]
    assert str(repo.resolve()) not in args["path"]
    assert "# Patch Mission PR Summary" in args["content"]
    assert "Changed files: demo.py" in args["content"]
    assert failing_step["action_id"] in args["content"]
    assert passing_step["action_id"] in args["content"]
    assert apply_step["action_id"] in args["content"]
    assert "-    return 'hi'" in args["content"]
    assert "+    return 'hello'" in args["content"]
    assert "No branch, commit, push, or pull request" in args["content"]
    assert not Path(args["path"]).exists()


def test_patch_mission_git_prep_reads_metadata_and_writes_outbox_checklist(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Prepare the verified greeting patch for local commit",
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])

    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-prep",
        action_id=metadata_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (prep_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])
    commit_count = git_output(repo, ["rev-list", "--count", "HEAD"])

    assert metadata_step["mode"] == "git-metadata"
    assert metadata_step["outcomes"][0]["success"] is True
    metadata = metadata_step["outcomes"][0]["output"]
    assert metadata["branch_status"]["returncode"] == 0
    assert "demo.py" in metadata["branch_status"]["stdout"]
    assert "demo.py" in metadata["diff_stat"]["stdout"]
    assert prep_step["mode"] == "git-prep"
    assert prep_step["source_action_id"] == metadata_step["action_id"]
    assert prep_step["authority"]["writes_canonical_repo"] is False
    assert prep_step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert prep_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert str(runtime.config.outbox_path) in args["path"]
    assert str(repo.resolve()) not in args["path"]
    assert "# Patch Mission Commit-Ready Checklist" in args["content"]
    assert metadata_step["action_id"] in args["content"]
    assert "demo.py" in args["content"]
    assert "No commit, branch creation, push, or pull request" in args["content"]
    assert not Path(args["path"]).exists()
    assert commit_count.strip() == "1"


def test_patch_mission_commit_draft_waits_for_owner_then_commits_locally(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Commit the verified greeting patch locally",
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing pytest verification",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    runtime.approvals.issue(
        summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR summary outbox write",
    )
    runtime.resume_action(summary_step["action_id"])
    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-prep",
        action_id=metadata_step["action_id"],
    )
    runtime.approvals.issue(
        prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves commit checklist outbox write",
    )
    runtime.resume_action(prep_step["action_id"])

    commit_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="commit-draft",
        action_id=prep_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (commit_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])
    command = args["command"]
    before_count = git_output(repo, ["rev-list", "--count", "HEAD"]).strip()

    assert commit_step["mode"] == "commit-draft"
    assert commit_step["source_action_id"] == prep_step["action_id"]
    assert commit_step["authority"]["writes_canonical_repo"] is False
    assert commit_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "run_command"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert command[:5] == [
        "git",
        "-c",
        "user.name=WLS",
        "-c",
        "user.email=wls@example.invalid",
    ]
    assert "commit" in command
    assert "-am" in command
    assert "push" not in command
    assert "branch" not in command
    assert before_count == "1"

    runtime.approvals.issue(
        commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local commit",
    )
    commit_result = runtime.resume_action(commit_step["action_id"])
    after_count = git_output(repo, ["rev-list", "--count", "HEAD"]).strip()
    last_message = git_output(repo, ["log", "-1", "--pretty=%B"])
    tracked_diff = git_output(repo, ["diff", "--name-only"])

    assert commit_result["status"] == "SUCCEEDED"
    assert commit_result["output"]["returncode"] == 0
    assert after_count == "2"
    assert "Commit the verified greeting patch locally" in last_message
    assert "demo.py" in last_message
    assert tracked_diff.strip() == ""


def test_patch_mission_remote_summary_reads_local_remote_metadata_only(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    git_check(repo, ["remote", "add", "origin", "https://github.com/example/demo.git"])
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Inspect remote context before any push",
        execute_first_action=False,
    )

    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-summary",
        action_id=metadata_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (summary_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])
    branches = git_output(repo, ["branch", "--list"]).strip()
    remotes = git_output(repo, ["remote", "-v"])

    assert metadata_step["mode"] == "git-metadata"
    assert metadata_step["outcomes"][0]["success"] is True
    metadata = metadata_step["outcomes"][0]["output"]
    assert "https://github.com/example/demo.git" in metadata["remotes"]["stdout"]
    assert metadata["last_commit"]["returncode"] == 0
    assert summary_step["mode"] == "remote-summary"
    assert summary_step["source_action_id"] == metadata_step["action_id"]
    assert summary_step["authority"]["writes_canonical_repo"] is False
    assert summary_step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert summary_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert str(runtime.config.outbox_path) in args["path"]
    assert "# Patch Mission Remote Readiness Summary" in args["content"]
    assert "https://github.com/example/demo.git" in args["content"]
    assert metadata_step["action_id"] in args["content"]
    assert "No fetch, branch creation, commit, push, or pull request" in args["content"]
    assert not Path(args["path"]).exists()
    assert branches.startswith("*")
    assert "https://github.com/example/demo.git" in remotes


def test_patch_mission_branch_draft_waits_for_owner_then_creates_local_branch(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    git_check(repo, ["remote", "add", "origin", "https://github.com/example/demo.git"])
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Create a local branch for the verified greeting patch",
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing pytest verification",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    runtime.approvals.issue(
        summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR summary outbox write",
    )
    runtime.resume_action(summary_step["action_id"])
    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-prep",
        action_id=metadata_step["action_id"],
    )
    runtime.approvals.issue(
        prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves commit checklist outbox write",
    )
    runtime.resume_action(prep_step["action_id"])
    commit_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="commit-draft",
        action_id=prep_step["action_id"],
    )
    runtime.approvals.issue(
        commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local commit",
    )
    runtime.resume_action(commit_step["action_id"])
    remote_metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    remote_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-summary",
        action_id=remote_metadata_step["action_id"],
    )
    runtime.approvals.issue(
        remote_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves remote summary outbox write",
    )
    runtime.resume_action(remote_summary_step["action_id"])

    branch_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="branch-draft",
        action_id=remote_summary_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (branch_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])
    command = args["command"]
    branch_name = command[-1]
    before_branch = git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    before_count = git_output(repo, ["rev-list", "--count", "HEAD"]).strip()

    assert branch_step["mode"] == "branch-draft"
    assert branch_step["source_action_id"] == remote_summary_step["action_id"]
    assert branch_step["authority"]["writes_canonical_repo"] is False
    assert branch_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "run_command"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert command[:3] == ["git", "checkout", "-b"]
    assert branch_name.startswith("wls/")
    assert "push" not in command
    assert git_output(repo, ["branch", "--list", branch_name]).strip() == ""
    assert before_count == "2"

    runtime.approvals.issue(
        branch_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local branch creation",
    )
    branch_result = runtime.resume_action(branch_step["action_id"])
    after_branch = git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    after_count = git_output(repo, ["rev-list", "--count", "HEAD"]).strip()

    assert branch_result["status"] == "SUCCEEDED"
    assert branch_result["output"]["returncode"] == 0
    assert before_branch != branch_name
    assert after_branch == branch_name
    assert after_count == "2"
    assert "https://github.com/example/demo.git" in git_output(repo, ["remote", "-v"])


def test_patch_mission_remote_live_waits_for_owner_and_summarizes_refs(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    remote = initialize_bare_remote(tmp_path / "remote.git", repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Inspect live remote refs before push",
        execute_first_action=False,
    )

    live_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-live",
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (live_step["action_id"],),
    )
    before_branch = git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip()
    before_count = git_output(repo, ["rev-list", "--count", "HEAD"]).strip()

    assert live_step["mode"] == "remote-live"
    assert live_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "inspect_git_remote_live"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"

    runtime.approvals.issue(
        live_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only live remote inspection",
    )
    live_result = runtime.resume_action(live_step["action_id"])
    output = live_result["output"]
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-live-summary",
        action_id=live_step["action_id"],
    )
    summary_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (summary_step["action_id"],),
    )
    summary_args = json.loads(summary_row["arguments_json"])

    assert live_result["status"] == "SUCCEEDED"
    assert live_result["success"] is True
    assert str(remote) in output["remote_url"]["stdout"]
    assert "refs/heads/" in output["remote_heads"]["stdout"]
    assert output["github"]["available"] is False
    assert summary_step["mode"] == "remote-live-summary"
    assert summary_step["source_action_id"] == live_step["action_id"]
    assert summary_step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert summary_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert summary_row["tool"] == "write_file"
    assert summary_row["status"] == "WAITING_APPROVAL"
    assert summary_row["risk"] == "REVERSIBLE_WRITE"
    assert "# Patch Mission Live Remote Inspection" in summary_args["content"]
    assert "refs/heads/" in summary_args["content"]
    assert "No fetch, branch creation, commit, push, or pull request" in summary_args["content"]
    assert not Path(summary_args["path"]).exists()
    assert git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip() == before_branch
    assert git_output(repo, ["rev-list", "--count", "HEAD"]).strip() == before_count


def test_patch_mission_push_draft_waits_for_owner_then_pushes_wls_branch(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    remote = initialize_bare_remote(tmp_path / "remote.git", repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Push the verified greeting patch branch",
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing pytest verification",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    runtime.approvals.issue(
        summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR summary outbox write",
    )
    runtime.resume_action(summary_step["action_id"])
    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-prep",
        action_id=metadata_step["action_id"],
    )
    runtime.approvals.issue(
        prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves commit checklist outbox write",
    )
    runtime.resume_action(prep_step["action_id"])
    commit_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="commit-draft",
        action_id=prep_step["action_id"],
    )
    runtime.approvals.issue(
        commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local commit",
    )
    runtime.resume_action(commit_step["action_id"])
    remote_metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    remote_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-summary",
        action_id=remote_metadata_step["action_id"],
    )
    runtime.approvals.issue(
        remote_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves remote summary outbox write",
    )
    runtime.resume_action(remote_summary_step["action_id"])
    branch_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="branch-draft",
        action_id=remote_summary_step["action_id"],
    )
    branch_command = branch_step["action"]["arguments"]["command"]
    branch_name = branch_command[-1]
    runtime.approvals.issue(
        branch_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local branch creation",
    )
    runtime.resume_action(branch_step["action_id"])
    live_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-live",
    )
    runtime.approvals.issue(
        live_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves live remote inspection",
    )
    runtime.resume_action(live_step["action_id"])
    live_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-live-summary",
        action_id=live_step["action_id"],
    )
    runtime.approvals.issue(
        live_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves live remote summary outbox write",
    )
    runtime.resume_action(live_summary_step["action_id"])

    push_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="push-draft",
        action_id=live_summary_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (push_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])
    command = args["command"]
    before_remote = git_check(
        repo, ["ls-remote", "--heads", str(remote), branch_name]
    ).stdout

    assert push_step["mode"] == "push-draft"
    assert push_step["source_action_id"] == live_summary_step["action_id"]
    assert push_step["authority"]["writes_canonical_repo"] is False
    assert push_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "run_command"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert command == ["git", "push", "-u", "origin", branch_name]
    assert branch_name.startswith("wls/")
    assert "pull-request" not in " ".join(command)
    assert before_remote.strip() == ""

    runtime.approvals.issue(
        push_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact branch push",
    )
    push_result = runtime.resume_action(push_step["action_id"])
    after_remote = git_check(
        repo, ["ls-remote", "--heads", str(remote), branch_name]
    ).stdout

    assert push_result["status"] == "SUCCEEDED"
    assert push_result["output"]["returncode"] == 0
    assert branch_name in after_remote
    assert git_output(repo, ["rev-parse", "--abbrev-ref", "HEAD"]).strip() == branch_name


def test_patch_mission_pr_create_draft_waits_for_owner_after_pushed_branch(
    tmp_path: Path,
) -> None:
    runtime, mission, repo, remote, branch_name, push_step = prepare_pushed_patch_branch(
        tmp_path,
        mission="Open a draft PR for the verified greeting patch",
    )
    git_check(repo, ["remote", "set-url", "origin", "https://github.com/example/demo.git"])
    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    remote_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="remote-summary",
        action_id=metadata_step["action_id"],
    )
    runtime.approvals.issue(
        remote_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves GitHub remote evidence summary",
    )
    runtime.resume_action(remote_summary_step["action_id"])

    pr_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-create-draft",
        action_id=push_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (pr_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])
    pushed_remote = git_check(repo, ["ls-remote", "--heads", str(remote), branch_name]).stdout

    assert pr_step["mode"] == "pr-create-draft"
    assert pr_step["source_action_id"] == push_step["action_id"]
    assert pr_step["authority"]["push_or_pr_created"] is False
    assert pr_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "create_github_pull_request"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert row["error"] == "runtime is in read-only mode"
    assert args["owner"] == "example"
    assert args["repo"] == "demo"
    assert args["head"] == branch_name
    assert args["base"] == "master"
    assert args["draft"] is True
    assert args["token_env"] == "GITHUB_TOKEN"
    assert args["api_url"] == "https://api.github.com"
    assert "verified greeting patch" in args["title"]
    assert "# Patch Mission PR Summary" in args["body"]
    assert push_step["action_id"] in args["body"]
    assert branch_name in args["body"]
    assert branch_name in pushed_remote


def test_patch_mission_pr_status_from_url_waits_for_owner_before_github_read(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Inspect PR CI status for a submitted patch",
        execute_first_action=False,
    )

    status_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (status_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert status_step["mode"] == "pr-status"
    assert status_step["target"] == "https://github.com/example/demo/pull/7"
    assert status_step["authority"]["push_or_pr_created"] is False
    assert status_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "inspect_github_pr_status"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert row["error"] == "runtime is in read-only mode"
    assert args == {
        "owner": "example",
        "repo": "demo",
        "number": 7,
        "token_env": "GITHUB_TOKEN",
        "api_url": "https://api.github.com",
    }
    continuity = runtime.patch_missions()[0]["continuity"]
    assert continuity["state"] == "waiting_approval"
    assert continuity["blocking_action_id"] == status_step["action_id"]
    assert continuity["blocking_mode"] == "pr-status"


def test_patch_mission_ci_fix_plan_consumes_pr_status_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Plan a local repair from failed PR CI",
        execute_first_action=False,
    )

    def fake_get(url: str, *, token: str | None = None) -> dict[str, object]:
        if (
            url.endswith("/pulls/7")
            or url.endswith("/pulls/8")
            or url.endswith("/pulls/9")
        ):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"head": {"sha": "abc123", "ref": "wls/fix-greeting"}},
            }
        if "/check-runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "check_runs": [
                        {
                            "name": "pytest",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": "https://github.com/example/demo/actions/runs/1",
                            "output": {
                                "title": "pytest failed",
                                "summary": "tests/test_demo.py::test_demo failed",
                            },
                        }
                    ]
                },
            }
        if url.endswith("/status"):
            return {"ok": True, "status": 200, "url": url, "body": {"statuses": []}}
        if "/actions/runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"workflow_runs": []},
            }
        raise AssertionError(url)

    monkeypatch.setattr(
        runtime.tools,
        "_github_api_get_with_optional_token",
        fake_get,
    )
    status_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status inspection",
    )
    status_result = runtime.resume_action(status_step["action_id"])

    ci_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="ci-fix-plan",
        action_id=status_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (ci_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert status_result["status"] == "SUCCEEDED"
    assert ci_step["mode"] == "ci-fix-plan"
    assert ci_step["source_action_id"] == status_step["action_id"]
    assert ci_step["authority"]["draft_patch_target"] == "wls_outbox_only"
    assert ci_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert row["error"] == "runtime is in read-only mode"
    assert "# Patch Mission CI Fix Plan" in args["content"]
    assert "tests/test_demo.py" in args["content"]
    assert "Mode: `test`" in args["content"]
    assert "wls patch-mission-step --mode test" in args["content"]
    assert "does not modify the repo, push, comment, review, merge" in args["content"]
    assert not Path(args["path"]).exists()


def test_patch_mission_resume_next_captures_ci_logs_before_fix_plan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Use failed CI logs before planning a local repair",
        execute_first_action=False,
    )

    def fake_get(url: str, *, token: str | None = None) -> dict[str, object]:
        if (
            url.endswith("/pulls/7")
            or url.endswith("/pulls/8")
            or url.endswith("/pulls/9")
        ):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"head": {"sha": "abc123", "ref": "wls/fix-greeting"}},
            }
        if "/check-runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"check_runs": []},
            }
        if url.endswith("/status"):
            return {"ok": True, "status": 200, "url": url, "body": {"statuses": []}}
        if url.endswith("/actions/runs?branch=wls%2Ffix-greeting&per_page=10"):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "workflow_runs": [
                        {
                            "id": 101,
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/example/demo/actions/runs/101",
                        }
                    ]
                },
            }
        if url.endswith("/actions/runs/101/jobs?per_page=20"):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "jobs": [
                        {
                            "id": 202,
                            "name": "pytest",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/example/demo/actions/runs/101/job/202",
                        }
                    ]
                },
            }
        raise AssertionError(url)

    def fake_log(
        url: str, *, token: str | None, max_bytes: int
    ) -> dict[str, object]:
        return {
            "ok": True,
            "url": url,
            "status": 200,
            "text": (
                "tests/test_demo.py::test_demo FAILED\n"
                "E       AssertionError: assert 'hi' == 'hello'\n"
                "FAILED tests/test_demo.py::test_demo\n"
            ),
            "bytes": 128,
            "truncated": False,
        }

    monkeypatch.setattr(
        runtime.tools,
        "_github_api_get_with_optional_token",
        fake_get,
    )
    monkeypatch.setattr(runtime.tools, "_github_actions_job_log", fake_log)

    status_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status inspection",
    )
    status_result = runtime.resume_action(status_step["action_id"])
    log_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    log_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (log_step["action_id"],),
    )
    log_args = json.loads(log_row["arguments_json"])

    assert status_result["status"] == "SUCCEEDED"
    assert log_step["mode"] == "ci-log-evidence"
    assert log_step["requested_mode"] == "resume-next"
    assert log_step["source_action_id"] == status_step["action_id"]
    assert log_row["tool"] == "inspect_github_ci_logs"
    assert log_row["status"] == "WAITING_APPROVAL"
    assert log_row["risk"] == "HIGH"
    assert log_args["workflow_run_ids"] == [101]

    runtime.approvals.issue(
        log_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only CI log evidence capture",
    )
    log_result = runtime.resume_action(log_step["action_id"])
    ci_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    ci_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (ci_step["action_id"],),
    )
    ci_args = json.loads(ci_row["arguments_json"])

    assert log_result["status"] == "SUCCEEDED"
    assert log_result["output"]["log_count"] == 1
    assert log_result["output"]["failure_clues"][0]["job_id"] == 202
    assert ci_step["mode"] == "ci-fix-plan"
    assert ci_step["requested_mode"] == "resume-next"
    assert ci_step["source_action_id"] == log_step["action_id"]
    assert ci_row["tool"] == "write_file"
    assert ci_row["status"] == "WAITING_APPROVAL"
    assert ci_row["risk"] == "REVERSIBLE_WRITE"
    assert f"CI log evidence action: {log_step['action_id']}" in ci_args["content"]
    assert "tests/test_demo.py::test_demo FAILED" in ci_args["content"]
    assert "Mode: `test`" in ci_args["content"]
    assert "Target: `tests/test_demo.py::test_demo`" in ci_args["content"]
    assert not Path(ci_args["path"]).exists()

    runtime.approvals.issue(
        ci_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI fix plan with narrow pytest target",
    )
    runtime.resume_action(ci_step["action_id"])
    next_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    next_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (next_step["action_id"],),
    )
    next_args = json.loads(next_row["arguments_json"])

    assert next_step["mode"] == "ci-next-action"
    assert next_step["requested_mode"] == "resume-next"
    assert next_step["source_action_id"] == ci_step["action_id"]
    assert next_step["authority"]["test_command_requires_policy"] is True
    assert next_row["tool"] == "run_command"
    assert next_row["status"] == "WAITING_APPROVAL"
    assert next_row["risk"] == "HIGH"
    assert next_args["command"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_demo.py::test_demo",
    ]


def test_patch_mission_ci_next_action_creates_selected_local_test_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Execute the local repair action from failed PR CI",
        execute_first_action=False,
    )

    def fake_get(url: str, *, token: str | None = None) -> dict[str, object]:
        if url.endswith("/pulls/7"):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"head": {"sha": "abc123", "ref": "wls/fix-greeting"}},
            }
        if "/check-runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "check_runs": [
                        {
                            "name": "pytest",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": "https://github.com/example/demo/actions/runs/1",
                            "output": {
                                "title": "pytest failed",
                                "summary": "tests/test_demo.py::test_demo failed",
                            },
                        }
                    ]
                },
            }
        if url.endswith("/status"):
            return {"ok": True, "status": 200, "url": url, "body": {"statuses": []}}
        if "/actions/runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"workflow_runs": []},
            }
        raise AssertionError(url)

    monkeypatch.setattr(
        runtime.tools,
        "_github_api_get_with_optional_token",
        fake_get,
    )
    status_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status inspection",
    )
    runtime.resume_action(status_step["action_id"])
    plan_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="ci-fix-plan",
        action_id=status_step["action_id"],
    )
    runtime.approvals.issue(
        plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI fix plan outbox write",
    )
    runtime.resume_action(plan_step["action_id"])

    next_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="ci-next-action",
        action_id=plan_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (next_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert next_step["mode"] == "ci-next-action"
    assert next_step["source_action_id"] == plan_step["action_id"]
    assert next_step["authority"]["test_command_requires_policy"] is True
    assert next_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "run_command"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert row["error"] == "runtime is in read-only mode"
    assert args["command"] == ["python", "-m", "pytest", "tests/test_demo.py::test_demo"]
    assert args["cwd"] == str(repo)

    runtime.approvals.issue(
        next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-triggered local pytest reproduction",
    )
    next_result = runtime.resume_action(next_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=next_step["action_id"],
    )
    draft_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (draft_step["action_id"],),
    )
    draft_args = json.loads(draft_row["arguments_json"])

    assert next_result["status"] == "SUCCEEDED"
    assert next_result["output"]["returncode"] != 0
    assert draft_step["mode"] == "from-test-result"
    assert draft_step["source_action_id"] == next_step["action_id"]
    assert draft_row["tool"] == "write_file"
    assert draft_row["status"] == "WAITING_APPROVAL"
    assert "PR/CI source evidence:" in draft_args["content"]
    assert "https://github.com/example/demo/pull/7" in draft_args["content"]
    assert "ci-fix-plan" in draft_args["content"]
    assert "tests/test_demo.py" in draft_args["content"]
    assert "Failure learning evidence:" in draft_args["content"]
    assert "observed value 'hi' did not match expected 'hello'" in draft_args["content"]
    assert "Rerun `python -m pytest tests/test_demo.py::test_demo`" in draft_args["content"]
    learning_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_failure_learning_recorded'"
    )
    assert len(learning_rows) == 1
    learning_payload = json.loads(learning_rows[0]["payload_json"])
    assert learning_payload["test_action_id"] == next_step["action_id"]
    assert learning_payload["pytest_target"] == "tests/test_demo.py::test_demo"
    assert learning_payload["patch_target"] == "demo.py"
    memory_row = runtime.db.query_one(
        "SELECT memory_type,content_json FROM memories WHERE memory_id=?",
        (learning_payload["memory_id"],),
    )
    assert memory_row["memory_type"] == "procedural"
    memory_content = json.loads(memory_row["content_json"])
    assert memory_content["kind"] == "patch_mission_failure_learning"
    assert memory_content["synthesis_status"] == "synthesized_literal_diff"

    second_mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Use prior failure learning for the next similar CI repair",
        execute_first_action=False,
    )
    second_status_step = runtime.continue_patch_mission(
        mission_id=second_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        second_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves second read-only PR status inspection",
    )
    runtime.resume_action(second_status_step["action_id"])
    second_plan_step = runtime.continue_patch_mission(
        mission_id=second_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=second_status_step["action_id"],
    )
    second_plan_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (second_plan_step["action_id"],),
        )["arguments_json"]
    )
    assert "Prior Failure-Learning Memory" in second_plan_args["content"]
    assert learning_payload["memory_id"] in second_plan_args["content"]

    runtime.approvals.issue(
        second_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves second CI fix plan",
    )
    runtime.resume_action(second_plan_step["action_id"])
    second_next_step = runtime.continue_patch_mission(
        mission_id=second_mission["mission_id"],
        mode="ci-next-action",
        action_id=second_plan_step["action_id"],
    )
    second_next_row = runtime.db.query_one(
        "SELECT status FROM actions WHERE action_id=?", (second_next_step["action_id"],)
    )
    if second_next_row["status"] == "WAITING_APPROVAL":
        runtime.approvals.issue(
            second_next_step["action_id"],
            approve=True,
            ttl_minutes=30,
            reason="owner approves second narrow pytest reproduction",
        )
        runtime.resume_action(second_next_step["action_id"])
    else:
        assert second_next_row["status"] == "SUCCEEDED"
    second_draft_step = runtime.continue_patch_mission(
        mission_id=second_mission["mission_id"],
        mode="from-test-result",
        action_id=second_next_step["action_id"],
    )
    second_draft_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (second_draft_step["action_id"],),
        )["arguments_json"]
    )
    assert "Prior failure-learning memory used:" in second_draft_args["content"]
    assert learning_payload["memory_id"] in second_draft_args["content"]

    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-sourced outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-sourced exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing local verification after CI fix",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    summary_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (summary_step["action_id"],),
    )
    summary_args = json.loads(summary_row["arguments_json"])

    assert summary_row["tool"] == "write_file"
    assert summary_row["status"] == "WAITING_APPROVAL"
    assert "PR/CI source:" in summary_args["content"]
    assert "https://github.com/example/demo/pull/7" in summary_args["content"]
    assert next_step["action_id"] in summary_args["content"]


def test_patch_mission_repeated_success_creates_repair_skill_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)

    def fake_get(url: str, *, token: str | None = None) -> dict[str, object]:
        if (
            url.endswith("/pulls/7")
            or url.endswith("/pulls/8")
            or url.endswith("/pulls/9")
        ):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"head": {"sha": "abc123", "ref": "wls/fix-greeting"}},
            }
        if "/check-runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "check_runs": [
                        {
                            "name": "pytest",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": "https://github.com/example/demo/actions/runs/1",
                            "output": {
                                "title": "pytest failed",
                                "summary": "tests/test_demo.py::test_demo failed",
                            },
                        }
                    ]
                },
            }
        if url.endswith("/status"):
            return {"ok": True, "status": 200, "url": url, "body": {"statuses": []}}
        if "/actions/runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"workflow_runs": []},
            }
        raise AssertionError(url)

    monkeypatch.setattr(
        runtime.tools,
        "_github_api_get_with_optional_token",
        fake_get,
    )

    first_summary = run_ci_literal_repair_to_summary(
        runtime,
        repo=make_failing_repo(tmp_path / "project-a"),
        mission="Repair first literal mismatch from failed PR CI",
    )
    first_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (first_summary["action_id"],),
        )["arguments_json"]
    )

    assert "## Skill Candidate" not in first_args["content"]

    second_summary = run_ci_literal_repair_to_summary(
        runtime,
        repo=make_failing_repo(tmp_path / "project-b"),
        mission="Repair second literal mismatch from failed PR CI",
    )
    second_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (second_summary["action_id"],),
        )["arguments_json"]
    )
    candidate_rows = runtime.db.query_all(
        "SELECT * FROM evolution_candidates WHERE candidate_type='patch_mission_repair_skill'"
    )
    skill_rows = runtime.db.query_all("SELECT * FROM skills")
    success_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_success_recorded'"
    )

    assert len(success_rows) == 2
    assert len(candidate_rows) == 1
    assert skill_rows == []
    candidate = candidate_rows[0]
    assert candidate["status"] == "PROPOSED"
    proposal = json.loads(candidate["proposal_json"])["proposal"]
    source_ids = json.loads(candidate["source_ids_json"])
    assert proposal["candidate_only"] is True
    assert proposal["owner_review_required"] is True
    assert proposal["pattern_family"] == "patch_mission_pytest_literal_mismatch_repair"
    assert proposal["observed_success_count"] == 2
    assert "No commit, push, PR creation, or skill promotion" in " ".join(
        proposal["risks"]
    )
    assert len(source_ids) >= 10
    assert "## Skill Candidate" in second_args["content"]
    assert candidate["candidate_id"] in second_args["content"]
    assert "proposed candidate only" in second_args["content"]

    replay = runtime.review_patch_mission_repair_skill_candidate(
        candidate_id=candidate["candidate_id"],
        reason="owner reviews repeated Patch Mission literal repair candidate",
    )
    reviewed_candidate = runtime.db.query_one(
        "SELECT status FROM evolution_candidates WHERE candidate_id=?",
        (candidate["candidate_id"],),
    )
    review_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_candidate_replayed'"
    )

    assert replay["status"] == "REPLAY_PASSED"
    assert replay["candidate_status_before"] == "PROPOSED"
    assert replay["candidate_status_after"] == "PROPOSED"
    assert replay["passed_sample_count"] == 2
    assert replay["promotion_executed"] is False
    assert replay["active_skill_created"] is False
    assert replay["command_executed"] is False
    assert replay["external_action_executed"] is False
    assert reviewed_candidate["status"] == "PROPOSED"
    assert runtime.db.query_all("SELECT * FROM skills") == []
    assert len(review_rows) == 1

    with pytest.raises(PermissionError):
        runtime.sandbox_patch_mission_repair_skill_candidate(
            candidate_id=candidate["candidate_id"],
            reason="missing owner approval",
        )
    sandbox = runtime.sandbox_patch_mission_repair_skill_candidate(
        candidate_id=candidate["candidate_id"],
        reason="owner validates repair candidate in disposable sandbox",
        owner_approved=True,
    )
    sandboxed_candidate = runtime.db.query_one(
        "SELECT status,experiment_json FROM evolution_candidates WHERE candidate_id=?",
        (candidate["candidate_id"],),
    )
    sandbox_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_candidate_sandboxed'"
    )

    assert sandbox["status"] == "SANDBOX_PASSED"
    assert sandbox["candidate_status_before"] == "PROPOSED"
    assert sandbox["candidate_status_after"] == "SANDBOXED"
    assert sandbox["command_executed"] is True
    assert sandbox["sandbox_only"] is True
    assert sandbox["canonical_repo_write_executed"] is False
    assert sandbox["external_action_executed"] is False
    assert sandbox["promotion_executed"] is False
    assert sandbox["active_skill_created"] is False
    assert sandbox["result"]["failing_returncode"] != 0
    assert sandbox["result"]["passing_returncode"] == 0
    assert sandbox["result"]["synthesis_status"] == "synthesized_literal_diff"
    assert sandboxed_candidate["status"] == "SANDBOXED"
    assert json.loads(sandboxed_candidate["experiment_json"])["experiment_id"] == sandbox["experiment_id"]
    assert Path(sandbox["manifest_path"]).is_file()
    assert Path(sandbox["result_path"]).is_file()
    assert str(runtime.config.sandbox_path) in sandbox["manifest_path"]
    assert runtime.db.query_all("SELECT * FROM skills") == []
    assert len(sandbox_rows) == 1

    with pytest.raises(PermissionError):
        runtime.validate_patch_mission_repair_skill_candidate(
            candidate_id=candidate["candidate_id"],
            reason="missing human approval",
        )
    validation = runtime.validate_patch_mission_repair_skill_candidate(
        candidate_id=candidate["candidate_id"],
        reason="human validates sandboxed repair candidate",
        human_approved=True,
    )
    validated_candidate = runtime.db.query_one(
        "SELECT status,result_json FROM evolution_candidates WHERE candidate_id=?",
        (candidate["candidate_id"],),
    )
    validation_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_candidate_validated'"
    )

    assert validation["status"] == "VALIDATED"
    assert validation["candidate_status_before"] == "SANDBOXED"
    assert validation["candidate_status_after"] == "VALIDATED"
    assert validation["human_approved"] is True
    assert validation["command_executed"] is False
    assert validation["canonical_repo_write_executed"] is False
    assert validation["external_action_executed"] is False
    assert validation["promotion_executed"] is False
    assert validation["active_skill_created"] is False
    assert validation["validated_result"]["passed"] is True
    assert validation["manifest_sha256"] == sandbox["manifest_sha256"]
    assert validation["result_sha256"] == sandbox["result_sha256"]
    assert validated_candidate["status"] == "VALIDATED"
    persisted_validation = json.loads(validated_candidate["result_json"])
    assert persisted_validation["receipt_digest"] == validation["receipt_digest"]
    assert runtime.db.query_all("SELECT * FROM skills") == []
    assert len(validation_rows) == 1

    skill_receipt = runtime.propose_patch_mission_repair_skill_from_candidate(
        candidate_id=candidate["candidate_id"],
        reason="owner wants an inspectable declarative skill proposal",
    )
    skill_rows = runtime.db.query_all("SELECT * FROM skills")
    proposed_skill = skill_rows[0]
    skill_definition = json.loads(proposed_skill["definition_json"])
    proposal_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_proposed'"
    )

    assert skill_receipt["status"] == "SKILL_PROPOSED"
    assert skill_receipt["candidate_id"] == candidate["candidate_id"]
    assert skill_receipt["candidate_status"] == "VALIDATED"
    assert skill_receipt["active_skill_created"] is False
    assert skill_receipt["promotion_executed"] is False
    assert proposed_skill["status"] == "PROPOSED"
    assert skill_definition["status"] == "PROPOSED"
    assert skill_definition["risk"] == "HIGH"
    assert candidate["candidate_id"] in skill_definition["source_episode_ids"]
    assert validation["receipt_digest"] in skill_definition["source_episode_ids"]
    assert len(skill_definition["steps"]) >= 6
    assert [step["tool"] for step in skill_definition["steps"]][:3] == [
        "inspect_github_pr_status",
        "inspect_github_ci_logs",
        "run_command",
    ]
    assert runtime.skills.active() == []
    assert len(proposal_rows) == 1

    duplicate_receipt = runtime.propose_patch_mission_repair_skill_from_candidate(
        candidate_id=candidate["candidate_id"],
        reason="owner repeats proposal request",
    )
    assert duplicate_receipt["status"] == "EXISTING_PROPOSAL"
    assert duplicate_receipt["skill_id"] == skill_receipt["skill_id"]
    assert len(runtime.db.query_all("SELECT * FROM skills")) == 1

    skill_sandbox = runtime.start_patch_mission_repair_skill_sandbox(
        skill_id=skill_receipt["skill_id"],
        reason="owner starts sandbox lifecycle for proposed repair skill",
    )
    sandboxed_skill = runtime.db.query_one(
        "SELECT status,definition_json FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    skill_experiment = runtime.db.query_one(
        "SELECT * FROM skill_experiments WHERE experiment_id=?",
        (skill_sandbox["experiment_id"],),
    )
    sandbox_manifest = json.loads(skill_experiment["manifest_json"])
    skill_sandbox_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_sandbox_started'"
    )

    assert skill_sandbox["status"] == "SKILL_SANDBOXED"
    assert skill_sandbox["skill_status_before"] == "PROPOSED"
    assert skill_sandbox["skill_status_after"] == "SANDBOXED"
    assert skill_sandbox["active_skill_created"] is False
    assert skill_sandbox["promotion_executed"] is False
    assert skill_sandbox["command_executed"] is False
    assert sandboxed_skill["status"] == "SANDBOXED"
    assert json.loads(sandboxed_skill["definition_json"])["status"] == "SANDBOXED"
    assert skill_experiment["status"] == "RUNNING"
    assert sandbox_manifest["source_candidate_id"] == candidate["candidate_id"]
    assert sandbox_manifest["source_candidate_status"] == "VALIDATED"
    assert sandbox_manifest["definition_sha256"] == digest_json(skill_definition)
    assert Path(skill_sandbox["manifest_path"]).is_file()
    assert str(runtime.config.sandbox_path) in skill_sandbox["manifest_path"]
    assert runtime.skills.active() == []
    assert len(skill_sandbox_rows) == 1

    skill_validation = runtime.validate_patch_mission_repair_skill_sandbox(
        skill_id=skill_receipt["skill_id"],
        reason="owner validates sandboxed repair skill evidence",
    )
    validated_skill = runtime.db.query_one(
        "SELECT status,definition_json FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    completed_experiment = runtime.db.query_one(
        "SELECT status,result_json FROM skill_experiments WHERE experiment_id=?",
        (skill_sandbox["experiment_id"],),
    )
    skill_validation_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_validated'"
    )
    skill_result = json.loads(completed_experiment["result_json"])

    assert skill_validation["status"] == "SKILL_VALIDATED"
    assert skill_validation["skill_status_before"] == "SANDBOXED"
    assert skill_validation["skill_status_after"] == "VALIDATED"
    assert skill_validation["candidate_cases"] == 1
    assert skill_validation["passed_cases"] == 1
    assert skill_validation["regressions"] == 0
    assert skill_validation["active_skill_created"] is False
    assert skill_validation["promotion_executed"] is False
    assert skill_validation["command_executed"] is False
    assert validated_skill["status"] == "VALIDATED"
    assert json.loads(validated_skill["definition_json"])["status"] == "VALIDATED"
    assert completed_experiment["status"] == "PASSED"
    assert skill_result["passed"] is True
    assert skill_result["passed_cases"] == 1
    assert skill_result["regressions"] == 0
    assert Path(skill_validation["result_path"]).is_file()
    assert runtime.skills.active() == []
    assert len(skill_validation_rows) == 1

    action_count_before_approval = runtime.db.query_one(
        "SELECT COUNT(*) AS count FROM actions"
    )["count"]
    with pytest.raises(PermissionError):
        runtime.approve_patch_mission_repair_skill(
            skill_id=skill_receipt["skill_id"],
            reason="missing owner approval must not approve repair skill",
        )
    skill_approval = runtime.approve_patch_mission_repair_skill(
        skill_id=skill_receipt["skill_id"],
        reason="owner approves validated repair skill for advisory matching",
        human_approved=True,
    )
    approved_skill = runtime.db.query_one(
        "SELECT status,definition_json FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    action_count_after_approval = runtime.db.query_one(
        "SELECT COUNT(*) AS count FROM actions"
    )["count"]
    skill_approval_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_approved'"
    )
    active_skills = runtime.skills.active()

    assert skill_approval["status"] == "SKILL_APPROVED"
    assert skill_approval["skill_status_before"] == "VALIDATED"
    assert skill_approval["skill_status_after"] == "APPROVED"
    assert skill_approval["human_approved"] is True
    assert skill_approval["active_skill_matchable"] is True
    assert skill_approval["active_skill_created"] is False
    assert skill_approval["promotion_executed"] is False
    assert skill_approval["command_executed"] is False
    assert skill_approval["external_action_executed"] is False
    assert skill_approval["canonical_repo_write_executed"] is False
    assert approved_skill["status"] == "APPROVED"
    approved_definition = json.loads(approved_skill["definition_json"])
    assert approved_definition["status"] == "APPROVED"
    assert action_count_after_approval == action_count_before_approval
    assert len(skill_approval_rows) == 1
    assert any(
        skill.get("skill_id") == skill_receipt["skill_id"] for skill in active_skills
    )
    assert all(skill.get("status") != "PROMOTED" for skill in active_skills)
    skill_use_count_before_advisory = runtime.db.query_one(
        "SELECT use_count FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )["use_count"]

    third_repo = make_failing_repo(tmp_path / "project-c")
    third_mission = runtime.start_patch_mission(
        repo_path=third_repo,
        mission="Use reviewed repair candidate as advisory context",
        execute_first_action=False,
    )
    third_status_step = runtime.continue_patch_mission(
        mission_id=third_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        third_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves third read-only PR status inspection",
    )
    runtime.resume_action(third_status_step["action_id"])
    third_plan_step = runtime.continue_patch_mission(
        mission_id=third_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=third_status_step["action_id"],
    )
    third_plan_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (third_plan_step["action_id"],),
        )["arguments_json"]
    )

    assert "## Reviewed Repair Skill Candidate" in third_plan_args["content"]
    assert candidate["candidate_id"] in third_plan_args["content"]
    assert "advisory only" in third_plan_args["content"]
    assert "## Approved Repair Skill Advisory Context" in third_plan_args["content"]
    assert skill_receipt["skill_id"] in third_plan_args["content"]
    assert "no skill execution" in third_plan_args["content"]
    assert "- Reason: Matched approved repair skill" in third_plan_args["content"]
    assert "supports the `test` next-step rationale" in third_plan_args["content"]

    runtime.approvals.issue(
        third_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves third CI fix plan",
    )
    runtime.resume_action(third_plan_step["action_id"])
    third_next_step = runtime.continue_patch_mission(
        mission_id=third_mission["mission_id"],
        mode="ci-next-action",
        action_id=third_plan_step["action_id"],
    )
    third_next_candidate = third_next_step["next_action_candidate"]
    third_next_action = runtime.db.query_one(
        "SELECT status,tool,skill_id,arguments_json FROM actions WHERE action_id=?",
        (third_next_step["action_id"],),
    )
    third_next_args = json.loads(third_next_action["arguments_json"])
    skill_action_candidate = third_next_candidate["patch_mission_skill_candidate"]

    assert third_next_candidate["source"] == "patch_mission_approved_skill_advisory"
    assert third_next_candidate["requires_owner_approval"] is True
    assert third_next_candidate["executes_now"] is False
    assert third_next_candidate["risk_class"] == "external"
    assert skill_action_candidate["candidate_type"] == "approved_repair_skill_action_candidate"
    assert skill_action_candidate["skill_id"] == skill_receipt["skill_id"]
    assert skill_action_candidate["owner_gated"] is True
    assert skill_action_candidate["executes_now"] is False
    assert skill_action_candidate["skill_execution_recorded"] is False
    assert skill_action_candidate["promotion_executed"] is False
    assert third_next_action["status"] == "WAITING_APPROVAL"
    assert third_next_action["tool"] == "run_command"
    assert third_next_action["skill_id"] is None
    assert (
        third_next_args["patch_mission_approved_skill_candidate"]["skill_id"]
        == skill_receipt["skill_id"]
    )
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory
    )

    runtime.approvals.issue(
        third_next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves third narrow pytest reproduction",
    )
    third_next_outcome = runtime.resume_action(third_next_step["action_id"])
    skill_after_approved_execution = runtime.db.query_one(
        "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    approved_skill_outcome_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_approved_repair_skill_action_outcome_recorded'"
    )
    approved_skill_outcome = third_next_outcome["approved_repair_skill_outcome"]

    assert third_next_outcome["status"] == "SUCCEEDED"
    assert approved_skill_outcome["status"] == "RECORDED"
    assert approved_skill_outcome["skill_id"] == skill_receipt["skill_id"]
    assert approved_skill_outcome["action_id"] == third_next_step["action_id"]
    assert approved_skill_outcome["approval_valid"] is True
    assert approved_skill_outcome["success"] is True
    assert approved_skill_outcome["promotion_executed"] is False
    assert approved_skill_outcome["repo_write_executed"] is False
    assert approved_skill_outcome["push_or_pr_executed"] is False
    assert skill_after_approved_execution["status"] == "APPROVED"
    assert (
        skill_after_approved_execution["use_count"]
        == skill_use_count_before_advisory + 1
    )
    assert skill_after_approved_execution["success_rate"] == 1.0
    assert len(approved_skill_outcome_rows) == 1

    third_draft_step = runtime.continue_patch_mission(
        mission_id=third_mission["mission_id"],
        mode="from-test-result",
        action_id=third_next_step["action_id"],
    )
    third_draft_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (third_draft_step["action_id"],),
        )["arguments_json"]
    )

    assert "Reviewed repair skill candidate used:" in third_draft_args["content"]
    assert candidate["candidate_id"] in third_draft_args["content"]
    assert "advisory only" in third_draft_args["content"]
    assert "Approved repair skill advisory context:" in third_draft_args["content"]
    assert skill_receipt["skill_id"] in third_draft_args["content"]
    assert "no skill execution" in third_draft_args["content"]
    assert "Approved repair skill rationale:" in third_draft_args["content"]
    assert "supports the outbox patch draft rationale" in third_draft_args["content"]
    skill_after_advisory = runtime.db.query_one(
        "SELECT status,use_count FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    push_or_pr_rows = runtime.db.query_all(
        """
        SELECT tool FROM actions
        WHERE tool IN ('git_push', 'create_github_pr')
        """
    )

    assert skill_after_advisory["status"] == "APPROVED"
    assert skill_after_advisory["use_count"] == skill_use_count_before_advisory + 1
    assert push_or_pr_rows == []

    fourth_repo = make_failing_repo(tmp_path / "project-d")
    fourth_mission = runtime.start_patch_mission(
        repo_path=fourth_repo,
        mission="Use approved repair skill for another owner-gated local reproduction",
        execute_first_action=False,
    )
    fourth_status_step = runtime.continue_patch_mission(
        mission_id=fourth_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        fourth_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fourth read-only PR status inspection",
    )
    runtime.resume_action(fourth_status_step["action_id"])
    fourth_plan_step = runtime.continue_patch_mission(
        mission_id=fourth_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=fourth_status_step["action_id"],
    )
    runtime.approvals.issue(
        fourth_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fourth CI fix plan",
    )
    runtime.resume_action(fourth_plan_step["action_id"])
    fourth_next_step = runtime.continue_patch_mission(
        mission_id=fourth_mission["mission_id"],
        mode="ci-next-action",
        action_id=fourth_plan_step["action_id"],
    )
    fourth_next_candidate = fourth_next_step["next_action_candidate"]

    assert (
        fourth_next_candidate["patch_mission_skill_candidate"]["skill_id"]
        == skill_receipt["skill_id"]
    )
    runtime.approvals.issue(
        fourth_next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fourth narrow pytest reproduction",
    )
    fourth_next_outcome = runtime.resume_action(fourth_next_step["action_id"])
    promotion_review_rows = runtime.db.query_all(
        "SELECT * FROM evolution_candidates WHERE candidate_type='patch_mission_repair_skill_promotion_review'"
    )
    promotion_review_events = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_promotion_review_candidate_created'"
    )
    skill_after_second_outcome = runtime.db.query_one(
        "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )

    assert fourth_next_outcome["status"] == "SUCCEEDED"
    assert (
        fourth_next_outcome["approved_repair_skill_outcome"][
            "promotion_review_candidate"
        ]["status"]
        == "PROMOTION_REVIEW_CANDIDATE_CREATED"
    )
    assert len(promotion_review_rows) == 1
    promotion_candidate = promotion_review_rows[0]
    promotion_proposal = json.loads(promotion_candidate["proposal_json"])["proposal"]
    assert promotion_candidate["status"] == "PROPOSED"
    assert promotion_proposal["skill_id"] == skill_receipt["skill_id"]
    assert promotion_proposal["approved_success_count"] == 2
    assert promotion_proposal["promotion_executed"] is False
    assert promotion_proposal["human_approval_required_for_promotion"] is True
    assert promotion_proposal["approval_bypass_allowed"] is False
    assert skill_after_second_outcome["status"] == "APPROVED"
    assert (
        skill_after_second_outcome["use_count"]
        == skill_use_count_before_advisory + 2
    )
    assert skill_after_second_outcome["success_rate"] == 1.0
    assert len(promotion_review_events) == 1

    action_count_before_review_approval = runtime.db.query_one(
        "SELECT COUNT(*) AS count FROM actions"
    )["count"]
    with pytest.raises(PermissionError):
        runtime.approve_patch_mission_repair_skill_promotion_review(
            candidate_id=promotion_candidate["candidate_id"],
            reason="missing human approval must not approve promotion review",
        )
    promotion_review_approval = (
        runtime.approve_patch_mission_repair_skill_promotion_review(
            candidate_id=promotion_candidate["candidate_id"],
            reason="owner approves promotion review without promoting skill",
            human_approved=True,
        )
    )
    reviewed_promotion_candidate = runtime.db.query_one(
        "SELECT status,result_json FROM evolution_candidates WHERE candidate_id=?",
        (promotion_candidate["candidate_id"],),
    )
    skill_after_review_approval = runtime.db.query_one(
        "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    action_count_after_review_approval = runtime.db.query_one(
        "SELECT COUNT(*) AS count FROM actions"
    )["count"]
    promotion_review_approval_events = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_promotion_review_approved'"
    )

    assert promotion_review_approval["status"] == "PROMOTION_REVIEW_APPROVED"
    assert promotion_review_approval["candidate_status_before"] == "PROPOSED"
    assert promotion_review_approval["candidate_status_after"] == "APPROVED"
    assert promotion_review_approval["skill_id"] == skill_receipt["skill_id"]
    assert promotion_review_approval["skill_status_before"] == "APPROVED"
    assert promotion_review_approval["skill_status_after"] == "APPROVED"
    assert promotion_review_approval["human_approved"] is True
    assert promotion_review_approval["promotion_request_authorized"] is True
    assert promotion_review_approval["promotion_executed"] is False
    assert promotion_review_approval["command_executed"] is False
    assert promotion_review_approval["repo_write_executed"] is False
    assert promotion_review_approval["push_or_pr_executed"] is False
    assert reviewed_promotion_candidate["status"] == "APPROVED"
    assert (
        json.loads(reviewed_promotion_candidate["result_json"])["receipt_digest"]
        == promotion_review_approval["receipt_digest"]
    )
    assert skill_after_review_approval["status"] == "APPROVED"
    assert (
        skill_after_review_approval["use_count"]
        == skill_use_count_before_advisory + 2
    )
    assert skill_after_review_approval["success_rate"] == 1.0
    assert action_count_after_review_approval == action_count_before_review_approval
    assert len(promotion_review_approval_events) == 1

    action_count_before_promotion = runtime.db.query_one(
        "SELECT COUNT(*) AS count FROM actions"
    )["count"]
    with pytest.raises(PermissionError):
        runtime.promote_patch_mission_repair_skill_from_review(
            candidate_id=promotion_candidate["candidate_id"],
            reason="missing human approval must not promote repair skill",
        )
    promotion_receipt = runtime.promote_patch_mission_repair_skill_from_review(
        candidate_id=promotion_candidate["candidate_id"],
        reason="owner promotes approved repair skill after review",
        human_approved=True,
    )
    promoted_skill = runtime.db.query_one(
        "SELECT status,definition_json,use_count,success_rate FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    promoted_candidate = runtime.db.query_one(
        "SELECT status,result_json FROM evolution_candidates WHERE candidate_id=?",
        (promotion_candidate["candidate_id"],),
    )
    promotion_events = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_repair_skill_promoted'"
    )
    action_count_after_promotion = runtime.db.query_one(
        "SELECT COUNT(*) AS count FROM actions"
    )["count"]

    assert promotion_receipt["status"] == "SKILL_PROMOTED"
    assert promotion_receipt["candidate_status_before"] == "APPROVED"
    assert promotion_receipt["candidate_status_after"] == "PROMOTED"
    assert promotion_receipt["skill_status_before"] == "APPROVED"
    assert promotion_receipt["skill_status_after"] == "PROMOTED"
    assert promotion_receipt["human_approved"] is True
    assert promotion_receipt["promotion_executed"] is True
    assert promotion_receipt["command_executed"] is False
    assert promotion_receipt["repo_write_executed"] is False
    assert promotion_receipt["push_or_pr_executed"] is False
    assert promoted_skill["status"] == "PROMOTED"
    assert json.loads(promoted_skill["definition_json"])["status"] == "PROMOTED"
    assert promoted_skill["use_count"] == skill_use_count_before_advisory + 2
    assert promoted_skill["success_rate"] == 1.0
    assert promoted_candidate["status"] == "PROMOTED"
    assert (
        json.loads(promoted_candidate["result_json"])["receipt_digest"]
        == promotion_receipt["receipt_digest"]
    )
    assert action_count_after_promotion == action_count_before_promotion
    assert len(promotion_events) == 1

    fifth_repo = make_failing_repo(tmp_path / "project-e")
    fifth_mission = runtime.start_patch_mission(
        repo_path=fifth_repo,
        mission="Use promoted repair skill as higher-confidence advisory context",
        execute_first_action=False,
    )
    fifth_status_step = runtime.continue_patch_mission(
        mission_id=fifth_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        fifth_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fifth read-only PR status inspection",
    )
    runtime.resume_action(fifth_status_step["action_id"])
    fifth_plan_step = runtime.continue_patch_mission(
        mission_id=fifth_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=fifth_status_step["action_id"],
    )
    fifth_plan_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (fifth_plan_step["action_id"],),
        )["arguments_json"]
    )

    assert "## Approved Repair Skill Advisory Context" in fifth_plan_args["content"]
    assert skill_receipt["skill_id"] in fifth_plan_args["content"]
    assert "status `PROMOTED`" in fifth_plan_args["content"]
    assert "promoted advisory confidence" in fifth_plan_args["content"]
    assert "- Reason: Matched promoted repair skill" in fifth_plan_args["content"]
    assert "higher-confidence promoted rationale" in fifth_plan_args["content"]
    assert "Advisory influence only" in fifth_plan_args["content"]

    runtime.approvals.issue(
        fifth_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fifth CI fix plan",
    )
    runtime.resume_action(fifth_plan_step["action_id"])
    fifth_next_step = runtime.continue_patch_mission(
        mission_id=fifth_mission["mission_id"],
        mode="ci-next-action",
        action_id=fifth_plan_step["action_id"],
    )
    fifth_next_candidate = fifth_next_step["next_action_candidate"]
    fifth_next_action = runtime.db.query_one(
        "SELECT status,tool,skill_id,arguments_json FROM actions WHERE action_id=?",
        (fifth_next_step["action_id"],),
    )
    fifth_next_args = json.loads(fifth_next_action["arguments_json"])
    promoted_skill_candidate = fifth_next_candidate["patch_mission_skill_candidate"]

    assert fifth_next_candidate["source"] == "patch_mission_promoted_skill_advisory"
    assert fifth_next_candidate["requires_owner_approval"] is True
    assert fifth_next_candidate["approval_required"] is True
    assert fifth_next_candidate["executes_now"] is False
    assert fifth_next_candidate["risk_class"] == "external"
    assert fifth_next_candidate["authority"]["promoted_skill_candidate_only"] is True
    assert (
        promoted_skill_candidate["candidate_type"]
        == "promoted_repair_skill_action_candidate"
    )
    assert promoted_skill_candidate["skill_id"] == skill_receipt["skill_id"]
    assert promoted_skill_candidate["skill_status"] == "PROMOTED"
    assert promoted_skill_candidate["promoted_skill_matched"] is True
    assert promoted_skill_candidate["advisory_confidence"] == "promoted_repair_skill"
    assert promoted_skill_candidate["owner_gated"] is True
    assert promoted_skill_candidate["approval_still_required"] is True
    assert promoted_skill_candidate["executes_now"] is False
    assert promoted_skill_candidate["repo_write_executed"] is False
    assert promoted_skill_candidate["push_or_pr_executed"] is False
    assert fifth_next_action["status"] == "WAITING_APPROVAL"
    assert fifth_next_action["tool"] == "run_command"
    assert fifth_next_action["skill_id"] is None
    assert (
        fifth_next_args["patch_mission_approved_skill_candidate"]["skill_status"]
        == "PROMOTED"
    )
    with pytest.raises(PermissionError):
        runtime.resume_action(fifth_next_step["action_id"])
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory + 2
    )
    approved_outcomes_before_promoted_execution = runtime.db.get_runtime(
        "patch_mission_approved_skill_outcomes", []
    )
    promoted_outcomes_before_execution = runtime.db.get_runtime(
        "patch_mission_promoted_skill_outcomes", []
    )
    assert isinstance(approved_outcomes_before_promoted_execution, list)
    assert isinstance(promoted_outcomes_before_execution, list)
    assert len(approved_outcomes_before_promoted_execution) == 2
    assert promoted_outcomes_before_execution == []

    runtime.approvals.issue(
        fifth_next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fifth promoted-skill local reproduction",
    )
    fifth_next_outcome = runtime.resume_action(fifth_next_step["action_id"])
    promoted_skill_after_success = runtime.db.query_one(
        "SELECT status,use_count,success_rate FROM skills WHERE skill_id=?",
        (skill_receipt["skill_id"],),
    )
    promoted_outcomes_after_success = runtime.db.get_runtime(
        "patch_mission_promoted_skill_outcomes", []
    )
    approved_outcomes_after_success = runtime.db.get_runtime(
        "patch_mission_approved_skill_outcomes", []
    )
    promoted_outcome_rows = runtime.db.query_all(
        "SELECT event_type,payload_json FROM evidence WHERE event_type='patch_mission_promoted_repair_skill_action_outcome_recorded'"
    )

    assert fifth_next_outcome["status"] == "SUCCEEDED"
    assert fifth_next_outcome["promoted_repair_skill_outcome"]["status"] == "RECORDED"
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"]["skill_id"]
        == skill_receipt["skill_id"]
    )
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"]["skill_status"]
        == "PROMOTED"
    )
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"][
            "promoted_skill_outcome"
        ]
        is True
    )
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"]["approval_valid"]
        is True
    )
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"]["promotion_executed"]
        is False
    )
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"]["repo_write_executed"]
        is False
    )
    assert (
        fifth_next_outcome["promoted_repair_skill_outcome"]["push_or_pr_executed"]
        is False
    )
    assert promoted_skill_after_success["status"] == "PROMOTED"
    assert (
        promoted_skill_after_success["use_count"]
        == skill_use_count_before_advisory + 3
    )
    assert promoted_skill_after_success["success_rate"] == 1.0
    assert isinstance(promoted_outcomes_after_success, list)
    assert len(promoted_outcomes_after_success) == 1
    assert promoted_outcomes_after_success[0]["skill_status"] == "PROMOTED"
    assert isinstance(approved_outcomes_after_success, list)
    assert len(approved_outcomes_after_success) == 2
    assert len(promoted_outcome_rows) == 1

    def create_promoted_skill_next_action(project_name: str) -> dict[str, object]:
        repo = make_failing_repo(tmp_path / project_name)
        mission_record = runtime.start_patch_mission(
            repo_path=repo,
            mission=f"Use promoted repair skill in {project_name}",
            execute_first_action=False,
        )
        status_step = runtime.continue_patch_mission(
            mission_id=mission_record["mission_id"],
            mode="pr-status",
            target="https://github.com/example/demo/pull/7",
        )
        runtime.approvals.issue(
            status_step["action_id"],
            approve=True,
            ttl_minutes=30,
            reason=f"owner approves {project_name} read-only PR status inspection",
        )
        runtime.resume_action(status_step["action_id"])
        plan_step = runtime.continue_patch_mission(
            mission_id=mission_record["mission_id"],
            mode="ci-fix-plan",
            action_id=status_step["action_id"],
        )
        plan_args = json.loads(
            runtime.db.query_one(
                "SELECT arguments_json FROM actions WHERE action_id=?",
                (plan_step["action_id"],),
            )["arguments_json"]
        )
        runtime.approvals.issue(
            plan_step["action_id"],
            approve=True,
            ttl_minutes=30,
            reason=f"owner approves {project_name} CI fix plan",
        )
        runtime.resume_action(plan_step["action_id"])
        next_step = runtime.continue_patch_mission(
            mission_id=mission_record["mission_id"],
            mode="ci-next-action",
            action_id=plan_step["action_id"],
        )
        return {
            "mission": mission_record,
            "plan_step": plan_step,
            "plan_args": plan_args,
            "next_step": next_step,
        }

    rejected_promoted_chain = create_promoted_skill_next_action("project-f")
    failed_promoted_chain = create_promoted_skill_next_action("project-g")
    rejected_promoted_step = rejected_promoted_chain["next_step"]
    failed_promoted_step = failed_promoted_chain["next_step"]
    rejected_promoted_action = runtime.db.query_one(
        "SELECT status,tool,skill_id,arguments_json FROM actions WHERE action_id=?",
        (rejected_promoted_step["action_id"],),
    )
    rejected_promoted_args = json.loads(rejected_promoted_action["arguments_json"])
    rejected_promoted_candidate = rejected_promoted_step["next_action_candidate"][
        "patch_mission_skill_candidate"
    ]

    assert "- Mode: `test`" in rejected_promoted_chain["plan_args"]["content"]
    assert (
        "- Original CI-selected mode: `test`"
        in rejected_promoted_chain["plan_args"]["content"]
    )
    assert (
        "direct path preserved by `outcome_supported` promoted history"
        in rejected_promoted_chain["plan_args"]["content"]
    )
    assert (
        "policy and owner approval still control command execution"
        in rejected_promoted_chain["plan_args"]["content"]
    )
    assert rejected_promoted_action["status"] == "WAITING_APPROVAL"
    assert rejected_promoted_action["tool"] == "run_command"
    assert rejected_promoted_action["skill_id"] is None
    assert rejected_promoted_candidate["promoted_outcome_confidence"]["level"] == "outcome_supported"
    assert rejected_promoted_candidate["owner_gated"] is True
    assert rejected_promoted_candidate["approval_still_required"] is True
    assert rejected_promoted_candidate["executes_now"] is False
    assert (
        rejected_promoted_args["patch_mission_approved_skill_candidate"][
            "promoted_outcome_confidence"
        ]["level"]
        == "outcome_supported"
    )
    rejected_mission_state = next(
        item
        for item in runtime.patch_missions(limit=100)
        if item["mission_id"] == rejected_promoted_chain["mission"]["mission_id"]
    )
    rejected_continuity = rejected_mission_state["continuity"]
    rejected_confidence_decision = rejected_continuity["confidence_decision"]

    assert rejected_continuity["state"] == "waiting_approval"
    assert rejected_continuity["blocking_mode"] == "ci-next-action"
    assert rejected_confidence_decision["level"] == "outcome_supported"
    assert (
        rejected_confidence_decision["decision"]
        == "outcome_supported_direct_test"
    )
    assert rejected_confidence_decision["selected_mode"] == "test"
    assert (
        rejected_confidence_decision["selected_target"]
        == "tests/test_demo.py::test_demo"
    )
    assert rejected_confidence_decision["requires_owner_approval"] is True
    assert rejected_confidence_decision["executes_now"] is False
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory + 3
    )
    runtime.approvals.issue(
        str(rejected_promoted_step["action_id"]),
        approve=False,
        ttl_minutes=30,
        reason="owner rejects promoted-skill candidate",
    )
    with pytest.raises(ValueError):
        runtime.resume_action(str(rejected_promoted_step["action_id"]))
    rejected_action = runtime.db.query_one(
        "SELECT status FROM actions WHERE action_id=?",
        (rejected_promoted_step["action_id"],),
    )
    assert rejected_action["status"] == "REJECTED"
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory + 3
    )
    assert len(runtime.db.get_runtime("patch_mission_promoted_skill_outcomes", [])) == 1

    runtime.db.execute(
        "UPDATE actions SET acceptance_json=? WHERE action_id=?",
        (
            json.dumps(["output contains promoted-skill-impossible-acceptance-token"]),
            failed_promoted_step["action_id"],
        ),
    )
    runtime.approvals.issue(
        str(failed_promoted_step["action_id"]),
        approve=True,
        ttl_minutes=30,
        reason="owner approves promoted-skill candidate that fails acceptance",
    )
    failed_promoted_outcome = runtime.resume_action(
        str(failed_promoted_step["action_id"])
    )
    failed_promoted_action = runtime.db.query_one(
        "SELECT status FROM actions WHERE action_id=?",
        (failed_promoted_step["action_id"],),
    )

    assert failed_promoted_outcome["status"] == "FAILED"
    assert failed_promoted_outcome["promoted_repair_skill_outcome"] is None
    assert failed_promoted_action["status"] == "FAILED"
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory + 3
    )
    assert len(runtime.db.get_runtime("patch_mission_promoted_skill_outcomes", [])) == 1

    eighth_repo = make_failing_repo(tmp_path / "project-h")
    eighth_mission = runtime.start_patch_mission(
        repo_path=eighth_repo,
        mission="Use promoted outcome history to calibrate next planning",
        execute_first_action=False,
    )
    eighth_status_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        eighth_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves eighth read-only PR status inspection",
    )
    runtime.resume_action(eighth_status_step["action_id"])
    eighth_plan_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=eighth_status_step["action_id"],
    )
    eighth_plan_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (eighth_plan_step["action_id"],),
        )["arguments_json"]
    )

    assert "status `PROMOTED`" in eighth_plan_args["content"]
    assert "promoted outcome confidence `mixed_outcome`" in eighth_plan_args["content"]
    assert "successes `1`" in eighth_plan_args["content"]
    assert "failed_or_rejected `2`" in eighth_plan_args["content"]
    assert "Promoted outcome history: level `mixed_outcome`" in eighth_plan_args["content"]
    assert "- Mode: `inspect-file`" in eighth_plan_args["content"]
    assert "- Target: `tests/test_demo.py`" in eighth_plan_args["content"]
    assert "- Original CI-selected mode: `test`" in eighth_plan_args["content"]
    assert "- Original CI-selected target: `tests/test_demo.py::test_demo`" in eighth_plan_args["content"]
    assert "before running or drafting another repair action" in eighth_plan_args["content"]
    assert "Advisory influence only" in eighth_plan_args["content"]

    runtime.approvals.issue(
        eighth_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves eighth CI fix plan",
    )
    runtime.resume_action(eighth_plan_step["action_id"])
    eighth_next_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="ci-next-action",
        action_id=eighth_plan_step["action_id"],
    )
    eighth_next_candidate = eighth_next_step["next_action_candidate"]
    eighth_next_action = runtime.db.query_one(
        "SELECT status,tool,skill_id,arguments_json FROM actions WHERE action_id=?",
        (eighth_next_step["action_id"],),
    )
    eighth_next_args = json.loads(eighth_next_action["arguments_json"])
    eighth_confidence = eighth_next_candidate["patch_mission_skill_candidate"][
        "promoted_outcome_confidence"
    ]

    assert eighth_next_candidate["source"] == "patch_mission_promoted_skill_advisory"
    assert eighth_next_candidate["requires_owner_approval"] is False
    assert eighth_next_candidate["approval_required"] is False
    assert eighth_next_candidate["executes_now"] is True
    assert eighth_next_candidate["risk_class"] == "read"
    assert eighth_next_action["status"] == "SUCCEEDED"
    assert eighth_next_action["tool"] == "read_file"
    assert eighth_next_action["skill_id"] is None
    assert eighth_next_args["patch_mission_approved_skill_candidate"]["owner_gated"] is False
    assert (
        eighth_next_args["patch_mission_approved_skill_candidate"][
            "approval_still_required"
        ]
        is False
    )
    assert eighth_next_args["patch_mission_approved_skill_candidate"]["executes_now"] is True
    assert eighth_confidence["level"] == "mixed_outcome"
    assert eighth_confidence["success_count"] == 1
    assert eighth_confidence["failed_or_rejected_count"] == 2
    assert eighth_confidence["authority"]["planning_metadata_only"] is True
    assert eighth_confidence["authority"]["approval_bypass_allowed"] is False
    assert eighth_confidence["authority"]["executes_actions"] is False
    eighth_mission_state = next(
        item
        for item in runtime.patch_missions(limit=100)
        if item["mission_id"] == eighth_mission["mission_id"]
    )
    eighth_continuity = eighth_mission_state["continuity"]
    eighth_confidence_decision = eighth_continuity["confidence_decision"]

    assert eighth_continuity["state"] == "local_confidence_fallback_inspected"
    assert "read-only file inspection" in eighth_continuity["summary"]
    assert (
        eighth_confidence_decision["decision"]
        == "safety_fallback_inspect_file"
    )
    assert eighth_confidence_decision["level"] == "mixed_outcome"
    assert eighth_confidence_decision["selected_mode"] == "inspect-file"
    assert eighth_confidence_decision["selected_target"] == "tests/test_demo.py"
    assert eighth_confidence_decision["requires_owner_approval"] is False
    assert eighth_confidence_decision["executes_now"] is True
    eighth_resume_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="resume-next",
    )
    eighth_resume_action = runtime.db.query_one(
        "SELECT status,tool,arguments_json FROM actions WHERE action_id=?",
        (eighth_resume_step["action_id"],),
    )
    eighth_resume_args = json.loads(eighth_resume_action["arguments_json"])

    assert eighth_resume_step["requested_mode"] == "resume-next"
    assert eighth_resume_step["resume_next"]["mode"] == "test"
    assert (
        eighth_resume_step["resume_next"]["target"]
        == "tests/test_demo.py::test_demo"
    )
    assert (
        eighth_resume_step["resume_next"]["confidence_decision"]["decision"]
        == "safety_fallback_inspect_file"
    )
    assert (
        eighth_resume_step["resume_next"]["target_decision"]["decision"]
        == "nodeid_restored_from_ci_and_inspection"
    )
    assert (
        "inspected file contains the referenced test function"
        in eighth_resume_step["resume_next"]["target_decision"]["reason"]
    )
    assert eighth_resume_action["status"] == "WAITING_APPROVAL"
    assert eighth_resume_action["tool"] == "run_command"
    assert eighth_resume_args["command"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_demo.py::test_demo",
    ]
    runtime.approvals.issue(
        eighth_resume_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fallback-restored narrow pytest target",
    )
    eighth_test_outcome = runtime.resume_action(eighth_resume_step["action_id"])
    eighth_draft_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="from-test-result",
        action_id=eighth_resume_step["action_id"],
    )
    eighth_draft_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (eighth_draft_step["action_id"],),
        )["arguments_json"]
    )
    eighth_learning = runtime._patch_mission_failure_learning_for_test(
        eighth_resume_step["action_id"]
    )

    assert eighth_test_outcome["status"] == "SUCCEEDED"
    assert "resume-next test" in eighth_draft_args["content"]
    assert "fallback inspect action" in eighth_draft_args["content"]
    assert "promoted confidence `mixed_outcome`" in eighth_draft_args["content"]
    assert "via `safety_fallback_inspect_file`" in eighth_draft_args["content"]
    assert "verification target `tests/test_demo.py::test_demo`" in eighth_draft_args["content"]
    assert "via `nodeid_restored_from_ci_and_inspection`" in eighth_draft_args["content"]
    assert "inspected file contains the referenced test function" in eighth_draft_args["content"]
    assert eighth_learning["pytest_target"] == "tests/test_demo.py::test_demo"
    assert "promoted confidence `mixed_outcome`" in eighth_learning["ci_source"]
    assert "nodeid_restored_from_ci_and_inspection" in eighth_learning["ci_source"]
    assert len(runtime.db.get_runtime("patch_mission_promoted_skill_outcomes", [])) == 1
    assert runtime.db.get_runtime("patch_mission_promoted_skill_recovery_outcomes", []) == []
    runtime.approvals.issue(
        eighth_draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fallback-restored patch draft",
    )
    runtime.resume_action(eighth_draft_step["action_id"])
    eighth_apply_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="apply-patch",
        action_id=eighth_draft_step["action_id"],
    )
    runtime.approvals.issue(
        eighth_apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fallback-restored repo patch write",
    )
    runtime.resume_action(eighth_apply_step["action_id"])
    eighth_verify_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        eighth_verify_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves fallback-restored passing verification",
    )
    eighth_verify_outcome = runtime.resume_action(eighth_verify_step["action_id"])
    assert eighth_verify_outcome["status"] == "SUCCEEDED"
    assert eighth_verify_outcome["output"]["returncode"] == 0
    eighth_summary_step = runtime.continue_patch_mission(
        mission_id=eighth_mission["mission_id"],
        mode="pr-summary",
        action_id=eighth_verify_step["action_id"],
    )
    recovery_outcomes = runtime.db.get_runtime(
        "patch_mission_promoted_skill_recovery_outcomes",
        [],
    )

    assert eighth_summary_step["mode"] == "pr-summary"
    assert len(recovery_outcomes) == 1
    assert recovery_outcomes[0]["skill_id"] == skill_receipt["skill_id"]
    assert recovery_outcomes[0]["success"] is True
    assert recovery_outcomes[0]["approval_valid"] is True
    assert recovery_outcomes[0]["failed_or_rejected_evidence_preserved"] is True
    assert (
        recovery_outcomes[0]["target_decision"]
        == "nodeid_restored_from_ci_and_inspection"
    )
    assert recovery_outcomes[0]["recovered_target"] == "tests/test_demo.py::test_demo"
    assert all(recovery_outcomes[0]["approval_checks"].values())

    ninth_repo = make_failing_repo(tmp_path / "project-i")
    ninth_mission = runtime.start_patch_mission(
        repo_path=ninth_repo,
        mission="Use promoted skill after fallback recovery evidence",
        execute_first_action=False,
    )
    ninth_status_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/8",
    )
    runtime.approvals.issue(
        ninth_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status after fallback recovery",
    )
    runtime.resume_action(ninth_status_step["action_id"])
    ninth_plan_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=ninth_status_step["action_id"],
    )
    ninth_plan_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (ninth_plan_step["action_id"],),
        )["arguments_json"]
    )

    assert "promoted outcome confidence `recovering_mixed_outcome`" in ninth_plan_args["content"]
    assert "successes `1`" in ninth_plan_args["content"]
    assert "failed_or_rejected `2`" in ninth_plan_args["content"]
    assert "recovery_successes `1`" in ninth_plan_args["content"]
    assert "Promoted outcome history: level `recovering_mixed_outcome`" in ninth_plan_args["content"]
    assert "- Mode: `inspect-file`" in ninth_plan_args["content"]
    assert "- Target: `tests/test_demo.py`" in ninth_plan_args["content"]
    runtime.approvals.issue(
        ninth_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovering confidence plan write",
    )
    runtime.resume_action(ninth_plan_step["action_id"])
    ninth_next_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="ci-next-action",
        action_id=ninth_plan_step["action_id"],
    )
    ninth_confidence = ninth_next_step["next_action_candidate"][
        "patch_mission_skill_candidate"
    ]["promoted_outcome_confidence"]
    ninth_mission_state = next(
        item
        for item in runtime.patch_missions(limit=100)
        if item["mission_id"] == ninth_mission["mission_id"]
    )
    ninth_confidence_decision = ninth_mission_state["continuity"]["confidence_decision"]

    assert ninth_confidence["level"] == "recovering_mixed_outcome"
    assert ninth_confidence["success_count"] == 1
    assert ninth_confidence["failed_or_rejected_count"] == 2
    assert ninth_confidence["recovery_count"] == 1
    assert ninth_confidence["unrecovered_failed_or_rejected_count"] == 1
    assert (
        ninth_confidence_decision["decision"]
        == "safety_fallback_inspect_file"
    )
    assert ninth_confidence_decision["level"] == "recovering_mixed_outcome"
    assert ninth_confidence_decision["recovery_count"] == 1
    ninth_resume_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="resume-next",
    )

    assert ninth_resume_step["resume_next"]["mode"] == "test"
    assert (
        ninth_resume_step["resume_next"]["target"]
        == "tests/test_demo.py::test_demo"
    )
    assert (
        ninth_resume_step["resume_next"]["target_decision"]["decision"]
        == "nodeid_restored_from_ci_and_inspection"
    )
    runtime.approvals.issue(
        ninth_resume_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves second fallback-restored narrow pytest target",
    )
    ninth_test_outcome = runtime.resume_action(ninth_resume_step["action_id"])
    assert ninth_test_outcome["status"] == "SUCCEEDED"
    assert ninth_test_outcome["output"]["returncode"] != 0
    ninth_draft_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="from-test-result",
        action_id=ninth_resume_step["action_id"],
    )
    runtime.approvals.issue(
        ninth_draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves second fallback-restored patch draft",
    )
    runtime.resume_action(ninth_draft_step["action_id"])
    ninth_apply_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="apply-patch",
        action_id=ninth_draft_step["action_id"],
    )
    runtime.approvals.issue(
        ninth_apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves second fallback-restored repo patch write",
    )
    runtime.resume_action(ninth_apply_step["action_id"])
    ninth_verify_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        ninth_verify_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves second fallback-restored passing verification",
    )
    ninth_verify_outcome = runtime.resume_action(ninth_verify_step["action_id"])
    assert ninth_verify_outcome["status"] == "SUCCEEDED"
    assert ninth_verify_outcome["output"]["returncode"] == 0
    ninth_summary_step = runtime.continue_patch_mission(
        mission_id=ninth_mission["mission_id"],
        mode="pr-summary",
        action_id=ninth_verify_step["action_id"],
    )
    recovered_outcomes = runtime.db.get_runtime(
        "patch_mission_promoted_skill_recovery_outcomes",
        [],
    )

    assert ninth_summary_step["mode"] == "pr-summary"
    assert len(recovered_outcomes) == 2
    assert {
        item["recovered_target"]
        for item in recovered_outcomes
        if item["skill_id"] == skill_receipt["skill_id"]
    } == {"tests/test_demo.py::test_demo"}
    assert all(item["failed_or_rejected_evidence_preserved"] for item in recovered_outcomes)

    tenth_repo = make_failing_repo(tmp_path / "project-j")
    initialize_git_repo(tenth_repo)
    tenth_mission = runtime.start_patch_mission(
        repo_path=tenth_repo,
        mission="Use recovered promoted skill confidence after repeated fallback repairs",
        execute_first_action=False,
    )
    tenth_status_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/9",
    )
    runtime.approvals.issue(
        tenth_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status after full recovery",
    )
    runtime.resume_action(tenth_status_step["action_id"])
    tenth_plan_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="ci-fix-plan",
        action_id=tenth_status_step["action_id"],
    )
    tenth_plan_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (tenth_plan_step["action_id"],),
        )["arguments_json"]
    )

    assert "promoted outcome confidence `recovered_outcome_supported`" in tenth_plan_args["content"]
    assert "successes `1`" in tenth_plan_args["content"]
    assert "failed_or_rejected `2`" in tenth_plan_args["content"]
    assert "recovery_successes `2`" in tenth_plan_args["content"]
    assert "Promoted outcome history: level `recovered_outcome_supported`" in tenth_plan_args["content"]
    assert "- Mode: `test`" in tenth_plan_args["content"]
    assert "- Target: `tests/test_demo.py::test_demo`" in tenth_plan_args["content"]
    assert "direct path preserved by `recovered_outcome_supported` promoted history" in tenth_plan_args["content"]
    runtime.approvals.issue(
        tenth_plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered confidence plan write",
    )
    runtime.resume_action(tenth_plan_step["action_id"])
    tenth_next_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="ci-next-action",
        action_id=tenth_plan_step["action_id"],
    )
    tenth_next_action = runtime.db.query_one(
        "SELECT status,tool,arguments_json FROM actions WHERE action_id=?",
        (tenth_next_step["action_id"],),
    )
    tenth_confidence = tenth_next_step["next_action_candidate"][
        "patch_mission_skill_candidate"
    ]["promoted_outcome_confidence"]
    tenth_mission_state = next(
        item
        for item in runtime.patch_missions(limit=100)
        if item["mission_id"] == tenth_mission["mission_id"]
    )
    tenth_confidence_decision = tenth_mission_state["continuity"]["confidence_decision"]

    assert tenth_next_action["status"] == "WAITING_APPROVAL"
    assert tenth_next_action["tool"] == "run_command"
    assert json.loads(tenth_next_action["arguments_json"])["command"] == [
        "python",
        "-m",
        "pytest",
        "tests/test_demo.py::test_demo",
    ]
    assert tenth_confidence["level"] == "recovered_outcome_supported"
    assert tenth_confidence["success_count"] == 1
    assert tenth_confidence["failed_or_rejected_count"] == 2
    assert tenth_confidence["recovery_count"] == 2
    assert tenth_confidence["unrecovered_failed_or_rejected_count"] == 0
    assert (
        tenth_confidence_decision["decision"]
        == "outcome_supported_direct_test"
    )
    assert tenth_confidence_decision["requires_owner_approval"] is True
    assert tenth_confidence_decision["executes_now"] is False
    assert tenth_mission_state["continuity"]["state"] == "waiting_approval"
    assert (
        "Recovered promoted confidence restored a direct narrow pytest action"
        in tenth_mission_state["continuity"]["summary"]
    )
    assert (
        "Approve or reject recovered direct pytest action"
        in tenth_mission_state["continuity"]["next_step"]
    )
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory + 3
    )
    runtime.approvals.issue(
        tenth_next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered direct narrow pytest target",
    )
    tenth_test_outcome = runtime.resume_action(tenth_next_step["action_id"])
    tenth_draft_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="from-test-result",
        action_id=tenth_next_step["action_id"],
    )
    tenth_draft_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (tenth_draft_step["action_id"],),
        )["arguments_json"]
    )
    tenth_learning = runtime._patch_mission_failure_learning_for_test(
        tenth_next_step["action_id"]
    )

    assert tenth_test_outcome["status"] == "SUCCEEDED"
    assert tenth_test_outcome["output"]["returncode"] != 0
    assert tenth_test_outcome["promoted_repair_skill_outcome"]["skill_id"] == skill_receipt["skill_id"]
    assert (
        runtime.db.query_one(
            "SELECT use_count FROM skills WHERE skill_id=?",
            (skill_receipt["skill_id"],),
        )["use_count"]
        == skill_use_count_before_advisory + 4
    )
    assert "promoted confidence `recovered_outcome_supported`" in tenth_draft_args["content"]
    assert "via `outcome_supported_direct_test`" in tenth_draft_args["content"]
    assert "recovery successes `2`" in tenth_draft_args["content"]
    assert "unrecovered failed_or_rejected `0`" in tenth_draft_args["content"]
    assert tenth_learning["pytest_target"] == "tests/test_demo.py::test_demo"
    assert "promoted confidence `recovered_outcome_supported`" in tenth_learning["ci_source"]
    assert "outcome_supported_direct_test" in tenth_learning["ci_source"]
    assert "recovery successes `2`" in tenth_learning["ci_source"]
    runtime.approvals.issue(
        tenth_draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered direct patch draft",
    )
    runtime.resume_action(tenth_draft_step["action_id"])
    tenth_apply_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="apply-patch",
        action_id=tenth_draft_step["action_id"],
    )
    runtime.approvals.issue(
        tenth_apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered direct repo patch write",
    )
    runtime.resume_action(tenth_apply_step["action_id"])
    tenth_verify_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        tenth_verify_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered direct passing verification",
    )
    tenth_verify_outcome = runtime.resume_action(tenth_verify_step["action_id"])
    assert tenth_verify_outcome["status"] == "SUCCEEDED"
    assert tenth_verify_outcome["output"]["returncode"] == 0
    tenth_summary_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="pr-summary",
        action_id=tenth_verify_step["action_id"],
    )
    tenth_summary_args = json.loads(
        runtime.db.query_one(
            "SELECT arguments_json FROM actions WHERE action_id=?",
            (tenth_summary_step["action_id"],),
        )["arguments_json"]
    )
    successful_repairs = runtime.db.get_runtime(
        "patch_mission_successful_repair_learning",
        [],
    )

    assert tenth_summary_step["mode"] == "pr-summary"
    assert "promoted confidence `recovered_outcome_supported`" in tenth_summary_args["content"]
    assert "outcome_supported_direct_test" in tenth_summary_args["content"]
    assert "recovery successes `2`" in tenth_summary_args["content"]
    assert "unrecovered failed_or_rejected `0`" in tenth_summary_args["content"]
    assert successful_repairs[0]["failing_test_action_id"] == tenth_next_step["action_id"]
    assert "promoted confidence `recovered_outcome_supported`" in successful_repairs[0]["ci_source"]
    assert "outcome_supported_direct_test" in successful_repairs[0]["ci_source"]
    assert "recovery successes `2`" in successful_repairs[0]["ci_source"]
    assert (
        len(
            runtime.db.get_runtime(
                "patch_mission_promoted_skill_recovery_outcomes",
                [],
            )
        )
        == 2
    )
    runtime.approvals.issue(
        tenth_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered direct PR summary",
    )
    runtime.resume_action(tenth_summary_step["action_id"])
    tenth_metadata_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="resume-next",
    )
    tenth_prep_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="resume-next",
    )
    tenth_prep_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json FROM actions WHERE action_id=?",
        (tenth_prep_step["action_id"],),
    )
    tenth_prep_args = json.loads(tenth_prep_row["arguments_json"])

    assert tenth_metadata_step["mode"] == "git-metadata"
    assert tenth_metadata_step["requested_mode"] == "resume-next"
    assert tenth_metadata_step["resume_next"]["from_mode"] == "pr-summary"
    assert (
        tenth_metadata_step["resume_next"]["from_action_id"]
        == tenth_summary_step["action_id"]
    )
    assert tenth_metadata_step["outcomes"][0]["success"] is True
    assert tenth_metadata_step["outcomes"][0]["output"]["branch_status"]["returncode"] == 0
    assert "demo.py" in tenth_metadata_step["outcomes"][0]["output"]["diff_stat"]["stdout"]
    assert tenth_prep_step["mode"] == "git-prep"
    assert tenth_prep_step["requested_mode"] == "resume-next"
    assert tenth_prep_step["source_action_id"] == tenth_metadata_step["action_id"]
    assert tenth_prep_row["tool"] == "write_file"
    assert tenth_prep_row["status"] == "WAITING_APPROVAL"
    assert tenth_prep_row["risk"] == "REVERSIBLE_WRITE"
    assert "Patch Mission Commit-Ready Checklist" in tenth_prep_args["content"]
    assert "demo.py" in tenth_prep_args["content"]
    assert "No commit, branch creation, push, or pull request" in tenth_prep_args["content"]
    assert str(runtime.config.outbox_path) in tenth_prep_args["path"]
    assert str(tenth_repo.resolve()) not in tenth_prep_args["path"]
    tenth_commit_count_before = git_output(tenth_repo, ["rev-list", "--count", "HEAD"]).strip()
    runtime.approvals.issue(
        tenth_prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered commit checklist",
    )
    runtime.resume_action(tenth_prep_step["action_id"])
    tenth_commit_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="resume-next",
    )
    tenth_commit_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json FROM actions WHERE action_id=?",
        (tenth_commit_step["action_id"],),
    )
    tenth_commit_args = json.loads(tenth_commit_row["arguments_json"])

    assert tenth_commit_step["mode"] == "commit-draft"
    assert tenth_commit_step["requested_mode"] == "resume-next"
    assert tenth_commit_step["source_action_id"] == tenth_prep_step["action_id"]
    assert tenth_commit_row["tool"] == "run_command"
    assert tenth_commit_row["status"] == "WAITING_APPROVAL"
    assert tenth_commit_row["risk"] == "HIGH"
    assert tenth_commit_args["cwd"] == str(tenth_repo.resolve())
    assert tenth_commit_args["command"][:7] == [
        "git",
        "-c",
        "user.name=WLS",
        "-c",
        "user.email=wls@example.invalid",
        "commit",
        "-am",
    ]
    assert "Use recovered promoted skill confidence" in tenth_commit_args["command"][7]
    assert tenth_commit_args["command"][8] == "-m"
    assert "Changed files: demo.py" in tenth_commit_args["command"][9]
    assert "No push or pull request was created by this action." in tenth_commit_args["command"][9]
    assert git_output(tenth_repo, ["rev-list", "--count", "HEAD"]).strip() == tenth_commit_count_before
    runtime.approvals.issue(
        tenth_commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves recovered local commit",
    )
    tenth_commit_result = runtime.resume_action(tenth_commit_step["action_id"])
    tenth_remote_metadata_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="resume-next",
    )
    tenth_remote_summary_step = runtime.continue_patch_mission(
        mission_id=tenth_mission["mission_id"],
        mode="resume-next",
    )
    tenth_remote_summary_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json FROM actions WHERE action_id=?",
        (tenth_remote_summary_step["action_id"],),
    )
    tenth_remote_summary_args = json.loads(tenth_remote_summary_row["arguments_json"])
    tenth_log = git_output(tenth_repo, ["log", "-1", "--pretty=%B"])

    assert tenth_commit_result["status"] == "SUCCEEDED"
    assert (
        git_output(tenth_repo, ["rev-list", "--count", "HEAD"]).strip()
        == str(int(tenth_commit_count_before) + 1)
    )
    assert "Use recovered promoted skill confidence" in tenth_log
    assert "Changed files: demo.py" in tenth_log
    assert "No push or pull request was created by this action." in tenth_log
    assert tenth_remote_metadata_step["mode"] == "git-metadata"
    assert tenth_remote_metadata_step["requested_mode"] == "resume-next"
    assert tenth_remote_metadata_step["resume_next"]["from_mode"] == "commit-draft"
    assert (
        tenth_remote_metadata_step["resume_next"]["from_action_id"]
        == tenth_commit_step["action_id"]
    )
    assert tenth_remote_metadata_step["outcomes"][0]["success"] is True
    assert tenth_remote_summary_step["mode"] == "remote-summary"
    assert tenth_remote_summary_step["requested_mode"] == "resume-next"
    assert (
        tenth_remote_summary_step["source_action_id"]
        == tenth_remote_metadata_step["action_id"]
    )
    assert tenth_remote_summary_row["tool"] == "write_file"
    assert tenth_remote_summary_row["status"] == "WAITING_APPROVAL"
    assert tenth_remote_summary_row["risk"] == "REVERSIBLE_WRITE"
    assert "Patch Mission Remote Readiness Summary" in tenth_remote_summary_args["content"]
    assert "No push, PR creation, comment, or merge is performed" in tenth_remote_summary_args["content"]
    assert (
        runtime.db.query_all(
            """
            SELECT tool FROM actions
            WHERE tool IN ('git_push', 'create_github_pr', 'create_github_pull_request')
            """
        )
        == []
    )


def test_patch_mission_pr_update_push_draft_updates_existing_pr_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    git_check(repo, ["checkout", "-b", "wls/fix-greeting"])
    remote = initialize_bare_remote(tmp_path / "remote.git", repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Update an existing PR branch after failed CI",
        execute_first_action=False,
    )
    github_state = {"updated": False, "post_sha": "def456"}

    def fake_get(url: str, *, token: str | None = None) -> dict[str, object]:
        if url.endswith("/pulls/7"):
            sha = github_state["post_sha"] if github_state["updated"] else "abc123"
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"head": {"sha": sha, "ref": "wls/fix-greeting"}},
            }
        if "/check-runs" in url:
            if github_state["updated"]:
                return {
                    "ok": True,
                    "status": 200,
                    "url": url,
                    "body": {
                        "check_runs": [
                            {
                                "name": "pytest",
                                "status": "completed",
                                "conclusion": "success",
                                "details_url": "https://github.com/example/demo/actions/runs/4",
                                "output": {
                                    "title": "pytest passed",
                                    "summary": "all tests passed",
                                },
                            }
                        ]
                    },
                }
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "check_runs": [
                        {
                            "name": "pytest",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": "https://github.com/example/demo/actions/runs/1",
                            "output": {
                                "title": "pytest failed",
                                "summary": "tests/test_demo.py::test_demo failed",
                            },
                        }
                    ]
                },
            }
        if url.endswith("/status"):
            return {"ok": True, "status": 200, "url": url, "body": {"statuses": []}}
        if "/actions/runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"workflow_runs": []},
            }
        raise AssertionError(url)

    monkeypatch.setattr(
        runtime.tools,
        "_github_api_get_with_optional_token",
        fake_get,
    )
    status_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status inspection",
    )
    runtime.resume_action(status_step["action_id"])
    plan_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="ci-fix-plan",
        action_id=status_step["action_id"],
    )
    runtime.approvals.issue(
        plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI fix plan outbox write",
    )
    runtime.resume_action(plan_step["action_id"])
    next_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="ci-next-action",
        action_id=plan_step["action_id"],
    )
    runtime.approvals.issue(
        next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-triggered local pytest reproduction",
    )
    runtime.resume_action(next_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="from-test-result",
        action_id=next_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-sourced outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-sourced exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing local verification after CI fix",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    runtime.approvals.issue(
        summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI-sourced PR summary",
    )
    runtime.resume_action(summary_step["action_id"])
    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-metadata",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="git-prep",
        action_id=metadata_step["action_id"],
    )
    runtime.approvals.issue(
        prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI repair commit checklist",
    )
    runtime.resume_action(prep_step["action_id"])
    commit_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="commit-draft",
        action_id=prep_step["action_id"],
    )
    runtime.approvals.issue(
        commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI repair local commit",
    )
    commit_result = runtime.resume_action(commit_step["action_id"])
    local_sha = git_output(repo, ["rev-parse", "HEAD"]).strip()
    before_remote = git_check(
        repo, ["ls-remote", "--heads", str(remote), "wls/fix-greeting"]
    ).stdout

    push_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-update-push-draft",
        action_id=status_step["action_id"],
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (push_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert commit_result["status"] == "SUCCEEDED"
    assert local_sha not in before_remote
    assert push_step["mode"] == "pr-update-push-draft"
    assert push_step["source_action_id"] == status_step["action_id"]
    assert push_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert row["tool"] == "run_command"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "HIGH"
    assert row["error"] == "runtime is in read-only mode"
    assert args["command"] == ["git", "push", "origin", "HEAD:wls/fix-greeting"]
    assert "--force" not in args["command"]

    runtime.approvals.issue(
        push_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact PR branch update push",
    )
    push_result = runtime.resume_action(push_step["action_id"])
    after_remote = git_check(
        repo, ["ls-remote", "--heads", str(remote), "wls/fix-greeting"]
    ).stdout

    assert push_result["status"] == "SUCCEEDED"
    assert push_result["output"]["returncode"] == 0
    assert local_sha in after_remote

    github_state["updated"] = True
    github_state["post_sha"] = local_sha
    post_status_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-update-status",
        action_id=push_step["action_id"],
    )
    post_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (post_status_step["action_id"],),
    )
    post_args = json.loads(post_row["arguments_json"])

    assert post_status_step["mode"] == "pr-update-status"
    assert post_status_step["source_action_id"] == push_step["action_id"]
    assert post_status_step["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert post_row["tool"] == "inspect_github_pr_status"
    assert post_row["status"] == "WAITING_APPROVAL"
    assert post_row["risk"] == "HIGH"
    assert post_args["owner"] == "example"
    assert post_args["repo"] == "demo"
    assert post_args["number"] == 7

    runtime.approvals.issue(
        post_status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves post-update PR status inspection",
    )
    post_status_result = runtime.resume_action(post_status_step["action_id"])
    verify_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-update-verify",
        action_id=post_status_step["action_id"],
    )
    verify_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (verify_step["action_id"],),
    )
    verify_args = json.loads(verify_row["arguments_json"])

    assert post_status_result["status"] == "SUCCEEDED"
    assert post_status_result["output"]["head_sha"] == local_sha
    assert verify_step["mode"] == "pr-update-verify"
    assert verify_step["source_action_id"] == post_status_step["action_id"]
    assert verify_row["tool"] == "write_file"
    assert verify_row["status"] == "WAITING_APPROVAL"
    assert verify_row["risk"] == "REVERSIBLE_WRITE"
    assert "# Patch Mission PR Update Verification" in verify_args["content"]
    assert "Before: abc123" in verify_args["content"]
    assert f"After: {local_sha}" in verify_args["content"]
    assert "Changed: True" in verify_args["content"]
    assert "Before: 1" in verify_args["content"]
    assert "After: 0" in verify_args["content"]
    assert "does not comment, review, merge, force push" in verify_args["content"]
    assert not Path(verify_args["path"]).exists()

    runtime.approvals.issue(
        verify_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR update verification note",
    )
    runtime.resume_action(verify_step["action_id"])
    next_decision_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    next_decision_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (next_decision_step["action_id"],),
    )
    next_decision_args = json.loads(next_decision_row["arguments_json"])

    assert next_decision_step["mode"] == "pr-update-next"
    assert next_decision_step["requested_mode"] == "resume-next"
    assert next_decision_step["resume_next"]["from_mode"] == "pr-update-verify"
    assert next_decision_step["source_action_id"] == verify_step["action_id"]
    assert next_decision_row["tool"] == "write_file"
    assert next_decision_row["status"] == "WAITING_APPROVAL"
    assert next_decision_row["risk"] == "REVERSIBLE_WRITE"
    assert "# Patch Mission PR Update Next Step" in next_decision_args["content"]
    assert "Post-update failure count: 0" in next_decision_args["content"]
    assert "awaiting owner review" in next_decision_args["content"]
    assert "does not comment, review, merge, force push" in next_decision_args["content"]
    assert not Path(next_decision_args["path"]).exists()

    runtime.approvals.issue(
        next_decision_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR update next-step note",
    )
    runtime.resume_action(next_decision_step["action_id"])
    continuity = runtime.patch_missions()[0]["continuity"]
    assert continuity["state"] == "ready_for_owner_review"
    assert continuity["latest_mode"] == "pr-update-next"
    assert continuity["latest_action_id"] == next_decision_step["action_id"]


def test_patch_mission_pr_update_next_reopens_ci_fix_plan_when_failures_remain(
    tmp_path: Path,
) -> None:
    repo = make_failing_repo(tmp_path / "project")
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Continue repair after post-update CI still fails",
        execute_first_action=False,
    )
    status_action = runtime._patch_mission_pr_status_action(
        mission_record=mission,
        target="https://github.com/example/demo/pull/7",
        action_id=None,
        goal_id=mission["goal_id"],
    )
    status_output = {
        "owner": "example",
        "repo": "demo",
        "number": 7,
        "url": "https://github.com/example/demo/pull/7",
        "head_sha": "def456",
        "head_ref": "wls/fix-greeting",
        "pull_request": {"ok": True, "body": {"head": {"sha": "def456"}}},
        "check_runs": {"ok": True},
        "failure_summary": [
            {
                "kind": "check_run",
                "name": "pytest",
                "conclusion": "failure",
                "summary": "tests/test_demo.py failed again",
            }
        ],
    }
    status_payload = {
        "result": {
            "action_id": status_action.action_id,
            "success": True,
            "output": status_output,
            "started_at": "2026-07-15T00:00:00+00:00",
            "finished_at": "2026-07-15T00:00:01+00:00",
            "error": None,
            "observed_side_effect": False,
            "result_id": "result_test",
        },
        "evaluation": {"accepted": True, "checks": []},
    }
    runtime.db.execute(
        """
        INSERT INTO actions(action_id,plan_id,goal_id,skill_id,tool,arguments_json,
                            purpose,expected_result,risk,acceptance_json,
                            idempotency_key,status,result_json,side_effect_class)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            status_action.action_id,
            mission["plan_id"],
            mission["goal_id"],
            status_action.skill_id,
            status_action.tool,
            json.dumps(status_action.arguments, ensure_ascii=False, sort_keys=True),
            status_action.purpose,
            status_action.expected_result,
            status_action.risk.value,
            json.dumps(status_action.acceptance, ensure_ascii=False),
            status_action.idempotency_key,
            "SUCCEEDED",
            json.dumps(status_payload, ensure_ascii=False, sort_keys=True),
            runtime.tools.get(status_action.tool).side_effect_class,
        ),
    )
    verify_content = "\n".join(
        [
            "# Patch Mission PR Update Verification",
            "",
            f"Mission ID: {mission['mission_id']}",
            "PR URL: https://github.com/example/demo/pull/7",
            "Pre-update PR status action: act_pre",
            "Update push action: act_push",
            f"Post-update PR status action: {status_action.action_id}",
            "",
            "## Failure Summary Counts",
            "- Before: 1",
            "- After: 1",
        ]
    )
    verify_action = ActionSpec(
        tool="write_file",
        arguments={
            "path": str(
                runtime.config.outbox_path
                / "patch-missions"
                / mission["mission_id"]
                / "pr-update-verify.md"
            ),
            "content": verify_content,
        },
        purpose="test",
        expected_result="test",
        risk=RiskLevel.REVERSIBLE_WRITE,
        goal_id=mission["goal_id"],
    )
    runtime.db.execute(
        """
        INSERT INTO actions(action_id,plan_id,goal_id,skill_id,tool,arguments_json,
                            purpose,expected_result,risk,acceptance_json,
                            idempotency_key,status,side_effect_class)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            verify_action.action_id,
            mission["plan_id"],
            mission["goal_id"],
            verify_action.skill_id,
            verify_action.tool,
            json.dumps(verify_action.arguments, ensure_ascii=False, sort_keys=True),
            verify_action.purpose,
            verify_action.expected_result,
            verify_action.risk.value,
            json.dumps(verify_action.acceptance, ensure_ascii=False),
            verify_action.idempotency_key,
            "SUCCEEDED",
            runtime.tools.get(verify_action.tool).side_effect_class,
        ),
    )
    verify_action_id = verify_action.action_id
    current = runtime._patch_mission_record(mission["mission_id"])
    updated = runtime._update_patch_mission_record(
        mission["mission_id"],
        {
            "step_id": "patch_step_test_status",
            "mission_id": mission["mission_id"],
            "mode": "pr-update-status",
            "target": None,
            "source_action_id": "act_push",
            "cycle_id": "cycle_test_status",
            "plan_id": "plan_test_status",
            "action_id": status_action.action_id,
            "action": status_action.to_dict(),
            "outcomes": [{"status": "SUCCEEDED"}],
            "plan_status": "SUCCEEDED",
        },
    )
    runtime._update_patch_mission_record(
        mission["mission_id"],
        {
            "step_id": "patch_step_test_verify",
            "mission_id": mission["mission_id"],
            "mode": "pr-update-verify",
            "target": None,
            "source_action_id": status_action.action_id,
            "cycle_id": "cycle_test_verify",
            "plan_id": "plan_test_verify",
            "action_id": verify_action_id,
            "action": verify_action.to_dict(),
            "outcomes": [{"status": "SUCCEEDED"}],
            "plan_status": "SUCCEEDED",
        },
    )

    next_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="pr-update-next",
        action_id=verify_action_id,
    )
    row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (next_step["action_id"],),
    )
    args = json.loads(row["arguments_json"])

    assert updated["mission_id"] == mission["mission_id"]
    assert current["mission_id"] == mission["mission_id"]
    assert next_step["mode"] == "pr-update-next"
    assert row["tool"] == "write_file"
    assert row["status"] == "WAITING_APPROVAL"
    assert row["risk"] == "REVERSIBLE_WRITE"
    assert "# Patch Mission CI Fix Plan" in args["content"]
    assert "tests/test_demo.py" in args["content"]
    assert "Mode: `test`" in args["content"]
    continuity = runtime.patch_missions()[0]["continuity"]
    assert continuity["state"] == "waiting_approval"
    assert continuity["blocking_mode"] == "pr-update-next"
    assert continuity["blocking_action_id"] == next_step["action_id"]

    runtime.approvals.issue(
        next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves next CI fix plan after post-update failures",
    )
    runtime.resume_action(next_step["action_id"])
    resumed_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    resumed_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (resumed_step["action_id"],),
    )
    resumed_args = json.loads(resumed_row["arguments_json"])

    assert resumed_step["mode"] == "ci-next-action"
    assert resumed_step["requested_mode"] == "resume-next"
    assert resumed_step["source_action_id"] == next_step["action_id"]
    assert resumed_row["tool"] == "run_command"
    assert resumed_row["status"] == "WAITING_APPROVAL"
    assert resumed_row["risk"] == "HIGH"
    assert resumed_args["command"] == ["python", "-m", "pytest", "tests/test_demo.py"]


def test_github_pr_status_tool_extracts_ci_failure_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)

    def fake_get(url: str, *, token: str | None = None) -> dict[str, object]:
        if url.endswith("/pulls/7"):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {"head": {"sha": "abc123", "ref": "wls/fix-greeting"}},
            }
        if "/check-runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "check_runs": [
                        {
                            "name": "pytest",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": "https://github.com/example/demo/actions/runs/1",
                            "output": {
                                "title": "pytest failed",
                                "summary": "tests/test_demo.py failed",
                            },
                        }
                    ]
                },
            }
        if url.endswith("/status"):
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "statuses": [
                        {
                            "context": "lint",
                            "state": "error",
                            "target_url": "https://github.com/example/demo/actions/runs/2",
                            "description": "ruff failed",
                        }
                    ]
                },
            }
        if "/actions/runs" in url:
            return {
                "ok": True,
                "status": 200,
                "url": url,
                "body": {
                    "workflow_runs": [
                        {
                            "name": "CI",
                            "status": "completed",
                            "conclusion": "failure",
                            "html_url": "https://github.com/example/demo/actions/runs/3",
                        }
                    ]
                },
            }
        raise AssertionError(url)

    monkeypatch.setattr(
        runtime.tools,
        "_github_api_get_with_optional_token",
        fake_get,
    )

    output = runtime.tools._inspect_github_pr_status(
        {"owner": "example", "repo": "demo", "number": 7}
    )

    assert output["writes_remote"] is False
    assert output["head_sha"] == "abc123"
    assert output["head_ref"] == "wls/fix-greeting"
    assert output["failure_summary"] == [
        {
            "kind": "check_run",
            "name": "pytest",
            "status": "completed",
            "conclusion": "failure",
            "details_url": "https://github.com/example/demo/actions/runs/1",
            "title": "pytest failed",
            "summary": "tests/test_demo.py failed",
        },
        {
            "kind": "commit_status",
            "context": "lint",
            "state": "error",
            "target_url": "https://github.com/example/demo/actions/runs/2",
            "description": "ruff failed",
        },
        {
            "kind": "workflow_run",
            "name": "CI",
            "status": "completed",
            "conclusion": "failure",
            "html_url": "https://github.com/example/demo/actions/runs/3",
        },
    ]


def test_cli_patch_mission_returns_executable_mission_payload(
    tmp_path: Path,
    capsys,
) -> None:
    home = tmp_path / "home"
    repo = make_repo(tmp_path / "repo")
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "patch-mission",
                str(repo),
                "Fix the greeting behavior",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["status"] == "STARTED"
    assert payload["repo_path"] == str(repo.resolve())
    assert payload["outcomes"][0]["success"] is True
    assert payload["next_action_candidate"]["source"] == "planner"


def test_cli_patch_mission_step_runs_inspect_file(
    tmp_path: Path,
    capsys,
) -> None:
    home = tmp_path / "home"
    repo = make_repo(tmp_path / "repo")
    config_path = home / "config.json"
    assert cli.main(["--config", str(config_path), "init", "--home", str(home)]) == 0
    capsys.readouterr()
    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "patch-mission",
                str(repo),
                "Fix the greeting behavior",
                "--no-execute",
            ]
        )
        == 0
    )
    mission = json.loads(capsys.readouterr().out)

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "patch-mission-step",
                "--mission-id",
                mission["mission_id"],
                "--mode",
                "inspect-file",
                "--target",
                "tests/test_demo.py",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "inspect-file"
    assert payload["outcomes"][0]["success"] is True


def test_cli_patch_mission_step_from_test_result_creates_outbox_draft(
    tmp_path: Path,
    capsys,
) -> None:
    home = tmp_path / "home"
    repo = make_failing_repo(tmp_path / "repo")
    config_path = home / "config.json"
    config = default_config(home)
    config.sensors = []
    save_config(config, config_path)
    runtime = LivingSystem(config)
    mission = runtime.start_patch_mission(
        repo_path=repo,
        mission="Fix the failing greeting behavior",
        execute_first_action=False,
    )
    test_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        test_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves local pytest probe",
    )
    runtime.resume_action(test_step["action_id"])
    runtime.db.close_all()

    assert (
        cli.main(
            [
                "--config",
                str(config_path),
                "patch-mission-step",
                "--mission-id",
                mission["mission_id"],
                "--mode",
                "from-test-result",
                "--action-id",
                test_step["action_id"],
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert payload["mode"] == "from-test-result"
    assert payload["source_action_id"] == test_step["action_id"]
    assert payload["outcomes"][0]["status"] == "WAITING_APPROVAL"
    assert "tests/test_demo.py" in payload["action"]["arguments"]["content"]
    assert "Patch target: demo.py" in payload["action"]["arguments"]["content"]
    assert "-    return 'hi'" in payload["action"]["arguments"]["content"]
    assert "+    return 'hello'" in payload["action"]["arguments"]["content"]


def test_patch_mission_resume_next_continues_local_git_publish_prep(
    tmp_path: Path,
) -> None:
    runtime, mission, repo, summary_step = prepare_verified_patch_summary(
        tmp_path,
        mission="Resume the verified patch into local git prep",
    )

    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    prep_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (prep_step["action_id"],),
    )

    assert metadata_step["mode"] == "git-metadata"
    assert metadata_step["requested_mode"] == "resume-next"
    assert metadata_step["resume_next"]["from_mode"] == "pr-summary"
    assert metadata_step["resume_next"]["from_action_id"] == summary_step["action_id"]
    assert metadata_step["outcomes"][0]["success"] is True
    assert prep_step["mode"] == "git-prep"
    assert prep_step["requested_mode"] == "resume-next"
    assert prep_step["source_action_id"] == metadata_step["action_id"]
    assert prep_row["tool"] == "write_file"
    assert prep_row["status"] == "WAITING_APPROVAL"
    assert prep_row["risk"] == "REVERSIBLE_WRITE"

    runtime.approvals.issue(
        prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed commit checklist",
    )
    runtime.resume_action(prep_step["action_id"])
    commit_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    commit_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (commit_step["action_id"],),
    )

    assert commit_step["mode"] == "commit-draft"
    assert commit_step["requested_mode"] == "resume-next"
    assert commit_step["source_action_id"] == prep_step["action_id"]
    assert commit_row["tool"] == "run_command"
    assert commit_row["status"] == "WAITING_APPROVAL"
    assert commit_row["risk"] == "HIGH"

    runtime.approvals.issue(
        commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed local commit",
    )
    commit_result = runtime.resume_action(commit_step["action_id"])
    remote_metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    remote_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    remote_summary_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (remote_summary_step["action_id"],),
    )

    assert commit_result["status"] == "SUCCEEDED"
    assert git_output(repo, ["rev-list", "--count", "HEAD"]).strip() == "2"
    assert remote_metadata_step["mode"] == "git-metadata"
    assert remote_metadata_step["requested_mode"] == "resume-next"
    assert remote_metadata_step["resume_next"]["from_mode"] == "commit-draft"
    assert remote_summary_step["mode"] == "remote-summary"
    assert remote_summary_step["requested_mode"] == "resume-next"
    assert remote_summary_step["source_action_id"] == remote_metadata_step["action_id"]
    assert remote_summary_row["tool"] == "write_file"
    assert remote_summary_row["status"] == "WAITING_APPROVAL"
    assert remote_summary_row["risk"] == "REVERSIBLE_WRITE"

    runtime.approvals.issue(
        remote_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed remote summary",
    )
    runtime.resume_action(remote_summary_step["action_id"])
    branch_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    branch_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (branch_step["action_id"],),
    )
    branch_args = json.loads(branch_row["arguments_json"])
    branch_name = branch_args["command"][-1]

    assert branch_step["mode"] == "branch-draft"
    assert branch_step["requested_mode"] == "resume-next"
    assert branch_step["source_action_id"] == remote_summary_step["action_id"]
    assert branch_row["tool"] == "run_command"
    assert branch_row["status"] == "WAITING_APPROVAL"
    assert branch_row["risk"] == "HIGH"
    assert branch_name.startswith("wls/")

    runtime.approvals.issue(
        branch_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed local branch",
    )
    runtime.resume_action(branch_step["action_id"])
    live_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    live_row = runtime.db.query_one(
        "SELECT tool,status,risk FROM actions WHERE action_id=?",
        (live_step["action_id"],),
    )

    assert live_step["mode"] == "remote-live"
    assert live_step["requested_mode"] == "resume-next"
    assert live_row["tool"] == "inspect_git_remote_live"
    assert live_row["status"] == "WAITING_APPROVAL"
    assert live_row["risk"] == "HIGH"

    runtime.approvals.issue(
        live_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed live remote inspection",
    )
    runtime.resume_action(live_step["action_id"])
    live_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    live_summary_row = runtime.db.query_one(
        "SELECT tool,status,risk FROM actions WHERE action_id=?",
        (live_summary_step["action_id"],),
    )

    assert live_summary_step["mode"] == "remote-live-summary"
    assert live_summary_step["requested_mode"] == "resume-next"
    assert live_summary_step["source_action_id"] == live_step["action_id"]
    assert live_summary_row["tool"] == "write_file"
    assert live_summary_row["status"] == "WAITING_APPROVAL"
    assert live_summary_row["risk"] == "REVERSIBLE_WRITE"

    runtime.approvals.issue(
        live_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed live remote summary",
    )
    runtime.resume_action(live_summary_step["action_id"])
    push_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    push_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (push_step["action_id"],),
    )
    push_args = json.loads(push_row["arguments_json"])

    assert push_step["mode"] == "push-draft"
    assert push_step["requested_mode"] == "resume-next"
    assert push_step["source_action_id"] == live_summary_step["action_id"]
    assert push_row["tool"] == "run_command"
    assert push_row["status"] == "WAITING_APPROVAL"
    assert push_row["risk"] == "HIGH"
    assert push_args["command"] == ["git", "push", "-u", "origin", branch_name]


def test_patch_mission_resume_next_prepares_pr_draft_after_push(
    tmp_path: Path,
) -> None:
    runtime, mission, repo, remote, branch_name, push_step = prepare_pushed_patch_branch(
        tmp_path,
        mission="Resume from pushed branch to draft PR",
    )
    git_check(repo, ["remote", "set-url", "origin", "https://github.com/example/demo.git"])

    metadata_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    remote_summary_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    runtime.approvals.issue(
        remote_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves resumed GitHub remote evidence summary",
    )
    runtime.resume_action(remote_summary_step["action_id"])
    pr_step = runtime.continue_patch_mission(
        mission_id=mission["mission_id"],
        mode="resume-next",
    )
    pr_row = runtime.db.query_one(
        "SELECT tool,status,risk,arguments_json,error FROM actions WHERE action_id=?",
        (pr_step["action_id"],),
    )
    pr_args = json.loads(pr_row["arguments_json"])
    pushed_remote = git_check(
        repo, ["ls-remote", "--heads", str(remote), branch_name]
    ).stdout

    assert metadata_step["mode"] == "git-metadata"
    assert metadata_step["requested_mode"] == "resume-next"
    assert metadata_step["resume_next"]["from_mode"] == "push-draft"
    assert remote_summary_step["mode"] == "remote-summary"
    assert remote_summary_step["requested_mode"] == "resume-next"
    assert remote_summary_step["source_action_id"] == metadata_step["action_id"]
    assert pr_step["mode"] == "pr-create-draft"
    assert pr_step["requested_mode"] == "resume-next"
    assert pr_step["source_action_id"] == push_step["action_id"]
    assert pr_row["tool"] == "create_github_pull_request"
    assert pr_row["status"] == "WAITING_APPROVAL"
    assert pr_row["risk"] == "HIGH"
    assert pr_args["owner"] == "example"
    assert pr_args["repo"] == "demo"
    assert pr_args["head"] == branch_name
    assert pr_args["draft"] is True
    assert branch_name in pushed_remote


def prepare_verified_patch_summary(
    tmp_path: Path,
    *,
    mission: str,
) -> tuple[LivingSystem, dict[str, object], Path, dict[str, object]]:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    initialize_bare_remote(tmp_path / "remote.git", repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission_record = runtime.start_patch_mission(
        repo_path=repo,
        mission=mission,
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing pytest verification",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    runtime.approvals.issue(
        summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR summary outbox write",
    )
    runtime.resume_action(summary_step["action_id"])
    return runtime, mission_record, repo, summary_step


def prepare_pushed_patch_branch(
    tmp_path: Path,
    *,
    mission: str,
) -> tuple[LivingSystem, dict[str, object], Path, Path, str, dict[str, object]]:
    repo = make_failing_repo(tmp_path / "project")
    initialize_git_repo(repo)
    remote = initialize_bare_remote(tmp_path / "remote.git", repo)
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystem(config)
    mission_record = runtime.start_patch_mission(
        repo_path=repo,
        mission=mission,
        execute_first_action=False,
    )
    failing_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        failing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves failing pytest probe",
    )
    runtime.resume_action(failing_step["action_id"])
    draft_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="from-test-result",
        action_id=failing_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves outbox patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo file write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing pytest verification",
    )
    runtime.resume_action(passing_step["action_id"])
    summary_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )
    runtime.approvals.issue(
        summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves PR summary outbox write",
    )
    runtime.resume_action(summary_step["action_id"])
    metadata_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="git-metadata",
    )
    prep_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="git-prep",
        action_id=metadata_step["action_id"],
    )
    runtime.approvals.issue(
        prep_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves commit checklist outbox write",
    )
    runtime.resume_action(prep_step["action_id"])
    commit_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="commit-draft",
        action_id=prep_step["action_id"],
    )
    runtime.approvals.issue(
        commit_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local commit",
    )
    runtime.resume_action(commit_step["action_id"])
    remote_metadata_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="git-metadata",
    )
    remote_summary_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="remote-summary",
        action_id=remote_metadata_step["action_id"],
    )
    runtime.approvals.issue(
        remote_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves remote summary outbox write",
    )
    runtime.resume_action(remote_summary_step["action_id"])
    branch_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="branch-draft",
        action_id=remote_summary_step["action_id"],
    )
    branch_name = branch_step["action"]["arguments"]["command"][-1]
    runtime.approvals.issue(
        branch_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact local branch creation",
    )
    runtime.resume_action(branch_step["action_id"])
    live_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="remote-live",
    )
    runtime.approvals.issue(
        live_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves live remote inspection",
    )
    runtime.resume_action(live_step["action_id"])
    live_summary_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="remote-live-summary",
        action_id=live_step["action_id"],
    )
    runtime.approvals.issue(
        live_summary_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves live remote summary outbox write",
    )
    runtime.resume_action(live_summary_step["action_id"])
    push_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="push-draft",
        action_id=live_summary_step["action_id"],
    )
    runtime.approvals.issue(
        push_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact branch push",
    )
    runtime.resume_action(push_step["action_id"])
    return runtime, mission_record, repo, remote, branch_name, push_step


def run_ci_literal_repair_to_summary(
    runtime: LivingSystem, *, repo: Path, mission: str
) -> dict[str, object]:
    mission_record = runtime.start_patch_mission(
        repo_path=repo,
        mission=mission,
        execute_first_action=False,
    )
    status_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="pr-status",
        target="https://github.com/example/demo/pull/7",
    )
    runtime.approvals.issue(
        status_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves read-only PR status inspection",
    )
    runtime.resume_action(status_step["action_id"])
    plan_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="ci-fix-plan",
        action_id=status_step["action_id"],
    )
    runtime.approvals.issue(
        plan_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves CI fix plan outbox write",
    )
    runtime.resume_action(plan_step["action_id"])
    next_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="ci-next-action",
        action_id=plan_step["action_id"],
    )
    runtime.approvals.issue(
        next_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves narrow pytest reproduction",
    )
    failed = runtime.resume_action(next_step["action_id"])
    assert failed["status"] == "SUCCEEDED"
    assert failed["output"]["returncode"] != 0
    draft_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="from-test-result",
        action_id=next_step["action_id"],
    )
    runtime.approvals.issue(
        draft_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves evidence-derived patch draft",
    )
    runtime.resume_action(draft_step["action_id"])
    apply_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="apply-patch",
        action_id=draft_step["action_id"],
    )
    runtime.approvals.issue(
        apply_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves exact repo patch write",
    )
    runtime.resume_action(apply_step["action_id"])
    passing_step = runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="test",
    )
    runtime.approvals.issue(
        passing_step["action_id"],
        approve=True,
        ttl_minutes=30,
        reason="owner approves passing local verification",
    )
    passed = runtime.resume_action(passing_step["action_id"])
    assert passed["status"] == "SUCCEEDED"
    assert passed["output"]["returncode"] == 0
    return runtime.continue_patch_mission(
        mission_id=mission_record["mission_id"],
        mode="pr-summary",
        action_id=passing_step["action_id"],
    )


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "CONTRIBUTING.md").write_text(
        "Run pytest before proposing a patch.\nKeep changes minimal.\n",
        encoding="utf-8",
    )
    (path / "README.md").write_text("# Demo repo\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_demo.py").write_text("def test_demo():\n    assert True\n", encoding="utf-8")
    return path


def make_failing_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "README.md").write_text("# Failing demo repo\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    (path / "demo.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_demo.py").write_text(
        "from demo import greet\n\n"
        "def test_demo():\n"
        "    assert greet() == 'hello'\n",
        encoding="utf-8",
    )
    return path


def initialize_git_repo(path: Path) -> None:
    try:
        git_check(path, ["init"])
        git_check(path, ["add", "."])
        git_check(
            path,
            [
                "-c",
                "user.name=WLS Test",
                "-c",
                "user.email=wls@example.test",
                "commit",
                "-m",
                "baseline",
            ],
        )
    except FileNotFoundError:
        pytest.skip("git is not available")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"git initialization failed: {exc.stderr}")


def initialize_bare_remote(path: Path, source_repo: Path) -> Path:
    try:
        git_check(path.parent, ["init", "--bare", str(path)])
        git_check(source_repo, ["remote", "add", "origin", str(path)])
        git_check(source_repo, ["push", "-u", "origin", "HEAD"])
    except FileNotFoundError:
        pytest.skip("git is not available")
    except subprocess.CalledProcessError as exc:
        pytest.skip(f"bare remote initialization failed: {exc.stderr}")
    return path.resolve()


def git_output(path: Path, args: list[str]) -> str:
    try:
        completed = git_check(path, args)
    except FileNotFoundError:
        pytest.skip("git is not available")
    return completed.stdout


def git_check(path: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        text=True,
    )
