from wls.config import default_config
from wls.db import Database
from wls.v2_runtime import ensure_experiment_tables


def test_extension_schema(tmp_path) -> None:
    config = default_config(tmp_path / "home")
    config.ensure_directories()
    database = Database(config.db_path)
    ensure_experiment_tables(database)
    names = {
        row["name"]
        for row in database.query_all(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "skill_experiments" in names
    assert "recovery_experiments" in names
