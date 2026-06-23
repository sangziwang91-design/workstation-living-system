from __future__ import annotations

from ctypes import Structure, c_ulonglong, c_ulong, sizeof
import ctypes
from pathlib import Path
from typing import Any
import hashlib
import json
import os
import platform
import shutil

from .base import Sensor
from ..schemas import Observation


class MEMORYSTATUSEX(Structure):
    _fields_ = [
        ("dwLength", c_ulong),
        ("dwMemoryLoad", c_ulong),
        ("ullTotalPhys", c_ulonglong),
        ("ullAvailPhys", c_ulonglong),
        ("ullTotalPageFile", c_ulonglong),
        ("ullAvailPageFile", c_ulonglong),
        ("ullTotalVirtual", c_ulonglong),
        ("ullAvailVirtual", c_ulonglong),
        ("ullAvailExtendedVirtual", c_ulonglong),
    ]


def memory_ratio() -> float | None:
    psutil_value: float | None = None
    try:
        import psutil  # type: ignore

        psutil_value = float(psutil.virtual_memory().percent) / 100.0
    except Exception:
        psutil_value = None
    if psutil_value is not None:
        return psutil_value
    if os.name == "nt":
        try:
            status = MEMORYSTATUSEX()
            status.dwLength = sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(status):  # type: ignore[attr-defined]
                return float(status.dwMemoryLoad) / 100.0
        except Exception:
            return None
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        available = os.sysconf("SC_AVPHYS_PAGES")
        if pages > 0:
            return 1.0 - (available / pages)
    except (AttributeError, ValueError, OSError):
        return None
    return None


class SystemSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        root = Path(
            self.settings.get("disk_root") or ("C:/" if os.name == "nt" else "/")
        )
        total, used, free = shutil.disk_usage(root)
        disk_ratio = used / total if total else 0.0
        mem_ratio = memory_ratio()
        cpu_load: float | None = None
        try:
            cpu_load = os.getloadavg()[0]
        except (AttributeError, OSError):
            pass
        values: dict[str, Any] = {
            "disk_used_ratio": disk_ratio,
            "disk_free_bytes": free,
            "memory_used_ratio": mem_ratio,
            "load_1m": cpu_load,
            "platform": platform.platform(),
            "python_pid": os.getpid(),
        }
        observations = [
            Observation(
                source=self.name,
                kind="resource",
                subject="host",
                predicate=key,
                value=value,
                confidence=1.0 if value is not None else 0.0,
                metadata={
                    "warning": self._warning(key, value),
                    "dedupe_key": f"system:{key}:{hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()}",
                },
            )
            for key, value in values.items()
        ]
        return observations, values

    def _warning(self, key: str, value: Any) -> bool:
        if value is None:
            return False
        if key == "disk_used_ratio":
            return float(value) >= float(self.settings.get("disk_warning_ratio", 0.9))
        if key == "memory_used_ratio":
            return float(value) >= float(self.settings.get("memory_warning_ratio", 0.9))
        return False
