import pytest
import tempfile
import json
import logging
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem

def test_secret_not_in_logs():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        canary = "CANARY_SECRET_12345_PADDING_FOR_32_BYTES"
        config.secret_path.write_text(canary)

        log_file = home / "test.log"
        handler = logging.FileHandler(log_file)
        logger = logging.getLogger("wls")
        logger.addHandler(handler)

        runtime = LivingSystem(config)
        runtime.ledger.append("test_op", {"secret": canary})

        logger.removeHandler(handler)
        handler.close()

        log_content = log_file.read_text()
        assert canary not in log_content, "Secret leaked in logs!"

def test_secret_not_in_verification_report():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        canary = "CANARY_SECRET_67890_PADDING_FOR_32_BYTES"
        config.secret_path.write_text(canary)

        runtime = LivingSystem(config)
        ok, details = runtime.ledger.verify()

        report_str = json.dumps(details)
        assert canary not in report_str, "Secret leaked in verification report!"
