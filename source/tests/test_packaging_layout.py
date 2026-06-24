from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def test_packaging_layout_contract() -> None:
    source_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "scripts/verify_packaging_layout.py"],
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
