from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
import json
import os


BUILTIN_SURVIVAL_PLUGIN = "wls.survival_plugin"


@dataclass(slots=True)
class SensorConfig:
    sensor_type: str
    name: str
    enabled: bool = True
    interval_seconds: float = 30.0
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class RuntimeConfig:
    home: str
    identity_name: str = "WLS"
    cycle_seconds: float = 30.0
    workspace_capacity: int = 8
    max_events_per_cycle: int = 50
    max_actions_per_cycle: int = 4
    read_only: bool = True
    allow_autonomous_read_actions: bool = True
    allow_autonomous_reversible_writes: bool = False
    full_integrity_check_every: int = 50
    memory_retrieval_limit: int = 8
    memory_decay_days: int = 30
    sleep_after_idle_cycles: int = 5
    max_autonomous_goals: int = 3
    daemon_max_consecutive_failures: int = 3
    daemon_failure_backoff_seconds: float = 1.0
    daemon_failure_backoff_max_seconds: float = 30.0
    daemon_heartbeat_every_cycles: int = 1
    daemon_heartbeat_retention: int = 10000
    daemon_max_pending_events: int = 10000
    daemon_max_database_bytes: int = 2 * 1024 * 1024 * 1024
    daemon_max_cycle_seconds: float = 300.0
    sensors: list[SensorConfig] = field(default_factory=list)
    tool_policy: dict[str, Any] = field(default_factory=dict)
    provider: dict[str, Any] = field(default_factory=lambda: {"type": "deterministic"})
    plugin_modules: list[str] = field(
        default_factory=lambda: [BUILTIN_SURVIVAL_PLUGIN]
    )

    @property
    def home_path(self) -> Path:
        return Path(self.home).expanduser().resolve()

    @property
    def db_path(self) -> Path:
        return self.home_path / "state" / "wls.db"

    @property
    def secret_path(self) -> Path:
        return self.home_path / "secrets" / "evidence.key"

    @property
    def sandbox_path(self) -> Path:
        return self.home_path / "sandbox"

    @property
    def inbox_path(self) -> Path:
        return self.home_path / "inbox"

    @property
    def outbox_path(self) -> Path:
        return self.home_path / "outbox"

    def validate(self) -> None:
        if self.cycle_seconds <= 0:
            raise ValueError("cycle_seconds must be positive")
        if not 1 <= self.workspace_capacity <= 64:
            raise ValueError("workspace_capacity must be within 1..64")
        if not 1 <= self.max_events_per_cycle <= 1000:
            raise ValueError("max_events_per_cycle must be within 1..1000")
        if not 0 <= self.max_actions_per_cycle <= 32:
            raise ValueError("max_actions_per_cycle must be within 0..32")
        if not 1 <= self.full_integrity_check_every <= 100000:
            raise ValueError("full_integrity_check_every must be within 1..100000")
        if not 1 <= self.daemon_max_consecutive_failures <= 1000:
            raise ValueError("daemon_max_consecutive_failures must be within 1..1000")
        if self.daemon_failure_backoff_seconds < 0:
            raise ValueError("daemon_failure_backoff_seconds must be non-negative")
        if self.daemon_failure_backoff_max_seconds < self.daemon_failure_backoff_seconds:
            raise ValueError("daemon failure backoff maximum is below its base")
        if not 1 <= self.daemon_heartbeat_every_cycles <= 100000:
            raise ValueError("daemon_heartbeat_every_cycles must be within 1..100000")
        if not 1 <= self.daemon_heartbeat_retention <= 1000000:
            raise ValueError("daemon_heartbeat_retention must be within 1..1000000")
        if not 1 <= self.daemon_max_pending_events <= 10000000:
            raise ValueError("daemon_max_pending_events must be within 1..10000000")
        if self.daemon_max_database_bytes < 1024 * 1024:
            raise ValueError("daemon_max_database_bytes must be at least 1 MiB")
        if self.daemon_max_cycle_seconds <= 0:
            raise ValueError("daemon_max_cycle_seconds must be positive")
        if BUILTIN_SURVIVAL_PLUGIN not in self.plugin_modules:
            raise ValueError("built-in survival supervisor cannot be disabled")
        if len(set(self.plugin_modules)) != len(self.plugin_modules):
            raise ValueError("duplicate plugin module")
        names: set[str] = set()
        for sensor in self.sensors:
            if sensor.name in names:
                raise ValueError(f"duplicate sensor name: {sensor.name}")
            names.add(sensor.name)
            if sensor.interval_seconds <= 0:
                raise ValueError(f"sensor interval must be positive: {sensor.name}")

    def ensure_directories(self) -> None:
        for path in (
            self.home_path,
            self.db_path.parent,
            self.secret_path.parent,
            self.sandbox_path,
            self.inbox_path,
            self.outbox_path,
            self.home_path / "logs",
            self.home_path / "snapshots",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sensors"] = [asdict(sensor) for sensor in self.sensors]
        return data


DEFAULT_SENSORS = [
    SensorConfig(
        sensor_type="clock",
        name="clock",
        interval_seconds=60.0,
        settings={"emit_hourly": True},
    ),
    SensorConfig(
        sensor_type="system",
        name="system_resources",
        interval_seconds=60.0,
        settings={"disk_warning_ratio": 0.9, "memory_warning_ratio": 0.9},
    ),
    SensorConfig(
        sensor_type="inbox",
        name="local_inbox",
        interval_seconds=5.0,
        settings={},
    ),
]


def default_config(home: str | Path | None = None) -> RuntimeConfig:
    if home is None:
        env_home = os.environ.get("WLS_HOME")
        if env_home:
            home = env_home
        elif os.name == "nt":
            home = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "WLS"
        else:
            home = Path.home() / ".wls"
    home_path = Path(home).expanduser().resolve()
    return RuntimeConfig(
        home=str(home_path),
        sensors=list(DEFAULT_SENSORS),
        tool_policy={
            "allowed_read_roots": [str(home_path), str(home_path.parent)],
            "allowed_write_roots": [
                str(home_path / "sandbox"),
                str(home_path / "outbox"),
            ],
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "allowed_commands": ["git", "python", "python.exe", "pytest", "pytest.exe"],
        },
    )


def save_config(config: RuntimeConfig, path: str | Path) -> Path:
    config.validate()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(
        json.dumps(config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    os.replace(temporary, target)
    return target


def load_config(path: str | Path) -> RuntimeConfig:
    source = Path(path)
    raw = json.loads(source.read_text(encoding="utf-8"))
    plugins = [str(item) for item in raw.get("plugin_modules", [])]
    if BUILTIN_SURVIVAL_PLUGIN not in plugins:
        plugins.insert(0, BUILTIN_SURVIVAL_PLUGIN)
    raw["plugin_modules"] = plugins
    sensors = [SensorConfig(**item) for item in raw.pop("sensors", [])]
    config = RuntimeConfig(sensors=sensors, **raw)
    config.validate()
    return config


def load_or_create_config(
    path: str | Path, home: str | Path | None = None
) -> RuntimeConfig:
    source = Path(path)
    if source.exists():
        config = load_config(source)
    else:
        config = default_config(home)
        save_config(config, source)
    config.ensure_directories()
    return config
