from __future__ import annotations

from pathlib import Path

import pytest

from wls.provider_hub_secure import SecureProviderHub, _loopback_base_url


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

    def status(self) -> dict[str, object]:
        return {"backend": "memory-test", "available": True}


class Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _amount: int = -1) -> bytes:
        return b'{"data":[{"id":"local-model"}]}'


class Opener:
    def open(self, request, timeout: float):
        assert request.full_url.startswith("http://127.0.0.1")
        assert timeout >= 1.0
        return Response()


def test_custom_provider_is_literal_loopback_only() -> None:
    assert _loopback_base_url("http://127.0.0.1:8080/v1")
    assert _loopback_base_url("http://localhost:8080/v1")
    with pytest.raises(ValueError, match="loopback"):
        _loopback_base_url("https://example.com/v1")
    with pytest.raises(ValueError, match="query"):
        _loopback_base_url("http://127.0.0.1:8080/v1?token=x")


def test_selection_requires_probe_for_current_configuration(tmp_path: Path) -> None:
    hub = SecureProviderHub(
        tmp_path / "home", secret_store=MemorySecrets()
    )
    hub.configure(
        "custom_openai",
        credential="secret",
        base_url="http://127.0.0.1:8080/v1",
        model="local-model",
    )
    with pytest.raises(ValueError, match="successful probe"):
        hub.select("custom_openai")
    assert hub.probe("custom_openai", opener=Opener())["status"] == "PASS"
    selected = hub.select("custom_openai")
    assert selected["selected_provider_id"] == "custom_openai"
    hub.configure(
        "custom_openai",
        base_url="http://127.0.0.1:8080/v1",
        model="changed-model",
    )
    with pytest.raises(ValueError, match="successful probe|current configuration"):
        hub.select("custom_openai")
