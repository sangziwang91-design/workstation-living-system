from __future__ import annotations

from typing import Any
import os
import re

try:
    import keyring as _keyring  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover
    _keyring = None


_PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
_SERVICE_NAME = "Workstation Living System Provider Hub"
_INSECURE_BACKEND_MARKERS = (
    "keyring.backends.fail",
    "keyring.backends.null",
    "plaintext",
)


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
    """Keep provider credentials outside WLS config, SQLite, reports, and Git."""

    def __init__(self, service_name: str = _SERVICE_NAME):
        self.service_name = service_name

    def _usable_backend(self, *, required: bool) -> Any | None:
        if _keyring is None:
            if required:
                raise SecretStoreError(
                    "OS credential vault support is unavailable; install the provider extra or use the environment-variable read fallback"
                )
            return None
        try:
            backend = _keyring.get_keyring()
            name = f"{type(backend).__module__}.{type(backend).__name__}"
            priority = float(getattr(backend, "priority", 0.0))
        except Exception as exc:
            if required:
                raise SecretStoreError(f"OS credential vault discovery failed: {exc}") from exc
            return None
        lowered = name.lower()
        insecure = priority <= 0 or any(marker in lowered for marker in _INSECURE_BACKEND_MARKERS)
        if insecure:
            if required:
                raise SecretStoreError(f"no usable secure credential backend is active: {name}")
            return None
        return backend

    def set(self, provider_id: str, secret: str) -> None:
        provider_id = _provider_id(provider_id)
        value = str(secret).strip()
        if not value:
            raise ValueError("credential must not be empty")
        if len(value) > 16_384:
            raise ValueError("credential is unexpectedly large")
        backend = self._usable_backend(required=True)
        assert backend is not None
        try:
            backend.set_password(self.service_name, provider_id, value)
        except Exception as exc:
            raise SecretStoreError(
                f"OS credential vault rejected the credential: {type(exc).__name__}"
            ) from exc

    def get(self, provider_id: str) -> str | None:
        provider_id = _provider_id(provider_id)
        environment_value = os.environ.get(provider_env_name(provider_id))
        if environment_value:
            return environment_value
        backend = self._usable_backend(required=False)
        if backend is None:
            return None
        try:
            return backend.get_password(self.service_name, provider_id)
        except Exception as exc:
            raise SecretStoreError(
                f"OS credential vault could not read the credential: {type(exc).__name__}"
            ) from exc

    def has(self, provider_id: str) -> bool:
        return self.get(provider_id) is not None

    def delete(self, provider_id: str) -> bool:
        provider_id = _provider_id(provider_id)
        backend = self._usable_backend(required=True)
        assert backend is not None
        try:
            existing = backend.get_password(self.service_name, provider_id)
            if existing is None:
                return False
            backend.delete_password(self.service_name, provider_id)
            return True
        except Exception as exc:
            raise SecretStoreError(
                f"OS credential vault could not delete the credential: {type(exc).__name__}"
            ) from exc

    def status(self) -> dict[str, Any]:
        backend = self._usable_backend(required=False)
        backend_name = (
            f"{type(backend).__module__}.{type(backend).__name__}"
            if backend is not None
            else "unavailable"
        )
        return {
            "backend": backend_name,
            "available": backend is not None,
            "service": self.service_name,
            "plaintext_in_wls_files": False,
            "environment_read_fallback": "WLS_PROVIDER_<PROVIDER_ID>_TOKEN",
            "environment_write_supported": False,
            "recommended_platform": "Windows Credential Manager through keyring",
        }
