from __future__ import annotations

from pathlib import Path

import pytest

from wls.db import Database
from wls.examiner_plugin import register_wls


class Ledger:
    def __init__(self):
        self.events = []

    def append(self, event_type, payload, connection=None):
        self.events.append((event_type, payload))
        return f"evidence-{len(self.events)}"


class Runtime:
    def __init__(self, tmp_path: Path):
        self.db = Database(tmp_path / "plugin.db")
        self.ledger = Ledger()


def test_plugin_is_additive_and_opt_in(tmp_path: Path):
    runtime = Runtime(tmp_path)
    try:
        register_wls(runtime)
        assert hasattr(runtime, "examiner")
        examiner = getattr(runtime, "examiner")
        assert examiner.active_version.version_id == "wls-examiner-v1-shadow"
        assert any(event == "examiner_registered" for event, _ in runtime.ledger.events)
        with pytest.raises(RuntimeError, match="already registered"):
            register_wls(runtime)
    finally:
        runtime.db.close()
