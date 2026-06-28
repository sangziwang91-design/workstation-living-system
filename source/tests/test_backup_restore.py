import pytest
import tempfile
import shutil
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem

def test_backup_and_restore():
    with tempfile.TemporaryDirectory() as base_dir:
        base_path = Path(base_dir)
        source_home = base_path / "source_home"
        restore_home = base_path / "restore_home"
        source_home.mkdir()

        config = default_config(source_home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)

        runtime = LivingSystem(config)
        runtime.ledger.append("test_event", {"data": "save_me"})
        runtime.db.checkpoint()

        # Backup
        shutil.copytree(source_home, restore_home)

        # Restore and verify
        config2 = default_config(restore_home)
        runtime2 = LivingSystem(config2)
        ok, details = runtime2.ledger.verify()
        assert ok is True
        assert details["records"] >= 1
