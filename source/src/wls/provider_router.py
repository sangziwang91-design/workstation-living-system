from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

from .config import RuntimeConfig


@dataclass(slots=True)
class ProviderDescriptor:
    provider_id: str
    locality: str
    capabilities: set[str]
    cost_class: str = "free"
    latency_class: str = "normal"
    paid: bool = False
    remote: bool = False


@dataclass(slots=True)
class RouteRequest:
    required_capability: str
    privacy: str = "local_only"
    max_cost_class: str = "free"
    risk_ceiling: str = "READ"
    fallback_chain: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProviderRoute:
    provider_id: str
    rationale: str
    fallback_chain: list[str]
    evidence: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ProviderRouter:
    """Deterministic route policy; Planner remains the planning authority."""

    COST_RANK = {"free": 0, "low": 1, "metered": 2, "high": 3}

    def __init__(self, providers: list[ProviderDescriptor]):
        self.providers = {provider.provider_id: provider for provider in providers}

    @classmethod
    def from_config(cls, config: RuntimeConfig) -> "ProviderRouter":
        provider_type = str(config.provider.get("type", "cognitive"))
        cost_class = str(config.provider.get("cost_class", "free"))
        base_url = str(config.provider.get("base_url", ""))
        parsed = urlparse(base_url)
        local_host = (parsed.hostname or "").lower() in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        remote = provider_type == "openai_compatible" and not local_host
        base = {
            "deterministic": ProviderDescriptor(
                provider_id="deterministic",
                locality="local",
                capabilities={"planning"},
                cost_class="free",
            ),
            "cognitive": ProviderDescriptor(
                provider_id="cognitive",
                locality="local",
                capabilities={"planning"},
                cost_class="free",
            ),
        }
        if provider_type == "openai_compatible":
            base["openai_compatible"] = ProviderDescriptor(
                    provider_id="openai_compatible",
                    locality="remote" if remote else "local",
                    capabilities={"planning"},
                cost_class=cost_class,
                latency_class=str(config.provider.get("latency_class", "normal")),
                paid=bool(config.provider.get("paid", cost_class != "free")),
                remote=remote,
            )
        providers = []
        if provider_type in base:
            providers.append(base[provider_type])
        providers.extend(item for key, item in base.items() if key != provider_type)
        return cls(providers)

    def choose(self, request: RouteRequest) -> ProviderRoute:
        for provider in self.providers.values():
            if request.required_capability not in provider.capabilities:
                continue
            if request.privacy == "local_only" and provider.remote:
                continue
            if self.COST_RANK.get(provider.cost_class, 99) > self.COST_RANK.get(
                request.max_cost_class, 0
            ):
                continue
            if provider.paid and request.max_cost_class == "free":
                continue
            return ProviderRoute(
                provider_id=provider.provider_id,
                rationale="first provider satisfying capability, privacy, and cost policy",
                fallback_chain=list(request.fallback_chain),
                evidence={"provider": asdict(provider), "request": asdict(request)},
            )
        raise PermissionError("no provider satisfies route policy")
