from __future__ import annotations

import gc
import time

import pytest


@pytest.fixture(autouse=True)
def _cleanup_databases():
    """Ensure all Database instances are properly closed after each test
    to prevent SQLite WAL file locks on Windows."""
    yield
    gc.collect()
    time.sleep(0.01)


@pytest.fixture(autouse=True)
def _isolate_default_wls_home(monkeypatch, tmp_path):
    """All tests must keep implicit WLS homes off the owner's real disk.

    Explicit per-test homes still work. Running this suite twice on the same
    machine must not inherit an earlier test's SQLite or HMAC identity.
    """
    monkeypatch.setenv("WLS_HOME", str(tmp_path / "default-wls-home"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local-app-data"))
    yield
