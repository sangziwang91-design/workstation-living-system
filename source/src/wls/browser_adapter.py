from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import hashlib

from .schemas import utc_now


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


@dataclass(slots=True)
class BrowserReadOnlyRequest:
    url: str
    allowed_hosts: set[str]
    session_digest: str
    timeout_seconds: float = 10.0
    max_bytes: int = 1024 * 1024


@dataclass(slots=True)
class BrowserReceipt:
    url: str
    host: str
    session_digest: str
    navigation_log: list[str]
    text_sha256: str
    status_code: int | None = None
    bytes_read: int = 0
    screenshot_sha256: str | None = None
    downloads: list[str] = field(default_factory=list)
    observed_at: str = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BrowserReadOnlyAdapter:
    def inspect_text(self, request: BrowserReadOnlyRequest, text: str) -> BrowserReceipt:
        parsed = self._validate(request)
        return BrowserReceipt(
            url=request.url,
            host=(parsed.hostname or "").lower(),
            session_digest=request.session_digest,
            navigation_log=[request.url],
            text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            bytes_read=len(text.encode("utf-8")),
        )

    def fetch_text(self, request: BrowserReadOnlyRequest) -> BrowserReceipt:
        parsed = self._validate(request)
        max_bytes = max(1, min(5 * 1024 * 1024, int(request.max_bytes)))
        opener = build_opener(NoRedirect)
        http_request = Request(request.url, method="GET", headers={"User-Agent": "WLS/1.0"})
        with opener.open(http_request, timeout=max(0.2, min(30.0, request.timeout_seconds))) as response:
            body = response.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise ValueError("response exceeds max_bytes")
            text = body.decode(
                response.headers.get_content_charset() or "utf-8",
                errors="replace",
            )
            return BrowserReceipt(
                url=request.url,
                host=(parsed.hostname or "").lower(),
                session_digest=request.session_digest,
                navigation_log=[request.url],
                text_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                status_code=response.status,
                bytes_read=len(body),
            )

    @staticmethod
    def _validate(request: BrowserReadOnlyRequest):
        parsed = urlparse(request.url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"} or not host:
            raise ValueError("browser URL must be http(s)")
        if host not in {item.lower() for item in request.allowed_hosts}:
            raise PermissionError("browser host not allowlisted")
        if not request.session_digest:
            raise ValueError("session digest is required")
        return parsed
