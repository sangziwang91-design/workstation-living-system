from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
