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

from .evidence import EvidenceLedger
from .provider_secrets import ProviderSecretStore, normalize_provider_id, provider_env_name


_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_MAX_MODELS = 200


@dataclass(frozen=True, slots=True)
class ProviderPreset:
    provider_id: str
    name: str
    family: str
    protocol: str
    base_url: str
    models_url: str
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
        "github_models",
        "https://models.github.ai/inference",
        "https://models.github.ai/catalog/models",
        "https://github.com/marketplace/models",
        "https://docs.github.com/en/github-models",
        "GitHub account access; visible models and limits are discovered at runtime.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "gemini",
        "Gemini API",
        "Google",
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        "https://generativelanguage.googleapis.com/v1beta/models",
        "https://aistudio.google.com/app/apikey",
        "https://ai.google.dev/gemini-api/docs",
        "Google AI Studio credential; free-tier and data-use terms remain provider-controlled.",
        "P0_PUBLIC_OR_SYNTHETIC",
    ),
    ProviderPreset(
        "groq",
        "GroqCloud",
        "Groq",
        "openai_compatible",
        "https://api.groq.com/openai/v1",
        "https://api.groq.com/openai/v1/models",
        "https://console.groq.com/keys",
        "https://console.groq.com/docs",
        "Fast compatible inference; account-specific rate limits apply.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "mistral",
        "Mistral API",
        "Mistral AI",
        "openai_compatible",
        "https://api.mistral.ai/v1",
        "https://api.mistral.ai/v1/models",
        "https://console.mistral.ai/api-keys",
        "https://docs.mistral.ai/api",
        "Access level depends on the current account tier.",
        "P1_REDACTED_ONLY",
    ),
    ProviderPreset(
        "openrouter",
        "OpenRouter",
        "OpenRouter",
        "openai_compatible",
        "https://openrouter.ai/api/v1",
        "https://openrouter.ai/api/v1/models",
        "https://openrouter.ai/settings/keys",
        "https://openrouter.ai/docs",
        "Router with free and paid models; paid fallback is permanently off in this target.",
        "P0_PUBLIC_OR_SYNTHETIC",
    ),
    ProviderPreset(
        "deepseek",
        "DeepSeek API",
        "DeepSeek",
        "openai_compatible",
        "https://api.deepseek.com",
        "https://api.deepseek.com/models",
        "https://platform.deepseek.com/api_keys",
        "https://api-docs.deepseek.com/",
        "Account balance or other provider-controlled requirements may apply.",
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
        "Owner-approved endpoint only; HTTPS is required outside loopback.",
        "OWNER_DEFINED",
        True,
    ),
)

_PRESET_BY_ID = {preset.provider_id: preset for preset in PRESETS}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_https_or_loopback(url: str) -> str:
    normalized = str(url).strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("invalid provider URL")
    if parsed.username or parsed.password:
        raise ValueError("provider URL must not contain credentials")
    if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("non-loopback provider URLs require HTTPS")
    return normalized


