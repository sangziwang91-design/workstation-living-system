from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import os
import time
import threading
import hashlib
import ipaddress
import socket

from .evidence import EvidenceLedger
from .provider_secrets import ProviderSecretStore, provider_env_name


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
        provider_id="github_models",
        name="GitHub Models",
        family="GitHub",
        protocol="openai_compatible",
        base_url="https://models.github.ai/inference",
        models_url="https://models.github.ai/catalog/models",
        default_model="openai/gpt-4.1",
        registration_url="https://github.com/marketplace/models",
        docs_url="https://docs.github.com/en/github-models",
        access_note="GitHub credentials; account limits and model availability may change.",
        privacy_ceiling="P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        provider_id="gemini",
        name="Gemini API",
        family="Google",
        protocol="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta",
        models_url="https://generativelanguage.googleapis.com/v1beta/models",
        default_model="gemini-3.5-flash",
        registration_url="https://aistudio.google.com/app/apikey",
        docs_url="https://ai.google.dev/gemini-api/docs",
        access_note="Google AI Studio key; review the current free-tier and data-use terms before use.",
        privacy_ceiling="P0_PUBLIC_OR_SYNTHETIC",
    ),
    ProviderPreset(
        provider_id="groq",
        name="GroqCloud",
        family="Groq",
        protocol="openai_compatible",
        base_url="https://api.groq.com/openai/v1",
        models_url="https://api.groq.com/openai/v1/models",
        default_model="openai/gpt-oss-20b",
        registration_url="https://console.groq.com/keys",
        docs_url="https://console.groq.com/docs",
        access_note="Fast OpenAI-compatible inference; account rate limits apply.",
        privacy_ceiling="P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        provider_id="mistral",
        name="Mistral API",
        family="Mistral AI",
        protocol="openai_compatible",
        base_url="https://api.mistral.ai/v1",
        models_url="https://api.mistral.ai/v1/models",
        default_model="mistral-small-latest",
        registration_url="https://console.mistral.ai/api-keys",
        docs_url="https://docs.mistral.ai/api",
        access_note="Evaluation or paid access depends on the current account tier.",
        privacy_ceiling="P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        provider_id="openrouter",
        name="OpenRouter",
        family="OpenRouter",
        protocol="openai_compatible",
        base_url="https://openrouter.ai/api/v1",
        models_url="https://openrouter.ai/api/v1/models",
        default_model="openrouter/free",
        registration_url="https://openrouter.ai/settings/keys",
        docs_url="https://openrouter.ai/docs",
        access_note="Router with free and paid models; WLS never enables paid fallback automatically.",
        privacy_ceiling="P0_PUBLIC_OR_SYNTHETIC",
    ),
    ProviderPreset(
        provider_id="deepseek",
        name="DeepSeek API",
        family="DeepSeek",
        protocol="openai_compatible",
        base_url="https://api.deepseek.com",
        models_url="https://api.deepseek.com/models",
        default_model="deepseek-chat",
        registration_url="https://platform.deepseek.com/api_keys",
        docs_url="https://api-docs.deepseek.com/",
        access_note="Low-cost API; a positive account balance may be required.",
        privacy_ceiling="P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        provider_id="custom_openai",
        name="Custom OpenAI-Compatible",
        family="Custom",
        protocol="openai_compatible",
        base_url="https://example.invalid/v1",
        models_url="https://example.invalid/v1/models",
        default_model="",
        registration_url="",
        docs_url="",
        access_note="For an owner-approved endpoint. HTTPS is required except for loopback.",
        privacy_ceiling="OWNER_DEFINED",
        custom_base_url=True,
    ),
)

