import pytest
import tempfile
from pathlib import Path
from wls.provider_hub import ProviderHub

class MockSecretStore:
    def __init__(self): self.secrets = {}
    def set(self, pid, val): self.secrets[pid] = val
    def get(self, pid): return self.secrets.get(pid)
    def has(self, pid): return pid in self.secrets
    def delete(self, pid):
        if pid in self.secrets: del self.secrets[pid]; return True
        return False
    def status(self): return {"backend": "mock"}

@pytest.fixture
def hub():
    with tempfile.TemporaryDirectory() as tmpdir:
        yield ProviderHub(tmpdir, secret_store=MockSecretStore())

def test_provider_hub_candidate_only(hub):
    status = hub.list()
    # runtime_attached was removed in my hardening as requested
    assert "providers" in status

def test_ssrf_protection_blocked_ips(hub):
    with pytest.raises(ValueError, match="unsafe address blocked"):
        hub.configure("custom_openai", base_url="http://192.168.1.1", model="test")

def test_atomic_updates(hub):
    hub.configure("custom_openai", model="m1", base_url="http://127.0.0.1:8000")
    p = hub.get_public("custom_openai")
    assert p["model"] == "m1"
