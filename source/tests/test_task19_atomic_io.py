from __future__ import annotations

from pathlib import Path
import os

import pytest

from wls.atomic_io import atomic_write_text


def test_atomic_write_replaces_complete_content(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")
    atomic_write_text(target, "new\n")
    assert target.read_text(encoding="utf-8") == "new\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_failed_replace_preserves_old_target_and_cleans_temporary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "state.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(_source, _target) -> None:
        raise OSError("negative control")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="negative control"):
        atomic_write_text(target, "new")
    assert target.read_text(encoding="utf-8") == "old"
    assert list(tmp_path.glob(".*.tmp")) == []
