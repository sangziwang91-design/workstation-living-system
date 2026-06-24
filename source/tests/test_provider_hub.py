from __future__ import annotations

from pathlib import Path
from threading import Thread
from time import sleep
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import json

import pytest

from wls.config import default_config
from wls.provider_hub import ProviderHub
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


class Response:
    status = 200

    def __init__(self, value: Any) -> None:
        self.payload = json.dumps(value).encode("utf-8")

    def __enter__(self) -> "Response":
        return self

    def __exit__(self, *args: Any) -> None:
        return None

    def read(self, limit: int) -> bytes:
        return self.payload[:limit]


class Opener:
    def __init__(self, value: Any) -> None:
        self.value = value
        self.request: Request | None = None

    def open(self, request: Request, timeout: float) -> Response:
        self.request = request
        return Response(self.value)


def test_provider_state_excludes_credential_value(tmp_path: Path) -> None:
    vault = MemoryVault()
    hub = ProviderHub(tmp_path, secret_store=vault)
    marker = "credential-test-marker"
    hub.configure("groq", credential=marker, model="openai/gpt-oss-20b")

    state_text = hub.state_path.read_text(encoding="utf-8")
    assert marker not in state_text
    public = hub.get_public("groq")
    assert public["credential_configured"] is True
    assert "credential" not in public
    assert public["runtime_attached"] is False
    assert public["hard_spend_cap_usd"] == 0


def test_probe_discovers_models_and_returns_no_credential(tmp_path: Path) -> None:
    vault = MemoryVault()
    marker = "probe-test-marker"
    vault.set("groq", marker)
    hub = ProviderHub(tmp_path, secret_store=vault)
    hub.configure("groq", enabled=True)
    opener = Opener({"data": [{"id": "model-a"}, {"id": "model-b"}]})

    result = hub.probe("groq", opener=opener)

    assert result["status"] == "PASS"
    assert result["models"] == ["model-a", "model-b"]
    assert result["credential_exposed"] is False
    assert marker not in json.dumps(result)
    assert marker not in hub.state_path.read_text(encoding="utf-8")


def test_custom_endpoint_requires_https_or_loopback(tmp_path: Path) -> None:
    hub = ProviderHub(tmp_path, secret_store=MemoryVault())
    with pytest.raises(ValueError, match="require HTTPS"):
        hub.configure("custom_openai", base_url="http://example.com/v1", model="m")
    row = hub.configure(
        "custom_openai",
        base_url="http://127.0.0.1:11434/v1",
        model="local-model",
    )
    assert row["configured_base_url"] == "http://127.0.0.1:11434/v1"


def test_selection_needs_enabled_provider_and_vault_entry(tmp_path: Path) -> None:
    vault = MemoryVault()
    hub = ProviderHub(tmp_path, secret_store=vault)
    with pytest.raises(ValueError, match="enabled"):
        hub.select("mistral")
    hub.configure("mistral", enabled=True)
    with pytest.raises(ValueError, match="credential"):
        hub.select("mistral")
    vault.set("mistral", "value")
    assert hub.select("mistral")["selected_provider_id"] == "mistral"


def test_browser_page_uses_terminal_vault_entry() -> None:
    assert "type='password'" not in PROVIDER_HUB_HTML
    assert 'type="password"' not in PROVIDER_HUB_HTML
    assert "python -m wls.provider_cli set-key" in PROVIDER_HUB_JS
    assert "canonical runtime" in PROVIDER_HUB_JS


def test_provider_api_requires_ephemeral_ui_session(tmp_path: Path) -> None:
    runtime = LivingSystem(default_config(tmp_path / "home"))
    server = WLSServer(runtime, "127.0.0.1", 0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    for _ in range(100):
        if server._server is not None and server.port:
            break
        sleep(0.01)
    base = f"http://127.0.0.1:{server.port}"
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
        assert len(payload["providers"]) >= 6
    finally:
        server.shutdown()
        thread.join(timeout=3)
