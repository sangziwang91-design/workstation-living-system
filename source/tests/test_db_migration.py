from __future__ import annotations

from multiprocessing import Event, Process, Queue
from pathlib import Path
from types import SimpleNamespace
import sqlite3
import time

import pytest

from wls import db as db_module
from wls.config import default_config
from wls.db import Database
from wls.growth_cycle import GrowthCycleManager
from wls.runtime import LivingSystem


GROWTH_TABLES = {
    "recovery_experiments",
    "skill_experiments",
    "growth_cycles",
    "growth_measurements",
}


def table_names(path: Path) -> set[str]:
    connection = sqlite3.connect(path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        return {str(row[0]) for row in rows}
    finally:
        connection.close()


def make_schema_v1_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            CREATE TABLE evolution_candidates (
                candidate_id TEXT PRIMARY KEY,
                candidate_type TEXT NOT NULL,
                title TEXT NOT NULL,
                proposal_json TEXT NOT NULL,
                source_ids_json TEXT NOT NULL,
                baseline_json TEXT,
                experiment_json TEXT,
                result_json TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE skills (
                skill_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                version INTEGER NOT NULL,
                definition_json TEXT NOT NULL,
                status TEXT NOT NULL,
                success_rate REAL NOT NULL,
                use_count INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(name, version)
            );
            INSERT INTO schema_migrations(version, applied_at)
            VALUES (1, '2026-06-29T00:00:00+00:00');
            """
        )
    finally:
        connection.close()


def test_fresh_database_initialization_creates_growth_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "wls.db")
    try:
        assert db._schema_version() == db_module.SCHEMA_VERSION
        assert GROWTH_TABLES.issubset(table_names(db.path))
        ok, detail = db.integrity_check()
        assert ok is True, detail
    finally:
        db.close()


def test_schema_v1_database_migrates_to_current_version(tmp_path: Path) -> None:
    path = tmp_path / "state" / "wls.db"
    make_schema_v1_database(path)

    db = Database(path)
    try:
        assert db._schema_version() == db_module.SCHEMA_VERSION
        assert GROWTH_TABLES.issubset(table_names(path))
        ok, detail = db.integrity_check()
        assert ok is True, detail
    finally:
        db.close()


def test_migration_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "state" / "wls.db"
    first = Database(path)
    first.close()
    second = Database(path)
    try:
        rows = second.query_all(
            "SELECT version, COUNT(*) AS n FROM schema_migrations GROUP BY version"
        )
        assert {int(row["version"]): int(row["n"]) for row in rows} == {1: 1, 2: 1}
    finally:
        second.close()


def test_failed_migration_rolls_back_version_and_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "state" / "wls.db"
    make_schema_v1_database(path)
    monkeypatch.setattr(
        db_module,
        "GROWTH_SCHEMA_SQL",
        [*db_module.GROWTH_SCHEMA_SQL[:1], "CREATE TABLE broken_sql ("],
    )

    with pytest.raises(sqlite3.OperationalError):
        Database(path)

    connection = sqlite3.connect(path)
    try:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        assert version == 1
        assert "recovery_experiments" not in table_names(path)
    finally:
        connection.close()


def test_living_system_construction_is_bounded_and_has_growth_schema(
    tmp_path: Path,
) -> None:
    config = default_config(tmp_path / "home")
    config.sensors = []

    started = time.perf_counter()
    runtime = LivingSystem(config)
    elapsed = time.perf_counter() - started

    assert elapsed < 10.0
    assert GROWTH_TABLES.issubset(table_names(runtime.db.path))
    ok, detail = runtime.db.integrity_check()
    assert ok is True, detail


def test_growth_cycle_manager_constructor_does_not_mutate_schema(tmp_path: Path) -> None:
    db = Database(tmp_path / "wls.db")
    calls = 0
    original_transaction = db.transaction

    def counting_transaction(immediate: bool = True) -> object:
        nonlocal calls
        calls += 1
        return original_transaction(immediate)

    db.transaction = counting_transaction  # type: ignore[assignment,method-assign]
    runtime = SimpleNamespace(
        db=db,
        ledger=object(),
        skills=object(),
        learning=object(),
        config=object(),
    )
    GrowthCycleManager(runtime)  # type: ignore[arg-type]
    assert calls == 0
    db.close()


def test_database_restart_continuity(tmp_path: Path) -> None:
    path = tmp_path / "state" / "wls.db"
    first = Database(path)
    first.set_runtime("restart_probe", {"ok": True})
    first.close()

    second = Database(path)
    try:
        assert second.get_runtime("restart_probe") == {"ok": True}
        ok, detail = second.integrity_check()
        assert ok is True, detail
    finally:
        second.close()


def competing_database_worker(path_text: str, start, results) -> None:
    start.wait(10)
    started = time.perf_counter()
    try:
        db = Database(Path(path_text))
        db.close()
        results.put(("PASS", time.perf_counter() - started, None))
    except Exception as exc:
        results.put(("ERROR", time.perf_counter() - started, repr(exc)))


def test_same_home_competing_initialization_is_bounded(tmp_path: Path) -> None:
    path = tmp_path / "shared" / "state" / "wls.db"
    start = Event()
    results: Queue = Queue()
    processes = [
        Process(target=competing_database_worker, args=(str(path), start, results))
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start.set()
    for process in processes:
        process.join(20)
    alive = [process.pid for process in processes if process.is_alive()]
    for process in processes:
        if process.is_alive():
            process.terminate()
            process.join(5)

    assert alive == []
    rows = [results.get(timeout=1) for _ in processes]
    assert all(row[0] == "PASS" for row in rows)
    db = Database(path)
    try:
        ok, detail = db.integrity_check()
        assert ok is True, detail
    finally:
        db.close()
