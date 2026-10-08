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
