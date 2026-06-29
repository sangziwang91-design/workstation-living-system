from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import builtins
import ipaddress
import json
import os
import socket
import threading
import time

from .evidence import EvidenceLedger
from .provider_secrets import ProviderSecretStore, provider_env_name
from .schemas import digest_json


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    provider_id: str
    name: str
    family: str
    protocol: str
    base_url: str
    models_url: str
    default_model: str
    registration_url: str
    docs_url: str
    access_note: str
    privacy_ceiling: str
    custom_base_url: bool = False


PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        "github_models",
        "GitHub Models",
        "GitHub",
        "openai_compatible",
        "https://models.github.ai/inference",
        "https://models.github.ai/catalog/models",
        "openai/gpt-4.1",
        "https://github.com/marketplace/models",
        "https://docs.github.com/en/github-models",
        "Account limits and model availability may change.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "gemini",
        "Gemini API",
        "Google",
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com/v1beta/models",
        "gemini-2.5-flash",
        "https://aistudio.google.com/app/apikey",
        "https://ai.google.dev/gemini-api/docs",
        "Review current account limits and data-use terms before use.",
        "P0_PUBLIC_OR_SYNTHETIC",
    ),
    ProviderPreset(
        "groq",
        "GroqCloud",
        "Groq",
        "openai_compatible",
        "https://api.groq.com/openai/v1",
        "https://api.groq.com/openai/v1/models",
        "openai/gpt-oss-20b",
        "https://console.groq.com/keys",
        "https://console.groq.com/docs",
        "Account rate limits apply.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "mistral",
        "Mistral API",
        "Mistral AI",
        "openai_compatible",
        "https://api.mistral.ai/v1",
        "https://api.mistral.ai/v1/models",
        "mistral-small-latest",
        "https://console.mistral.ai/api-keys",
        "https://docs.mistral.ai/api",
        "Evaluation or paid access depends on the current account tier.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "openrouter",
        "OpenRouter",
        "OpenRouter",
        "openai_compatible",
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/models",
        "openrouter/free",
        "https://openrouter.ai/settings/keys",
        "https://openrouter.ai/docs",
        "WLS never enables paid fallback automatically.",
        "P0_PUBLIC_OR_SYNTHETIC",
    ),
    ProviderPreset(
        "deepseek",
        "DeepSeek API",
        "DeepSeek",
        "openai_compatible",
        "https://api.deepseek.com",
        "https://api.deepseek.com/models",
        "deepseek-chat",
        "https://platform.deepseek.com/api_keys",
        "https://api-docs.deepseek.com/",
        "A positive account balance may be required.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "custom_openai",
        "Custom OpenAI-Compatible",
        "Custom",
        "openai_compatible",
        "https://example.invalid/v1",
        "https://example.invalid/v1/models",
        "",
        "",
        "",
        "Owner-approved HTTPS or loopback endpoint only.",
        "OWNER_DEFINED",
        True,
    ),
)

_PRESET_BY_ID = {item.provider_id: item for item in PRESETS}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _provider_id(value: str) -> str:
    normalized = str(value).strip().lower()
    if normalized not in _PRESET_BY_ID:
        raise KeyError(f"unknown provider: {normalized}")
    return normalized


def _normalized_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    ip = ipaddress.ip_address(value.split("%", 1)[0])
    mapped = getattr(ip, "ipv4_mapped", None)
    return mapped or ip


def _safe_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_loopback:
        return True
    return not (
        ip.is_private
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_link_local
    )


