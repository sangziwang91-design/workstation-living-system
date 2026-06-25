from __future__ import annotations

from pathlib import Path

import pytest

import wls.approval as approval_module
import wls.evidence as evidence_module
from wls.approval import ApprovalManager
from wls.db import Database
from wls.evidence import EvidenceLedger


NEWLINE_KEY = b"\n" * 32


def test_evidence_key_round_trips_binary_bytes_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(evidence_module.os, "urandom", lambda size: NEWLINE_KEY)
    database = Database(tmp_path / "state" / "wls.db")
    key_path = tmp_path / "secrets" / "evidence.key"

    ledger = EvidenceLedger(database, key_path)
    ledger.append("binary_key_probe", {"platform": "windows"})
    restarted = EvidenceLedger(database, key_path)

    assert key_path.read_bytes() == NEWLINE_KEY
    assert restarted.verify()[0] is True


def test_approval_key_round_trips_binary_bytes_across_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(approval_module.os, "urandom", lambda size: NEWLINE_KEY)
    database = Database(tmp_path / "state" / "wls.db")
    evidence_key = tmp_path / "secrets" / "evidence.key"
    approval_key = tmp_path / "secrets" / "approval.key"
    ledger = EvidenceLedger(database, evidence_key)

    ApprovalManager(database, ledger, approval_key)
    restarted = ApprovalManager(database, ledger, approval_key)

    assert approval_key.read_bytes() == NEWLINE_KEY
    assert restarted._secret == NEWLINE_KEY


@pytest.mark.parametrize("manager", ["evidence", "approval"])
def test_rejects_legacy_text_mode_key_length(
    tmp_path: Path, manager: str
) -> None:
    database = Database(tmp_path / "state" / "wls.db")
    key_path = tmp_path / "secrets" / f"{manager}.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_bytes(b"\r\n" + b"x" * 31)

    if manager == "evidence":
        with pytest.raises(ValueError, match="exactly 32 bytes"):
            EvidenceLedger(database, key_path)
    else:
        ledger = EvidenceLedger(database, tmp_path / "secrets" / "valid.key")
        with pytest.raises(ValueError, match="exactly 32 bytes"):
            ApprovalManager(database, ledger, key_path)
