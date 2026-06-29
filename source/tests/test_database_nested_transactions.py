from __future__ import annotations

from pathlib import Path

import pytest

from wls.db import Database


def test_nested_write_and_read_share_outer_transaction(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    with db.transaction() as outer:
        outer.execute(
            "INSERT INTO runtime_state(key,value_json,updated_at) VALUES (?,?,?)",
            ("outer", '"before"', "now"),
        )
        db.set_runtime("nested", "visible")
        assert db.get_runtime("nested") == "visible"
    assert db.get_runtime("outer") == "before"
    assert db.get_runtime("nested") == "visible"


def test_inner_failure_rolls_back_only_savepoint(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    with db.transaction():
        db.set_runtime("outer_before", 1)
        with pytest.raises(RuntimeError, match="inner"):
            with db.transaction():
                db.set_runtime("inner", 2)
                raise RuntimeError("inner")
        db.set_runtime("outer_after", 3)
        assert db.get_runtime("inner") is None
    assert db.get_runtime("outer_before") == 1
    assert db.get_runtime("outer_after") == 3
    assert db.get_runtime("inner") is None


def test_outer_failure_rolls_back_nested_success(tmp_path: Path) -> None:
    db = Database(tmp_path / "state.db")
    with pytest.raises(RuntimeError, match="outer"):
        with db.transaction():
            db.set_runtime("outer", 1)
            db.set_runtime("nested", 2)
            raise RuntimeError("outer")
    assert db.get_runtime("outer") is None
    assert db.get_runtime("nested") is None