def _validate_url(url: str) -> tuple[str, tuple[str, ...]]:
    parsed = urlparse(str(url).strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("provider URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("userinfo credentials in provider URL are forbidden")
    if parsed.fragment:
        raise ValueError("provider URL fragments are forbidden")
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("provider URL requires a hostname")
    try:
        addresses = {
            str(_normalized_ip(str(item[4][0])))
            for item in socket.getaddrinfo(hostname, parsed.port, type=socket.SOCK_STREAM)
        }
    except (OSError, ValueError) as exc:
        raise ValueError(f"provider hostname could not be resolved: {hostname}") from exc
    if not addresses:
        raise ValueError("provider hostname resolved to no addresses")
    normalized = tuple(sorted(addresses))
    ips = tuple(_normalized_ip(item) for item in normalized)
    all_loopback = all(ip.is_loopback for ip in ips)
    for ip in ips:
        if not _safe_address(ip):
            raise ValueError(f"unsafe provider address blocked: {ip}")
    if not all_loopback and parsed.scheme != "https":
        raise ValueError("non-loopback provider URLs require HTTPS")
    return parsed.geturl(), normalized


def _public_config_digest(preset: ProviderPreset, metadata: dict[str, Any]) -> str:
    return digest_json(
        {
            "provider_id": preset.provider_id,
            "base_url": str(metadata.get("base_url", preset.base_url)),
            "model": str(metadata.get("model", preset.default_model)),
            "enabled": bool(metadata.get("enabled", False)),
        }
    )


def _sanitize_error(exc: Exception, credential: str) -> str:
    message = f"{type(exc).__name__}: {exc}"
    if credential:
        message = message.replace(credential, "***")
    return message[:500]


class ProviderHub:
    """Candidate-only local provider switchboard with no planner attachment."""

    def __init__(
        self,
        home_path: str | Path,
        ledger: EvidenceLedger | None = None,
        secret_store: Any | None = None,
    ) -> None:
        self.home_path = Path(home_path).expanduser().resolve()
        self.state_path = self.home_path / "state" / "provider_hub.json"
        self.ledger = ledger
        self.secrets = secret_store or ProviderSecretStore()
        self._lock = threading.RLock()
        self._state = self._load()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
        return {
            "schema_version": "1.1",
            "selected_provider_id": None,
            "runtime_attached": False,
            "auto_paid_fallback": False,
            "providers": {},
            "updated_at": _now(),
        }

    def _load(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return self._empty_state()
        value = json.loads(self.state_path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("provider hub state must be an object")
        if value.get("schema_version") not in {"1.0", "1.1"}:
            raise ValueError("unsupported provider hub state")
        value["schema_version"] = "1.1"
        value["runtime_attached"] = False
        value["auto_paid_fallback"] = False
        value.setdefault("providers", {})
        return value

    def _save_locked(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at"] = _now()
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self._state, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, self.state_path)

    def _record(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.ledger is not None:
            self.ledger.append(event_type, payload)

    @staticmethod
    def _preset(provider_id: str) -> ProviderPreset:
        return _PRESET_BY_ID[_provider_id(provider_id)]

    def _metadata_locked(self, provider_id: str) -> dict[str, Any]:
        return self._state["providers"].setdefault(provider_id, {})

    def list(self) -> dict[str, Any]:
        with self._lock:
            snapshot = json.loads(json.dumps(self._state))
        selected = snapshot.get("selected_provider_id")
        rows: list[dict[str, Any]] = []
        for preset in PRESETS:
            metadata = dict(snapshot["providers"].get(preset.provider_id, {}))
            row = asdict(preset)
            row.update(
                {
                    "enabled": bool(metadata.get("enabled", False)),
                    "selected": selected == preset.provider_id,
                    "model": str(metadata.get("model", preset.default_model)),
                    "configured_base_url": str(
                        metadata.get("base_url", preset.base_url)
                    ),
                    "credential_configured": bool(
                        self.secrets.has(preset.provider_id)
                    ),
                    "credential_env": provider_env_name(preset.provider_id),
                    "last_probe_at": metadata.get("last_probe_at"),
                    "last_probe_status": metadata.get("last_probe_status", "NEVER"),
                    "last_latency_ms": metadata.get("last_latency_ms"),
                    "last_error": metadata.get("last_error"),
                    "available_models": list(metadata.get("available_models", [])),
                    "configuration_digest": _public_config_digest(preset, metadata),
                }
            )
            rows.append(row)
        return {
            "schema_version": "1.1",
            "selected_provider_id": selected,
            "runtime_attached": False,
            "auto_paid_fallback": False,
            "secret_store": self.secrets.status(),
            "providers": rows,
        }

    def configure(
        self,
        provider_id: str,
        *,
        credential: str | None = None,
        enabled: bool = True,
        model: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        preset = self._preset(provider_id)
        configured_model = preset.default_model if model is None else str(model).strip()
        if not configured_model and preset.provider_id != "custom_openai":
            raise ValueError("model must not be empty")
        with self._lock:
            current = dict(self._state["providers"].get(preset.provider_id, {}))
        configured_base = preset.base_url
        if preset.custom_base_url:
            raw_base = base_url or str(current.get("base_url", preset.base_url))
            configured_base, _ = _validate_url(raw_base.rstrip("/"))
        elif base_url is not None and base_url.rstrip("/") != preset.base_url:
            raise ValueError("preset provider base_url is immutable")
        if credential is not None and credential.strip():
            self.secrets.set(preset.provider_id, credential)
        with self._lock:
            metadata = self._metadata_locked(preset.provider_id)
            metadata.update(
                {
                    "enabled": bool(enabled),
                    "model": configured_model,
                    "base_url": configured_base,
                    "updated_at": _now(),
                }
            )
            self._save_locked()
        self._record(
            "provider_hub_configured",
            {
                "provider_id": preset.provider_id,
                "enabled": bool(enabled),
                "credential_configured": self.secrets.has(preset.provider_id),
                "configuration_digest": _public_config_digest(preset, metadata),
            },
        )
        return self.get_public(preset.provider_id)

    def select(self, provider_id: str) -> dict[str, Any]:
        preset = self._preset(provider_id)
        with self._lock:
            metadata = dict(self._state["providers"].get(preset.provider_id, {}))
        if not bool(metadata.get("enabled", False)):
            raise ValueError("provider must be enabled before selection")
        if not self.secrets.has(preset.provider_id):
            raise ValueError("provider credential is not configured")
        with self._lock:
            self._state["selected_provider_id"] = preset.provider_id
            self._save_locked()
        self._record("provider_hub_selected", {"provider_id": preset.provider_id})
        return self.list()

    def remove_credential(self, provider_id: str) -> dict[str, Any]:
        preset = self._preset(provider_id)
        removed = self.secrets.delete(preset.provider_id)
        with self._lock:
            if self._state.get("selected_provider_id") == preset.provider_id:
                self._state["selected_provider_id"] = None
            metadata = self._metadata_locked(preset.provider_id)
            metadata.update(
                {
                    "enabled": False,
                    "last_probe_status": "CREDENTIAL_REMOVED",
                    "last_error": None,
                }
            )
            self._save_locked()
        self._record(
            "provider_hub_credential_removed",
            {"provider_id": preset.provider_id, "removed": removed},
        )
        return {"removed": removed, "provider": self.get_public(preset.provider_id)}

    def get_public(self, provider_id: str) -> dict[str, Any]:
        normalized = _provider_id(provider_id)
        for row in self.list()["providers"]:
            if row["provider_id"] == normalized:
                return row
        raise KeyError(normalized)

    @staticmethod
    def _request(
        preset: ProviderPreset, metadata: dict[str, Any], credential: str
    ) -> tuple[Request, tuple[str, ...]]:
        models_url = preset.models_url
        if preset.custom_base_url:
            base_url = str(metadata.get("base_url", preset.base_url)).rstrip("/")
            models_url = f"{base_url}/models"
        validated_url, addresses = _validate_url(models_url)
        headers = {"Accept": "application/json", "User-Agent": "WLS-Provider-Hub/1.1"}
        if preset.protocol == "gemini":
            headers["x-goog-api-key"] = credential
        else:
            headers["Authorization"] = f"Bearer {credential}"
        return Request(validated_url, method="GET", headers=headers), addresses

    def probe(
        self,
        provider_id: str,
        *,
        timeout_seconds: float = 15.0,
        opener: Any | None = None,
    ) -> dict[str, Any]:
        preset = self._preset(provider_id)
        with self._lock:
            metadata = dict(self._state["providers"].get(preset.provider_id, {}))
            starting_digest = _public_config_digest(preset, metadata)
        credential = self.secrets.get(preset.provider_id)
        if not credential:
            raise ValueError("provider credential is not configured")
        request, resolved_addresses = self._request(preset, metadata, credential)
        timeout = max(1.0, min(float(timeout_seconds), 30.0))
        transport = opener or build_opener(_NoRedirect)
        started = time.monotonic()
        status = "ERROR"
        http_status: int | None = None
        model_ids: list[str] = []
        error_message: str | None = None
        try:
            with transport.open(request, timeout=timeout) as response:
                http_status = int(getattr(response, "status", 200))
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(payload) > _MAX_RESPONSE_BYTES:
                    raise ValueError("provider response exceeded 2 MiB")
                value = json.loads(payload.decode("utf-8"))
                model_ids = self._model_ids(value)
                status = "PASS" if 200 <= http_status < 300 else "FAIL"
        except HTTPError as exc:
            http_status = int(exc.code)
            status = "FAIL"
            error_message = f"HTTP {exc.code}"
        except (URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            error_message = _sanitize_error(exc, credential)
        latency_ms = round((time.monotonic() - started) * 1000, 2)
        with self._lock:
            current = self._metadata_locked(preset.provider_id)
            current_digest = _public_config_digest(preset, current)
            configuration_changed = current_digest != starting_digest
            if not configuration_changed:
                current.update(
                    {
                        "last_probe_at": _now(),
                        "last_probe_status": status,
                        "last_latency_ms": latency_ms,
                        "last_error": error_message,
                        "available_models": model_ids,
                        "probe_fingerprint": digest_json(
                            {
                                "configuration_digest": starting_digest,
                                "resolved_addresses": resolved_addresses,
                                "http_status": http_status,
                            }
                        ),
                    }
                )
                self._save_locked()
        result = {
            "provider_id": preset.provider_id,
            "status": "STALE_CONFIG" if configuration_changed else status,
            "http_status": http_status,
            "latency_ms": latency_ms,
            "model_count": len(model_ids),
            "models": model_ids,
            "error": (
                "provider configuration changed during probe"
                if configuration_changed
                else error_message
            ),
            "resolved_addresses": list(resolved_addresses),
            "configuration_digest": starting_digest,
        }
        self._record("provider_hub_probe", result)
        return result

    @staticmethod
    def _model_ids(value: Any) -> builtins.list[str]:
        if isinstance(value, list):
            candidates = value
        elif isinstance(value, dict) and isinstance(value.get("data"), list):
            candidates = value["data"]
        elif isinstance(value, dict) and isinstance(value.get("models"), list):
            candidates = value["models"]
        else:
            raise ValueError("provider response does not contain a model list")
        model_ids: builtins.list[str] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            raw = item.get("id") or item.get("baseModelId") or item.get("name")
            if raw is None:
                continue
            model_id = str(raw).removeprefix("models/")
            if model_id and model_id not in model_ids:
                model_ids.append(model_id)
            if len(model_ids) >= 200:
                break
        return model_ids
