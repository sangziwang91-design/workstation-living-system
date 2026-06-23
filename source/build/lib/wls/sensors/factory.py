from __future__ import annotations

from pathlib import Path
import importlib
from typing import Any

from .base import Sensor
from .clock import ClockSensor
from .filesystem import FileSystemSensor
from .git import GitSensor
from .http import HttpHealthSensor
from .inbox import InboxSensor
from .process import ProcessSensor
from .system import SystemSensor


SENSOR_TYPES: dict[str, type[Sensor]] = {
    "clock": ClockSensor,
    "filesystem": FileSystemSensor,
    "git": GitSensor,
    "http": HttpHealthSensor,
    "inbox": InboxSensor,
    "process": ProcessSensor,
    "system": SystemSensor,
}


def build_sensor(
    sensor_type: str, name: str, settings: dict[str, Any], home: str | Path
) -> Sensor:
    sensor_class = SENSOR_TYPES.get(sensor_type)
    if sensor_class is None and ":" in sensor_type:
        module_name, class_name = sensor_type.split(":", 1)
        module = importlib.import_module(module_name)
        candidate = getattr(module, class_name)
        if not isinstance(candidate, type) or not issubclass(candidate, Sensor):
            raise TypeError(f"custom sensor must subclass Sensor: {sensor_type}")
        sensor_class = candidate
    if sensor_class is None:
        raise ValueError(f"unknown sensor type: {sensor_type}")
    return sensor_class(name=name, settings=settings, home=home)
