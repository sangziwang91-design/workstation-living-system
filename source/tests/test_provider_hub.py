from __future__ import annotations

from pathlib import Path
from threading import Thread
from time import sleep
from types import SimpleNamespace
from typing import Any, cast
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import pytest

from wls.config import default_config
from wls.provider_hub import ProviderHub
from wls.provider_secrets import provider_env_name
from wls.provider_ui import PROVIDER_HUB_HTML, PROVIDER_HUB_JS
from wls.runtime import LivingSystem
from wls.server import WLSServer


class MemoryVault:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, provider_id: str, value: str) -> None:
        self.values[provider_id] = value

    def get(self, provider_id: str) -> str | None:
        return self.values.get(provider_id)

    def has(self, provider_id: str) -> bool:
        return provider_id in self.values

    def delete(self, provider_id: str) -> bool:
        return self.values.pop(provider_id, None) is not None

    def status(self) -> dict[str, Any]:
        return {"backend": "memory", "available": True, "plaintext_in_wls_files": False}


class FakeResponse:
    status = 200

    def __init__(self, value: Any) -> None:
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class FakeOpener:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.request: Request | None = None
        self.timeout: float | None = None

    def open(self, request: Request, timeout: float) -> FakeResponse:
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.value)


def test_provider_metadata_never_persists_credential(tmp_path: Path) -> None:
    vault = MemoryVault()
    vault.set("groq", "credential-test-marker")
    hub = ProviderHub(tmp_path, secret_store=vault)
    hub.configure("groq", enabled=True, model="model-a")

    state_text = hub.state_path.read_text(encoding="utf-8")
    assert "credential-test-marker" not in state_text
    public = hub.get_public("groq")
    assert public["credential_configured"] is True
    assert public["runtime_attached"] is False
    assert public["hard_spend_cap_usd"] == 0
    assert public["auto_paid_fallback"] is False
    assert "credential" not in public


def test_probe_discovers_models_without_returning_credential(tmp_path: Path) -> None:
    vault = MemoryVault()
    vault.set("groq", "private-test-value")
    hub = ProviderHub(tmp_path, secret_store=vault)
    hub.configure("groq", enabled=True)
    opener = FakeOpener({"data": [{"id": "model-a"}, {"id": "model-b"}]})

    result = hub.probe("groq", opener=opener)

    assert result["status"] == "PASS"
    assert result["models"] == ["model-a", "model-b"]
    assert result["credential_exposed"] is False
    assert opener.request is not None
    assert opener.request.get_header("Authorization") == "Bearer private-test-value"
    assert "private-test-value" not in json.dumps(result)
    assert "private-test-value" not in hub.state_path.read_text(encoding="utf-8")


def test_selection_requires_enabled_credential_and_successful_probe(tmp_path: Path) -> None:
    vault = MemoryVault()
    hub = ProviderHub(tmp_path, secret_store=vault)
    with pytest.raises(ValueError, match="enabled"):
        hub.select("mistral")
    hub.configure("mistral", enabled=True)
    with pytest.raises(ValueError, match="credential"):
        hub.select("mistral")
    vault.set("mistral", "value")
    with pytest.raises(ValueError, match="probe"):
        hub.select("mistral")
    hub.probe("mistral", opener=FakeOpener({"data": [{"id": "mistral-test"}]}))
    assert hub.select("mistral")["selected_provider_id"] == "mistral"


def test_custom_endpoint_requires_https_or_loopback(tmp_path: Path) -> None:
    hub = ProviderHub(tmp_path, secret_store=MemoryVault())
    with pytest.raises(ValueError, match="require HTTPS"):
        hub.configure("custom_openai", base_url="http://example.com/v1")
    row = hub.configure(
        "custom_openai",
        base_url="http://127.0.0.1:11434/v1",
        model="local-model",
    )
    assert row["configured_base_url"] == "http://127.0.0.1:11434/v1"


def test_environment_variable_name_is_deterministic() -> None:
    assert provider_env_name("github_models") == "WLS_PROVIDER_GITHUB_MODELS_TOKEN"
    assert provider_env_name("custom-openai") == "WLS_PROVIDER_CUSTOM_OPENAI_TOKEN"


def test_browser_page_has_no_credential_input() -> None:
    assert "type='password'" not in PROVIDER_HUB_HTML
    assert 'type="password"' not in PROVIDER_HUB_HTML
    assert "wls-provider set-key" in PROVIDER_HUB_JS
    assert "canonical runtime" in PROVIDER_HUB_JS.lower()


def test_server_constructor_preserves_minimal_runtime_compatibility(tmp_path: Path) -> None:
    secret_path = tmp_path / "minimal" / "owner.secret"
    runtime = cast(Any, SimpleNamespace(config=SimpleNamespace(secret_path=secret_path)))

    server = WLSServer(runtime, "127.0.0.1", 0)

    assert server.providers.home_path == secret_path.parent.resolve()
    assert server.providers.ledger is None
    assert server.token


def _start_server(tmp_path: Path) -> tuple[WLSServer, Thread, str]:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    provider_hub = ProviderHub(runtime.config.home_path, secret_store=MemoryVault())
    server = WLSServer(runtime, "127.0.0.1", 0, provider_hub=provider_hub)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(200):
        if server._server is not None and server.port:
            break
        sleep(0.01)
    return server, thread, f"http://127.0.0.1:{server.port}"


def test_provider_api_requires_ephemeral_ui_session(tmp_path: Path) -> None:
    server, thread, base = _start_server(tmp_path)
    try:
        with urlopen(f"{base}/providers", timeout=3) as response:
            page = response.read().decode("utf-8")
        assert "WLS Provider Hub" in page
        assert server.ui_session in page

        with pytest.raises(HTTPError) as error:
            urlopen(f"{base}/api/providers", timeout=3)
        assert error.value.code == 401

        request = Request(
            f"{base}/api/providers",
            headers={"X-WLS-UI-Session": server.ui_session},
        )
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["runtime_attached"] is False
        assert len(payload["providers"]) >= 7
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_browser_api_rejects_credential_bodies(tmp_path: Path) -> None:
    server, thread, base = _start_server(tmp_path)
    body = json.dumps({"provider_id": "groq", "credential": "must-not-pass"}).encode()
    request = Request(
        f"{base}/api/providers/configure",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-WLS-UI-Session": server.ui_session,
        },
    )
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 400
        response_body = error.value.read().decode("utf-8")
        assert "secure local CLI" in response_body
        assert "must-not-pass" not in response_body
    finally:
        server.shutdown()
        thread.join(timeout=3)


def test_static_assets_reject_non_loopback_host_header(tmp_path: Path) -> None:
    server, thread, base = _start_server(tmp_path)
    request = Request(
        f"{base}/assets/provider-hub.js",
        headers={"Host": "attacker.example"},
    )
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(request, timeout=3)
        assert error.value.code == 421
    finally:
        server.shutdown()
        thread.join(timeout=3)
