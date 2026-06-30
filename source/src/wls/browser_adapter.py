from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse
import hashlib

from .schemas import utc_now


@dataclass(slots=True)
class BrowserReadOnlyRequest:
    url: str
    allowed_hosts: set[str]
    session_digest: str
    timeout_seconds: float = 10.0


@dataclass(slots=True)
class BrowserReceipt:
    url: str
    host: str
    session_digest: str
    navigation_log: list[str]
    text_sha256: str
    screenshot_sha256: str | None = None
    downloads: list[str] = field(default_factory=list)
    observed_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrowserReadOnlyAdapter:
    def inspect_text(self, request: BrowserReadOnlyRequest, text: str) -> BrowserReceipt:
        parsed = urlparse(request.url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("browser URL must be http(s)")
        if host not in {item.lower() for item in request.allowed_hosts}:
            raise PermissionError("browser host not allowlisted")
        if not request.session_digest:
            raise ValueError("session digest is required")
        return BrowserReceipt(
            url=request.url,
            host=host,
            session_digest=request.session_digest,
            navigation_log=[request.url],
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        )
