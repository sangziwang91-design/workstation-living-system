import pytest
import tempfile
from pathlib import Path
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import ActionSpec, RiskLevel

def test_path_traversal():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        runtime = LivingSystem(config)

        # Ensure no read roots are allowed
        runtime.config.tool_policy["allowed_read_roots"] = []

        action = ActionSpec(
            tool="read_file",
            arguments={"path": "../../etc/passwd"},
            purpose="attack",
            expected_result="leak",
            risk=RiskLevel.HIGH
        )
        with pytest.raises((ValueError, PermissionError)):
             runtime.policy.validate_arguments(action)

def test_command_injection():
    with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        runtime = LivingSystem(config)

        runtime.config.tool_policy["allowed_commands"] = []

        action = ActionSpec(
            tool="run_command",
            arguments={"command": ["ls", ";", "rm", "-rf", "/"]},
            purpose="attack",
            expected_result="leak",
            risk=RiskLevel.HIGH
        )
        with pytest.raises((ValueError, PermissionError)):
             runtime.policy.validate_arguments(action)

def test_secret_in_path():
     with tempfile.TemporaryDirectory() as tmpdir:
        home = Path(tmpdir)
        config = default_config(home)
        config.secret_path.parent.mkdir(parents=True, exist_ok=True)
        config.secret_path.write_bytes(b"a" * 32)
        runtime = LivingSystem(config)

        # Explicitly set allowed_read_roots to something else
        runtime.config.tool_policy["allowed_read_roots"] = [str(home / "data")]
        (home / "data").mkdir()

        action = ActionSpec(
            tool="read_file",
            arguments={"path": str(config.secret_path)},
            purpose="attack",
            expected_result="leak",
            risk=RiskLevel.HIGH
        )
        # Should raise PermissionError because it's not in home/data
        with pytest.raises((ValueError, PermissionError)):
            runtime.policy.validate_arguments(action)
