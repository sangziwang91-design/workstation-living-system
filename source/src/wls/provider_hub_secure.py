from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
import ipaddress

from .provider_hub import ProviderHub, _public_config_digest


def _loopback_base_url(value: str) -> str:
    parsed = urlparse(str(value).strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("custom provider URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("userinfo credentials in provider URL are forbidden")
    if parsed.query or parsed.fragment:
        raise ValueError("custom provider URL query and fragment are forbidden")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("custom provider URL requires a hostname")
    if hostname.lower() == "localhost":
        return parsed.geturl().rstrip("/")
    try:
        ip = ipaddress.ip_address(hostname.split("%", 1)[0])
    except ValueError as exc:
        raise ValueError(
            "custom provider hostname must be localhost or a literal loopback address"
        ) from exc
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        ip = mapped
    if not ip.is_loopback:
        raise ValueError("custom provider endpoint must be loopback-only")
    return parsed.geturl().rstrip("/")


class SecureProviderHub(ProviderHub):
    """Candidate-only Provider Hub with probe-bound selection."""

    def configure(
        self,
        provider_id: str,
        *,
        credential: str | None = None,
        enabled: bool = True,
        model: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        normalized = str(provider_id).strip().lower()
        safe_base = base_url
        if normalized == "custom_openai" and base_url is not None:
            safe_base = _loopback_base_url(base_url)
        super().configure(
            normalized,
            credential=credential,
            enabled=enabled,
            model=model,
            base_url=safe_base,
        )
        with self._lock:
            metadata = self._metadata_locked(normalized)
            metadata.pop("last_probe_configuration_digest", None)
            if metadata.get("last_probe_status") == "PASS":
                metadata["last_probe_status"] = "CONFIG_CHANGED"
            self._save_locked()
        return self.get_public(normalized)

    def probe(
        self,
        provider_id: str,
        *,
        timeout_seconds: float = 15.0,
        opener: Any | None = None,
    ) -> dict[str, Any]:
        normalized = str(provider_id).strip().lower()
        result = super().probe(
            normalized,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
        if result.get("status") == "PASS":
            preset = self._preset(normalized)
            with self._lock:
                metadata = self._metadata_locked(normalized)
                digest = _public_config_digest(preset, metadata)
                metadata["last_probe_configuration_digest"] = digest
                self._save_locked()
            result["configuration_digest"] = digest
        return result

    def select(self, provider_id: str) -> dict[str, Any]:
        normalized = str(provider_id).strip().lower()
        preset = self._preset(normalized)
        with self._lock:
            metadata = dict(self._state["providers"].get(normalized, {}))
        current_digest = _public_config_digest(preset, metadata)
        if metadata.get("last_probe_status") != "PASS":
            raise ValueError(
                "provider requires a current successful probe before selection"
            )
        if metadata.get("last_probe_configuration_digest") != current_digest:
            raise ValueError("provider probe does not match the current configuration")
        return super().select(normalized)
