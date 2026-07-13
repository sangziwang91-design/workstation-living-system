from __future__ import annotations

from wls.db import Database


def test_predictions_status_key_index_exists(tmp_path) -> None:
    db = Database(tmp_path / "state" / "wls.db")

    indexes = {
        str(row["name"])
        for row in db.query_all("PRAGMA index_list('predictions')")
    }

    assert "idx_predictions_status_key" in indexes


def test_events_status_salience_time_index_exists(tmp_path) -> None:
    db = Database(tmp_path / "state" / "wls.db")

    indexes = {
        str(row["name"])
        for row in db.query_all("PRAGMA index_list('events')")
    }

    assert "idx_events_status_salience_time" in indexes
