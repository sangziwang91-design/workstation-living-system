from __future__ import annotations

from wls.sensors.system import SystemSensor


def test_system_sensor_suppresses_stable_resource_noise(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("wls.sensors.system.shutil.disk_usage", lambda root: (1000, 500, 500))
    monkeypatch.setattr("wls.sensors.system.memory_ratio", lambda: 0.5)
    monkeypatch.setattr("wls.sensors.system.platform.platform", lambda: "test-platform")
    monkeypatch.setattr("wls.sensors.system.os.getloadavg", lambda: (0.0, 0.0, 0.0), raising=False)

    sensor = SystemSensor(name="system_resources", settings={}, home=tmp_path)
    first_observations, state = sensor.poll({})
    stable_observations, stable_state = sensor.poll(state)

    assert {item.predicate for item in first_observations} == {
        "disk_used_ratio",
        "disk_free_bytes",
        "load_1m",
        "memory_used_ratio",
        "platform",
    }
    assert stable_observations == []
    assert stable_state == state


def test_system_sensor_emits_significant_resource_change(tmp_path, monkeypatch) -> None:
    disk_values = [(1000, 500, 500), (1000, 530, 470)]

    def fake_disk_usage(root):
        return disk_values.pop(0)

    monkeypatch.setattr("wls.sensors.system.shutil.disk_usage", fake_disk_usage)
    monkeypatch.setattr("wls.sensors.system.memory_ratio", lambda: 0.5)
    monkeypatch.setattr("wls.sensors.system.platform.platform", lambda: "test-platform")
    monkeypatch.setattr("wls.sensors.system.os.getloadavg", lambda: (0.0, 0.0, 0.0), raising=False)

    sensor = SystemSensor(
        name="system_resources",
        settings={"ratio_change_epsilon": 0.02},
        home=tmp_path,
    )
    _, state = sensor.poll({})
    observations, _ = sensor.poll(state)

    assert {item.predicate for item in observations} == {"disk_used_ratio"}
