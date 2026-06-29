from __future__ import annotations

from pathlib import Path
import re
import subprocess


REPOSITORY = Path(__file__).resolve().parents[2]
FORBIDDEN_TRACKED_SUFFIXES = (".key", ".db", ".db-wal", ".db-shm", ".whl")
FORBIDDEN_TRACKED_PARTS = {
    "soak_home",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "build",
    "dist",
}
BUILD_SOURCE_PATTERN = re.compile(
    r"(?:python(?:\.exe)?|sys\.executable).*?-m\s+build\s+source\b",
    re.IGNORECASE | re.DOTALL,
)
VERSION_PATTERN = re.compile(
    r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$',
    re.MULTILINE,
)


def _tracked_files() -> list[Path]:
    completed = subprocess.run(
        ["git", "ls-files"],
        cwd=REPOSITORY,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    return [Path(line) for line in completed.stdout.splitlines() if line.strip()]


def test_repository_tracks_no_generated_runtime_or_build_artifacts() -> None:
    forbidden: list[str] = []
    for path in _tracked_files():
        if path.name.endswith(FORBIDDEN_TRACKED_SUFFIXES):
            forbidden.append(path.as_posix())
            continue
        if any(part in FORBIDDEN_TRACKED_PARTS for part in path.parts):
            forbidden.append(path.as_posix())
            continue
        if ".egg-info" in path.as_posix():
            forbidden.append(path.as_posix())
    assert forbidden == []


def test_repository_has_one_project_root_and_one_package_root() -> None:
    tracked = {path.as_posix() for path in _tracked_files()}
    pyprojects = sorted(path for path in tracked if path.endswith("pyproject.toml"))
    assert pyprojects == ["pyproject.toml"]
    assert "source/src/wls/__init__.py" in tracked
    assert "src/wls/__init__.py" not in tracked
    assert "source/pyproject.toml" not in tracked


def test_verification_code_never_builds_source_as_a_second_project() -> None:
    offenders: list[str] = []
    roots = (
        REPOSITORY / "source" / "scripts",
        REPOSITORY / "source" / "tests",
        REPOSITORY / "scripts",
    )
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() not in {".py", ".ps1", ".sh", ".bat"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if BUILD_SOURCE_PATTERN.search(text) or "python -m build source" in text.lower():
                offenders.append(path.relative_to(REPOSITORY).as_posix())
    assert offenders == []


def test_version_authority_and_dynamic_package_metadata_agree() -> None:
    version_text = (
        REPOSITORY / "source" / "src" / "wls" / "_version.py"
    ).read_text(encoding="utf-8")
    match = VERSION_PATTERN.search(version_text)
    assert match is not None
    canonical_version = match.group(1)
    project = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")
    assert 'dynamic = ["version"]' in project
    assert 'version = {attr = "wls._version.__version__"}' in project
    assert f'__version__ = "{canonical_version}"' in version_text
