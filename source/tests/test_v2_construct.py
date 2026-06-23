from wls.config import default_config
from wls.v2_runtime import LivingSystemV2


def test_v2_constructs(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []
    runtime = LivingSystemV2(config)
    assert runtime.status()["version"] == "0.7.0.dev1"