_PRESET_BY_ID = {preset.provider_id: preset for preset in PRESETS}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _calculate_fingerprint(preset: ProviderPreset, metadata: dict[str, Any], credential: str | None) -> str:
    data = {
        "provider_id": preset.provider_id,
        "base_url": str(metadata.get("base_url", preset.base_url)),
        "model": str(metadata.get("model", preset.default_model)),
        "credential_hash": hashlib.sha256(credential.encode()).hexdigest() if credential else "none"
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def _validate_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("unsupported scheme")

    if parsed.username or parsed.password:
        raise ValueError("credentials in URL blocked")

    hostname = parsed.hostname
    if not hostname:
        raise ValueError("missing hostname")

    try:
        addrs = socket.getaddrinfo(hostname, None)
        ips = {a[4][0] for a in addrs}
    except Exception:
        raise ValueError(f"could not resolve hostname: {hostname}")

    for ip_str in ips:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_loopback:
            continue
        if (ip.is_private or ip.is_reserved or ip.is_multicast or
            ip.is_unspecified or ip.is_link_local):
            raise ValueError(f"unsafe address blocked: {ip_str}")

    is_all_loopback = all(ipaddress.ip_address(ip_str).is_loopback for ip_str in ips)
    if not is_all_loopback and parsed.scheme != "https":
        raise ValueError("non-loopback provider URLs require HTTPS")

    return url


def _sanitize_config(data: Any, canary: str | None = None) -> Any:
    sensitive = {"credential", "token", "api_key", "apikey", "authorization", "bearer", "secret", "password"}
    if isinstance(data, str) and canary and canary in data:
        raise ValueError("config contains canary secret")
    if isinstance(data, dict):
        for k, v in data.items():
            if str(k).lower() in sensitive:
                 raise ValueError(f"contains sensitive field: {k}")
            _sanitize_config(v, canary)
    elif isinstance(data, (list, tuple)):
        for item in data:
            _sanitize_config(item, canary)
    return data


class ProviderHub:
    """Local provider switchboard."""

    def __init__(
        self,
        home_path: str | Path,
        ledger: EvidenceLedger | None = None,
        secret_store: Any | None = None,
    ):
        self.home_path = Path(home_path).expanduser().resolve()
        self.state_path = self.home_path / "state" / "provider_hub.json"
        self.ledger = ledger
        self.secrets = secret_store or ProviderSecretStore()
        self._lock = threading.RLock()
        with self._lock:
            self._state = self._load()

    def _empty_state(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
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
        if not isinstance(value, dict) or value.get("schema_version") != "1.0":
            raise ValueError("unsupported provider hub state")
        return value

    def _save(self) -> None:
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

    def _preset(self, provider_id: str) -> ProviderPreset:
        normalized = str(provider_id).strip().lower()
        preset = _PRESET_BY_ID.get(normalized)
        if preset is None:
            raise KeyError(f"unknown provider: {normalized}")
        return preset

    def _metadata(self, provider_id: str) -> dict[str, Any]:
        providers = self._state["providers"]
        if provider_id not in providers:
            providers[provider_id] = {}
        return providers[provider_id]

    def list(self) -> dict[str, Any]:
        with self._lock:
            rows: list[dict[str, Any]] = []
            selected = self._state.get("selected_provider_id")
            for preset in PRESETS:
                metadata = dict(self._state["providers"].get(preset.provider_id, {}))
                row = asdict(preset)
                row.update(
                    {
                        "enabled": bool(metadata.get("enabled", False)),
                        "selected": selected == preset.provider_id,
                        "model": str(metadata.get("model", preset.default_model)),
                        "configured_base_url": str(metadata.get("base_url", preset.base_url)),
                        "credential_configured": bool(self.secrets.has(preset.provider_id)),
                        "credential_env": provider_env_name(preset.provider_id),
                        "last_probe_at": metadata.get("last_probe_at"),
                        "last_probe_status": metadata.get("last_probe_status", "NEVER"),
                        "last_latency_ms": metadata.get("last_latency_ms"),
                        "last_error": metadata.get("last_error"),
                        "available_models": list(metadata.get("available_models", [])),
                    }
                )
                rows.append(row)
            return {
                "schema_version": "1.0",
                "selected_provider_id": selected,
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
        with self._lock:
            preset = self._preset(provider_id)
            metadata = self._metadata(preset.provider_id)
            _sanitize_config(model)
            if credential is not None and credential.strip():
                self.secrets.set(preset.provider_id, credential)
            configured_base_url = preset.base_url
            if preset.custom_base_url:
                if base_url is None:
                    configured_base_url = str(metadata.get("base_url", preset.base_url))
                else:
                    configured_base_url = _validate_url(base_url)
            elif base_url is not None and base_url.rstrip("/") != preset.base_url:
                raise ValueError("preset provider base_url is immutable")

            configured_model = preset.default_model if model is None else str(model).strip()
            if not configured_model and preset.provider_id != "custom_openai":
                raise ValueError("model must not be empty")

            metadata.update(
                {
                    "enabled": bool(enabled),
                    "model": configured_model,
                    "base_url": configured_base_url,
                    "updated_at": _now(),
                }
            )
            self._save()
            self._record(
                "provider_hub_configured",
                {
                    "provider_id": preset.provider_id,
                    "enabled": bool(enabled),
                    "credential_configured": self.secrets.has(preset.provider_id),
                },
            )
            return self.get_public(preset.provider_id)

    def select(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            preset = self._preset(provider_id)
            metadata = self._metadata(preset.provider_id)
            if not bool(metadata.get("enabled", False)):
                raise ValueError("provider must be enabled before selection")
            if not self.secrets.has(preset.provider_id):
                raise ValueError("provider credential is not configured")
            self._state["selected_provider_id"] = preset.provider_id
            self._save()
            self._record(
                "provider_hub_selected",
                {"provider_id": preset.provider_id},
            )
            return self.list()

    def remove_credential(self, provider_id: str) -> dict[str, Any]:
        with self._lock:
            preset = self._preset(provider_id)
            removed = self.secrets.delete(preset.provider_id)
            if self._state.get("selected_provider_id") == preset.provider_id:
                self._state["selected_provider_id"] = None
            metadata = self._metadata(preset.provider_id)
            metadata["enabled"] = False
            metadata["last_probe_status"] = "CREDENTIAL_REMOVED"
            metadata["last_error"] = None
            self._save()
            self._record(
                "provider_hub_credential_removed",
                {"provider_id": preset.provider_id, "removed": removed},
            )
            return {"removed": removed, "provider": self.get_public(preset.provider_id)}

    def get_public(self, provider_id: str) -> dict[str, Any]:
        for row in self.list()["providers"]:
            if row["provider_id"] == provider_id:
                return row
        raise KeyError(provider_id)

    def _probe_request(self, preset: ProviderPreset, credential: str) -> Request:
        metadata = self._metadata(preset.provider_id)
        models_url = preset.models_url
        if preset.custom_base_url:
            base_url = _validate_url(str(metadata.get("base_url", preset.base_url)))
            models_url = f"{base_url}/models"
        headers = {
            "Accept": "application/json",
            "User-Agent": "WLS-Provider-Hub/1.0",
        }
        if preset.protocol == "gemini":
            headers["x-goog-api-key"] = credential
        else:
            headers["Authorization"] = f"Bearer {credential}"
        return Request(models_url, method="GET", headers=headers)

    def probe(self, provider_id: str, *, timeout_seconds: float = 15.0, opener: Any | None = None) -> dict[str, Any]:
        with self._lock:
            preset = self._preset(provider_id)
            credential = self.secrets.get(preset.provider_id)
            if not credential:
                raise ValueError("provider credential is not configured")
            timeout = max(1.0, min(float(timeout_seconds), 30.0))
            request = self._probe_request(preset, credential)
            transport = opener or build_opener(_NoRedirect)
            started = time.monotonic()
            status = "ERROR"
            error_message: str | None = None
            http_status: int | None = None
            model_ids: list[str] = []
            try:
                with transport.open(request, timeout=timeout) as response:
                    http_status = int(getattr(response, "status", 200))
                    payload = response.read(2 * 1024 * 1024 + 1)
                    if len(payload) > 2 * 1024 * 1024:
                        raise ValueError("provider response exceeded 2 MiB")
                    value = json.loads(payload.decode("utf-8"))
                    model_ids = self._model_ids(value)
                    status = "PASS" if 200 <= http_status < 300 else "FAIL"
            except HTTPError as exc:
                http_status = int(exc.code)
                status = "FAIL"
                error_message = f"HTTP {exc.code}"
            except (URLError, TimeoutError) as exc:
                error_message = type(exc).__name__
            except Exception as exc:
                sanitized = str(exc).replace(credential, "***")
                error_message = f"{type(exc).__name__}: {sanitized[:300]}"
            latency_ms = round((time.monotonic() - started) * 1000, 2)
            metadata = self._metadata(preset.provider_id)
            metadata.update(
                {
                    "last_probe_at": _now(),
                    "last_probe_status": status,
                    "last_latency_ms": latency_ms,
                    "last_error": error_message,
                    "available_models": model_ids,
                    "probe_fingerprint": _calculate_fingerprint(preset, metadata, credential),
                }
            )
            self._save()
            result = {
                "provider_id": preset.provider_id,
                "status": status,
                "http_status": http_status,
                "latency_ms": latency_ms,
                "model_count": len(model_ids),
                "models": model_ids,
                "error": error_message,
            }
            self._record("provider_hub_probe", result)
            return result

    @staticmethod
    def _model_ids(value: Any) -> list[str]:
        if isinstance(value, list):
            candidates = value
        elif isinstance(value, dict) and isinstance(value.get("data"), list):
            candidates = value["data"]
        elif isinstance(value, dict) and isinstance(value.get("models"), list):
            candidates = value["models"]
        else:
            candidates = []
        model_ids: list[str] = []
        for item in candidates:
            if not isinstance(item, dict):
                continue
            raw = item.get("id") or item.get("baseModelId") or item.get("name")
            if raw is None:
                continue
            model_id = str(raw)
            if model_id.startswith("models/"):
                model_id = model_id.removeprefix("models/")
            if model_id and model_id not in model_ids:
                model_ids.append(model_id)
            if len(model_ids) >= 200:
                break
        return model_ids
