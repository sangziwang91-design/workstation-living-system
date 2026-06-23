from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


tracked = subprocess.check_output(
    ["git", "ls-files"], cwd=ROOT, text=True
).splitlines()

for name in tracked:
    normalized = name.replace("\\", "/")
    generated = (
        normalized == ".coverage"
        or normalized.startswith("source/build/")
        or "/__pycache__/" in normalized
        or ".egg-info/" in normalized
        or (
            normalized.startswith(".github/workflows/round001-")
            and normalized.endswith(".yml")
        )
        or normalized.startswith("tools/apply_round001_")
    )
    if generated:
        remove_path(ROOT / name)

# Keep this utility only for the cleanup commit itself; the workflow removes it
# after execution so Round 001 does not leave disposable migration machinery.
print("Round 001 generated-artifact cleanup complete.")