class ProviderHub:
    """Candidate-only provider switchboard; never a second planner or truth source."""

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
        self._state = self._load()

    @staticmethod
    def _empty_state() -> dict[str, Any]:
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
        if not isinstance(value.get("providers"), dict):
            raise ValueError("provider hub providers must be an object")
        value["runtime_attached"] = False
        value["auto_paid_fallback"] = False
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
        normalized = normalize_provider_id(provider_id)
        preset = _PRESET_BY_ID.get(normalized)
        if preset is None:
            raise KeyError(f"unknown provider: {normalized}")
        return preset

    def _metadata(self, provider_id: str) -> dict[str, Any]:
        providers = self._state["providers"]
        value = providers.get(provider_id)
        if not isinstance(value, dict):
            value = {}
            providers[provider_id] = value
        return value

    def list(self) -> dict[str, Any]:
        selected = self._state.get("selected_provider_id")
        rows: list[dict[str, Any]] = []
        for preset in PRESETS:
            metadata = dict(self._state["providers"].get(preset.provider_id, {}))
            metadata.pop("credential", None)
            row = asdict(preset)
            row.update(
                {
                    "enabled": bool(metadata.get("enabled", False)),
                    "selected": selected == preset.provider_id,
                    "model": str(metadata.get("model", "")),
                    "configured_base_url": str(metadata.get("base_url", preset.base_url)),
                    "credential_configured": bool(self.secrets.has(preset.provider_id)),
                    "credential_env": provider_env_name(preset.provider_id),
                    "last_probe_at": metadata.get("last_probe_at"),
                    "last_probe_status": metadata.get("last_probe_status", "NEVER"),
                    "last_latency_ms": metadata.get("last_latency_ms"),
                    "last_error": metadata.get("last_error"),
                    "available_models": list(metadata.get("available_models", [])),
                    "write_authority": "NONE",
                    "canonical_truth_authority": "NONE",
                    "hard_spend_cap_usd": 0,
                    "auto_paid_fallback": False,
                    "runtime_attached": False,
                }
            )
            rows.append(row)
        return {
            "schema_version": "1.0",
            "selected_provider_id": selected,
            "runtime_attached": False,
            "auto_paid_fallback": False,
            "secret_store": self.secrets.status(),
            "providers": rows,
        }

    def get_public(self, provider_id: str) -> dict[str, Any]:
        normalized = normalize_provider_id(provider_id)
        for row in self.list()["providers"]:
            if row["provider_id"] == normalized:
                return row
        raise KeyError(normalized)

    def configure(
        self,
        provider_id: str,
        *,
        enabled: bool = True,
        model: str | None = None,
        base_url: str | None = None,
    ) -> dict[str, Any]:
        preset = self._preset(provider_id)
        metadata = self._metadata(preset.provider_id)
        if preset.custom_base_url:
            configured_base_url = _validate_https_or_loopback(
                base_url or str(metadata.get("base_url", preset.base_url))
            )
        else:
            if base_url is not None and base_url.rstrip("/") != preset.base_url:
                raise ValueError("preset provider base_url is immutable")
            configured_base_url = preset.base_url
        configured_model = str(metadata.get("model", "")) if model is None else str(model).strip()
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
                "runtime_attached": False,
            },
        )
        return self.get_public(preset.provider_id)

    def select(self, provider_id: str) -> dict[str, Any]:
        preset = self._preset(provider_id)
        metadata = self._metadata(preset.provider_id)
        if not bool(metadata.get("enabled", False)):
            raise ValueError("provider must be enabled before selection")
        if not self.secrets.has(preset.provider_id):
            raise ValueError("provider credential is not configured")
        if metadata.get("last_probe_status") != "PASS":
            raise ValueError("provider must pass a current connectivity probe before selection")
        self._state["selected_provider_id"] = preset.provider_id
        self._save()
        self._record(
            "provider_hub_selected",
            {"provider_id": preset.provider_id, "runtime_attached": False},
        )
        return self.list()

    def remove_credential(self, provider_id: str) -> dict[str, Any]:
        preset = self._preset(provider_id)
        removed = self.secrets.delete(preset.provider_id)
        if self._state.get("selected_provider_id") == preset.provider_id:
            self._state["selected_provider_id"] = None
        metadata = self._metadata(preset.provider_id)
        metadata.update(
            {
                "enabled": False,
                "last_probe_status": "CREDENTIAL_REMOVED",
                "last_error": None,
                "available_models": [],
            }
        )
        self._save()
        self._record(
            "provider_hub_credential_removed",
            {"provider_id": preset.provider_id, "removed": removed},
        )
        return {"removed": removed, "provider": self.get_public(preset.provider_id)}

    def _probe_request(self, preset: ProviderPreset, credential: str) -> Request:
        metadata = self._metadata(preset.provider_id)
        models_url = preset.models_url
        if preset.custom_base_url:
            base_url = _validate_https_or_loopback(str(metadata.get("base_url", preset.base_url)))
            models_url = f"{base_url}/models"
        headers = {"Accept": "application/json", "User-Agent": "WLS-Provider-Hub/0.2"}
        if preset.protocol == "gemini":
            headers["x-goog-api-key"] = credential
        else:
            headers["Authorization"] = f"Bearer {credential}"
        if preset.protocol == "github_models":
            headers["Accept"] = "application/vnd.github+json"
        return Request(models_url, method="GET", headers=headers)

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
            model_id = str(raw).removeprefix("models/")
            if model_id and model_id not in model_ids:
                model_ids.append(model_id)
            if len(model_ids) >= _MAX_MODELS:
                break
        return model_ids

    def probe(
        self,
        provider_id: str,
        *,
        timeout_seconds: float = 15.0,
        opener: Any | None = None,
    ) -> dict[str, Any]:
        preset = self._preset(provider_id)
        credential = self.secrets.get(preset.provider_id)
        if not credential:
            raise ValueError("provider credential is not configured")
        timeout = max(1.0, min(float(timeout_seconds), 30.0))
        transport = opener or build_opener(_NoRedirect)
        request = self._probe_request(preset, credential)
        started = time.monotonic()
        status = "ERROR"
        error_message: str | None = None
        http_status: int | None = None
        model_ids: list[str] = []
        try:
            with transport.open(request, timeout=timeout) as response:
                http_status = int(getattr(response, "status", 200))
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(payload) > _MAX_RESPONSE_BYTES:
                    raise ValueError("provider response exceeded 2 MiB")
                model_ids = self._model_ids(json.loads(payload.decode("utf-8")))
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
            "credential_exposed": False,
            "runtime_attached": False,
        }
        self._record("provider_hub_probe", result)
        return result
