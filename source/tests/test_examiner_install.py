from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "source/scripts/install_examiner_overlay.py"
UNINSTALLER = ROOT / "source/scripts/uninstall_examiner_overlay.py"


def run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*args],
        check=check,
        text=True,
        capture_output=True,
    )


def git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run("git", "-C", str(repo), *args)


def make_repo(path: Path) -> None:
    run("git", "init", "-b", "feature/examiner-test", str(path))
    git(path, "config", "user.email", "examiner@example.invalid")
    git(path, "config", "user.name", "Examiner Test")
    (path / "README.md").write_text("fixture\n", encoding="utf-8")
    git(path, "add", ".")
    git(path, "commit", "-m", "fixture")


def test_installer_apply_and_hash_safe_rollback(tmp_path: Path):
    repo = tmp_path / "repo"
    make_repo(repo)
    dry = run(
        sys.executable,
        str(INSTALLER),
        str(repo),
        "--allow-head-mismatch",
    )
    assert '"apply": false' in dry.stdout

    run(
        sys.executable,
        str(INSTALLER),
        str(repo),
        "--allow-head-mismatch",
        "--apply",
    )
    receipt = repo / ".evolution/examiner/INSTALL_RECEIPT.json"
    assert receipt.exists()
    assert (repo / "source/src/wls/examiner/system.py").exists()

    run(sys.executable, str(UNINSTALLER), str(repo), "--apply")
    assert not receipt.exists()
    assert not (repo / "source/src/wls/examiner/system.py").exists()
    assert (repo / "README.md").read_text(encoding="utf-8") == "fixture\n"


def test_installer_conflict_preflight_writes_nothing(tmp_path: Path):
    repo = tmp_path / "repo"
    make_repo(repo)
    conflict = repo / "source/src/wls/examiner/system.py"
    conflict.parent.mkdir(parents=True, exist_ok=True)
    conflict.write_text("conflict\n", encoding="utf-8")
    result = run(
        sys.executable,
        str(INSTALLER),
        str(repo),
        "--allow-head-mismatch",
        "--allow-dirty",
        "--apply",
        check=False,
    )
    assert result.returncode != 0
    assert "non-identical files" in (result.stdout + result.stderr)
    assert not (repo / ".evolution/examiner/constitution.json").exists()
    assert not (repo / ".evolution/examiner/INSTALL_RECEIPT.json").exists()
    assert conflict.read_text(encoding="utf-8") == "conflict\n"


def test_uninstall_preserves_preexisting_identical_file(tmp_path: Path):
    repo = tmp_path / "repo"
    make_repo(repo)
    relative = Path("source/src/wls/examiner/__init__.py")
    destination = repo / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / relative, destination)
    git(repo, "add", ".")
    git(repo, "commit", "-m", "preexisting identical examiner file")

    run(
        sys.executable,
        str(INSTALLER),
        str(repo),
        "--allow-head-mismatch",
        "--apply",
    )
    run(sys.executable, str(UNINSTALLER), str(repo), "--apply")
    assert destination.exists()
    assert destination.read_bytes() == (ROOT / relative).read_bytes()
