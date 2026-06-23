from __future__ import annotations

from datetime import datetime, UTC
from typing import Any

from .base import Sensor
from ..schemas import Observation


class ClockSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        now = datetime.now(UTC)
        hour_key = now.strftime("%Y-%m-%dT%H")
        day_key = now.strftime("%Y-%m-%d")
        observations: list[Observation] = []
        if previous_state.get("hour_key") != hour_key:
            observations.append(
                Observation(
                    source=self.name,
                    kind="time",
                    subject="runtime",
                    predicate="utc_hour",
                    value=hour_key,
                    metadata={
                        "weekday": now.weekday(),
                        "hour": now.hour,
                        "dedupe_key": f"clock:hour:{hour_key}",
                    },
                )
            )
        if previous_state.get("day_key") != day_key:
            observations.append(
                Observation(
                    source=self.name,
                    kind="time",
                    subject="runtime",
                    predicate="utc_day",
                    value=day_key,
                    metadata={"dedupe_key": f"clock:day:{day_key}"},
                )
            )
        return observations, {"hour_key": hour_key, "day_key": day_key}
