import pytest
import tempfile
import json
import socket
import threading
from pathlib import Path
from wls.provider_hub import ProviderHub, _validate_url, _sanitize_config

class MockSecretStore:
    def __init__(self):
        self.secrets = {}
    def set(self, pid, val): self.secrets[pid] = val
    def get(self, pid): return self.secrets.get(pid)
    def has(self, pid): return pid in self.secrets
    def delete(self, pid):
        if pid in self.secrets:
            del self.secrets[pid]
            return True
        return False
    def status(self): return {"backend": "mock"}

def test_ssrf_validation():
    # Public HTTPS is OK
    assert _validate_url("https://api.openai.com/v1") == "https://api.openai.com/v1"

    # Loopback HTTP is OK
    assert _validate_url("http://127.0.0.1:8000") == "http://127.0.0.1:8000"
    assert _validate_url("http://localhost:11434") == "http://localhost:11434"

    # Private ranges blocked
    with pytest.raises(ValueError, match="unsafe address blocked|could not resolve"):
        _validate_url("http://192.168.1.1")
    with pytest.raises(ValueError, match="unsafe address blocked|could not resolve"):
        _validate_url("https://10.0.0.1")
    with pytest.raises(ValueError, match="unsafe address blocked|could not resolve"):
        _validate_url("http://169.254.169.254") # AWS metadata

    # Non-HTTPS non-loopback blocked
    with pytest.raises(ValueError, match="require HTTPS"):
        _validate_url("http://api.openai.com/v1")

def test_secret_rejection():
    canary = "WLS_TASK19_CANARY_SECRET_7F3A"

    # Direct canary check
    with pytest.raises(ValueError, match="canary"):
        _sanitize_config(f"prefix {canary} suffix", canary)

    # Sensitive fields check
    with pytest.raises(ValueError, match="sensitive"):
        _sanitize_config({"api_key": "something"})

    # Nested check
    with pytest.raises(ValueError, match="canary"):
        _sanitize_config({"model": {"name": canary}}, canary)

def test_provider_hub_thread_safety():
    with tempfile.TemporaryDirectory() as tmpdir:
        hub = ProviderHub(tmpdir, secret_store=MockSecretStore())
        hub.configure("custom_openai", base_url="http://127.0.0.1:8000", model="test", credential="key")

        def worker():
            for i in range(100):
                hub.configure("custom_openai", model=f"model-{threading.get_ident()}-{i}")
                hub.list()

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert hub.get_public("custom_openai")["enabled"] is True
