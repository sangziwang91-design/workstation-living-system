"""The same code task path policy must hold on Linux and Windows runners."""

from pathlib import Path

import pytest
from wls.coding_adapter import CodingTaskContract


def _contract(root: Path, changed: str) -> CodingTaskContract:
    return CodingTaskContract(
        task_id="cross-os",
        base_sha="base",
        worktree=root.resolve(),
        changed_files=[changed],
        tests=["python -m pytest -q"],
        rollback=["discard worktree"],
    )


def test_windows_relative_path_is_canonicalized_on_linux_too(tmp_path: Path) -> None:
    folder = tmp_path / "module"
    folder.mkdir()
    (folder / "part.py").write_text("value=1\n", encoding="utf-8")
    result = _contract(tmp_path, r"module\part.py").candidate_artifact()
    assert result["status"] == "CANDIDATE_ONLY"
    assert result["changed_files"][0]["path"] == "module/part.py"


@pytest.mark.parametrize("bad", [
    r"..\outside.py", "../outside.py", r"module\..\..\escape.py",
    r"C:\secrets\token.txt", r"C:relative-secret", r"\\server\share\secret.py",
])
def test_cross_platform_traversal_or_drive_is_rejected(tmp_path: Path, bad: str):
    with pytest.raises(ValueError, match="escapes worktree|relative paths"):
        _contract(tmp_path, bad).candidate_artifact()


@pytest.mark.parametrize("ads", [
    "module.py:stream", "src/module.py:$DATA",
    "module.py::$DATA", r"src\\module.py:payload",
])
def test_real_model_generated_ads_repair_rejects_hidden_ntfs_streams(
    tmp_path: Path, ads: str,
):
    # Tested against the actual WLS source model repair promoted by GitHub.
    # validate(), not candidate_artifact(), checks the policy independently
    # of whether a candidate file happens to exist on Linux.
    with pytest.raises(ValueError, match="relative paths"):
        _contract(tmp_path, ads).validate()



@pytest.mark.parametrize("poisoned", [
    "module\nother.py", "module\rother.py",
    "pkg/module\nnext.py", "pkg/module\rnext.py",
])
def test_second_real_model_generated_repair_rejects_crlf_audit_injection(
    tmp_path: Path, poisoned: str,
) -> None:
    # Qwen-generated predicate, independently graded then retained via GitHub.
    # This permanent test guards against later regression of that real repair.
    with pytest.raises(ValueError, match="relative paths"):
        _contract(tmp_path, poisoned).validate()



@pytest.mark.parametrize("bad", [
    "module\x00other.py", "src/module\x00next.py",
])
def test_third_real_model_generated_repair_rejects_nul_paths_with_canonical_error(
    tmp_path: Path, bad: str,
) -> None:
    # An actual Qwen2.5-Coder reply from GitHub run 37765939009 was
    # independently transformed via an equivalence-checked AST reduction,
    # red/green tested, and persisted in candidate run 37766766079.
    with pytest.raises(ValueError, match="changed files must be relative paths"):
        _contract(tmp_path, bad).validate()
