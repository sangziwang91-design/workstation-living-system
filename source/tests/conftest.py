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
