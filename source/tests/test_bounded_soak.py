from scripts.run_bounded_soak import run_soak
import tempfile
from pathlib import Path

def test_soak_small_run():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        report = run_soak(max_cycles=5, home_path=home)
        assert report["completed_cycles"] >= 4
        assert report["failed_cycles"] == 0
        assert report["integrity_pass"] is True
