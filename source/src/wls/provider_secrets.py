from __future__ import annotations

from typing import Any
import os
import re

import keyring
from keyring.errors import KeyringError


_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SERVICE_NAME = "Workstation Living System Provider Hub"


class SecretStoreError(RuntimeError):
    """Raised when a provider credential cannot be handled safely."""


def _provider_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if _PROVIDER_ID_RE.fullmatch(normalized) is None:
        raise ValueError("invalid provider_id")
    return normalized


def provider_env_name(provider_id: str) -> str:
    normalized = _provider_id(provider_id).upper().replace("-", "_").replace(".", "_")
    return f"WLS_PROVIDER_{normalized}_TOKEN"


class ProviderSecretStore:
    """Keep provider credentials outside config files, SQLite, logs, and Git."""

    def __init__(self, service_name: str = _SERVICE_NAME):
        self.service_name = service_name

    def set(self, provider_id: str, secret: str) -> None:
        provider_id = _provider_id(provider_id)
        value = str(secret).strip()
        if not value:
            raise ValueError("credential must not be empty")
        if len(value) > 16384:
            raise ValueError("credential is unexpectedly large")
        try:
            keyring.set_password(self.service_name, provider_id, value)
        except KeyringError as exc:
            raise SecretStoreError(f"OS credential vault rejected the credential: {exc}") from exc

    def get(self, provider_id: str) -> str | None:
        provider_id = _provider_id(provider_id)
        environment_value = os.environ.get(provider_env_name(provider_id))
        if environment_value:
            return environment_value
        try:
            return keyring.get_password(self.service_name, provider_id)
        except KeyringError as exc:
            raise SecretStoreError(f"OS credential vault could not read the credential: {exc}") from exc

    def has(self, provider_id: str) -> bool:
        return self.get(provider_id) is not None

    def delete(self, provider_id: str) -> bool:
        provider_id = _provider_id(provider_id)
        try:
            existing = keyring.get_password(self.service_name, provider_id)
            if existing is None:
                return False
            keyring.delete_password(self.service_name, provider_id)
            return True
        except KeyringError as exc:
            raise SecretStoreError(f"OS credential vault could not delete the credential: {exc}") from exc

    def status(self) -> dict[str, Any]:
        backend = keyring.get_keyring()
        return {
            "backend": f"{type(backend).__module__}.{type(backend).__name__}",
            "service": self.service_name,
            "plaintext_in_wls_files": False,
            "environment_fallback": "WLS_PROVIDER_<PROVIDER_ID>_TOKEN",
            "recommended_platform": "Windows Credential Manager through keyring",
        }
