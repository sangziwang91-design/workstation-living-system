"""Deterministic adversarial coverage of the read-only R2 public diagnostic."""
from __future__ import annotations

from pathlib import Path
import sys

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import diagnose_r2_paired_outcomes as diagnostic  # noqa: E402


def test_paired_diagnostic_rejects_moved_public_baseline() -> None:
    """All public summary and baseline proof limits are exercised in CI."""
    diagnostic.self_test()
