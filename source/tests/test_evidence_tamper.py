import pytest
import tempfile
import sqlite3
import json
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem

@pytest.fixture
def isolated_runtime():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        runtime = LivingSystem(config)
        yield runtime

def test_evidence_tamper_payload(isolated_runtime):
    # Append some evidence
    isolated_runtime.ledger.append("test_event", {"data": "original"})
    ok, details = isolated_runtime.ledger.verify()
    assert ok is True

    # Manually tamper with the database
    db_path = isolated_runtime.config.db_path
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE evidence SET payload_json = ? WHERE seq = 1", (json.dumps({"data": "tampered"}),))
    conn.commit()
    conn.close()

    # Verify should fail
    ok, details = isolated_runtime.ledger.verify()
    assert ok is False
    assert details["reason"] in ("hash_mismatch", "chain_break")

def test_evidence_tamper_deletion(isolated_runtime):
    isolated_runtime.ledger.append("event_1", {"data": 1})
    isolated_runtime.ledger.append("event_2", {"data": 2})
    isolated_runtime.ledger.append("event_3", {"data": 3})

    # Delete a middle record
    conn = sqlite3.connect(isolated_runtime.config.db_path)
    conn.execute("DELETE FROM evidence WHERE seq = 2")
    conn.commit()
    conn.close()

    # Verify should fail due to chain break (seq gap)
    ok, details = isolated_runtime.ledger.verify()
    assert ok is False
    assert details["reason"] == "chain_break"

def test_evidence_tamper_link(isolated_runtime):
    isolated_runtime.ledger.append("event_1", {"data": 1})
    isolated_runtime.ledger.append("event_2", {"data": 2})

    # Change the previous_hash of the second record
    conn = sqlite3.connect(isolated_runtime.config.db_path)
    conn.execute("UPDATE evidence SET previous_hash = 'invalid' WHERE seq = 2")
    conn.commit()
    conn.close()

    ok, details = isolated_runtime.ledger.verify()
    assert ok is False
    assert details["reason"] in ("hash_mismatch", "chain_break")
