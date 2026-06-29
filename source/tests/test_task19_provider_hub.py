from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
import json

import pytest

from wls.provider_hub import ProviderHub, _normalized_ip, _safe_address, _validate_url
from wls.provider_secrets import ProviderSecretStore, SecretStoreError
import wls.provider_secrets as provider_secrets


class MemorySecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, provider_id: str, secret: str) -> None:
        self.values[provider_id] = secret

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)

    def has(self, provider_id: str) -> bool:
        return provider_id in self.values

    def delete(self, provider_id: str) -> bool:
        return self.values.pop(provider_id, None) is not None

    def status(self) -> dict:
        return {"backend": "memory-test", "available": True}


class FakeResponse:
    status = 200

    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, amount: int = -1) -> bytes:
        return self.payload if amount < 0 else self.payload[:amount]


class FakeOpener:
    def __init__(self, payload: bytes = b'{"data":[{"id":"model-a"}]}') -> None:
        self.payload = payload

    def open(self, request, timeout: float):
        assert request.full_url.startswith("http://127.0.0.1")
        assert timeout >= 1.0
        return FakeResponse(self.payload)


class BlockingOpener(FakeOpener):
    def __init__(self, entered: Event, release: Event) -> None:
        super().__init__()
        self.entered = entered
        self.release = release

    def open(self, request, timeout: float):
        self.entered.set()
        assert self.release.wait(timeout=2.0)
        return super().open(request, timeout)


def _hub(tmp_path: Path) -> tuple[ProviderHub, MemorySecrets]:
    secrets = MemorySecrets()
    hub = ProviderHub(tmp_path / "home", secret_store=secrets)
    hub.configure(
        "custom_openai",
        credential="WLS_TASK19_CANARY_SECRET_7F3A",
        base_url="http://127.0.0.1:8765/v1",
        model="local-model",
    )
    return hub, secrets


def test_ssrf_validation_blocks_private_userinfo_and_mapped_private() -> None:
    with pytest.raises(ValueError, match="unsafe"):
        _validate_url("https://10.0.0.1/v1")
    with pytest.raises(ValueError, match="userinfo"):
        _validate_url("https://user:password@example.com/v1")
    with pytest.raises(ValueError, match="require HTTPS"):
        _validate_url("http://8.8.8.8/v1")
    assert _safe_address(_normalized_ip("::ffff:10.0.0.1")) is False
    validated, addresses = _validate_url("http://127.0.0.1:8765/v1")
    assert validated.startswith("http://127.0.0.1")
    assert addresses == ("127.0.0.1",)


def test_secret_never_enters_provider_state_or_public_fingerprint(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    public = hub.list()
    state_text = hub.state_path.read_text(encoding="utf-8")
    serialized = json.dumps(public, sort_keys=True)
    secret = "WLS_TASK19_CANARY_SECRET_7F3A"
    assert secret not in state_text
    assert secret not in serialized
    import hashlib

    assert hashlib.sha256(secret.encode()).hexdigest() not in state_text
    row = hub.get_public("custom_openai")
    assert row["credential_configured"] is True
    assert len(row["configuration_digest"]) == 64


def test_probe_is_bounded_redirect_free_and_updates_atomically(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    result = hub.probe("custom_openai", opener=FakeOpener(), timeout_seconds=2)
    assert result["status"] == "PASS"
    assert result["models"] == ["model-a"]
    state = json.loads(hub.state_path.read_text(encoding="utf-8"))
    metadata = state["providers"]["custom_openai"]
    assert metadata["last_probe_status"] == "PASS"
    assert len(metadata["probe_fingerprint"]) == 64


def test_slow_probe_does_not_hold_global_provider_lock(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    entered = Event()
    release = Event()
    opener = BlockingOpener(entered, release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        probe_future = pool.submit(hub.probe, "custom_openai", opener=opener)
        assert entered.wait(timeout=1.0)
        list_future = pool.submit(hub.list)
        listed = list_future.result(timeout=0.5)
        assert listed["providers"]
        release.set()
        assert probe_future.result(timeout=2.0)["status"] == "PASS"


def test_configuration_change_during_probe_prevents_stale_overwrite(tmp_path: Path) -> None:
    hub, _ = _hub(tmp_path)
    entered = Event()
    release = Event()
    opener = BlockingOpener(entered, release)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(hub.probe, "custom_openai", opener=opener)
        assert entered.wait(timeout=1.0)
        hub.configure(
            "custom_openai",
            base_url="http://127.0.0.1:8765/v1",
            model="changed-model",
        )
        release.set()
        result = future.result(timeout=2.0)
    assert result["status"] == "STALE_CONFIG"
    assert hub.get_public("custom_openai")["model"] == "changed-model"


def test_insecure_keyring_backend_fails_closed(monkeypatch) -> None:
    class Backend:
        priority = 0

    class Keyring:
        @staticmethod
        def get_keyring():
            return Backend()

    monkeypatch.setattr(provider_secrets, "_keyring", Keyring())
    store = ProviderSecretStore()
    assert store.status()["available"] is False
    with pytest.raises(SecretStoreError, match="no usable secure"):
        store.set("deepseek", "secret")
