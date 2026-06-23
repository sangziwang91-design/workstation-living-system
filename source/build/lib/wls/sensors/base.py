from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..schemas import Observation


class Sensor(ABC):
    def __init__(self, name: str, settings: dict[str, Any], home=None):
        self.name = name
        self.settings = settings
        self.home = home

    def ack(self, observation: Observation) -> None:
        """Acknowledge a durably ingested observation. Sensors may override."""
        return None

    @abstractmethod
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        """Return observations and the next durable sensor state."""
