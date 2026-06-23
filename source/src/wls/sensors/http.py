from __future__ import annotations

from ipaddress import ip_address
from socket import getaddrinfo
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import hashlib
import json
import time

from .base import Sensor
from ..schemas import Observation


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


def host_is_private(host: str) -> bool:
    if host.lower() in {"localhost", "localhost.localdomain"}:
        return True
    try:
        addresses = {entry[4][0] for entry in getaddrinfo(host, None)}
    except OSError:
        return False
    for value in addresses:
        try:
            address = ip_address(value)
            if (
                address.is_private
                or address.is_loopback
                or address.is_link_local
                or address.is_reserved
            ):
                return True
        except ValueError:
            continue
    return False


class HttpHealthSensor(Sensor):
    def poll(
        self, previous_state: dict[str, Any]
    ) -> tuple[list[Observation], dict[str, Any]]:
        endpoints = list(self.settings.get("endpoints", []))
        allowed_hosts = {
            str(item).lower() for item in self.settings.get("allowed_hosts", [])
        }
        allow_private = bool(self.settings.get("allow_private", True))
        timeout = float(self.settings.get("timeout_seconds", 5.0))
        max_bytes = int(self.settings.get("max_bytes", 1024 * 1024))
        opener = build_opener(NoRedirect)
        current: dict[str, Any] = {}
        observations: list[Observation] = []
        previous_endpoints = dict(previous_state.get("endpoints", {}))
        for endpoint in endpoints:
            name = str(endpoint.get("name") or endpoint.get("url"))
            url = str(endpoint["url"])
            parsed = urlparse(url)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                state = {"healthy": False, "error": "invalid URL"}
            elif allowed_hosts and parsed.hostname.lower() not in allowed_hosts:
                state = {"healthy": False, "error": "host not allowlisted"}
            elif not allow_private and host_is_private(parsed.hostname):
                state = {"healthy": False, "error": "private host blocked"}
            else:
                started = time.monotonic()
                try:
                    request = Request(
                        url, method="GET", headers={"User-Agent": "WLS/1.0"}
                    )
                    with opener.open(request, timeout=timeout) as response:
                        body = response.read(max_bytes + 1)
                        if len(body) > max_bytes:
                            raise ValueError("response exceeds max_bytes")
                        text = body.decode(
                            response.headers.get_content_charset() or "utf-8",
                            errors="replace",
                        )
                        data: Any
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            data = text[:4000]
                        state = {
                            "healthy": 200 <= response.status < 300,
                            "status": response.status,
                            "latency_ms": round((time.monotonic() - started) * 1000, 2),
                            "body": data,
                        }
                except (HTTPError, URLError, TimeoutError, ValueError) as exc:
                    state = {
                        "healthy": False,
                        "error": str(exc),
                        "latency_ms": round((time.monotonic() - started) * 1000, 2),
                    }
            current[name] = state
            old = previous_endpoints.get(name)
            if (
                old is None
                or old.get("healthy") != state.get("healthy")
                or old.get("status") != state.get("status")
            ):
                observations.append(
                    Observation(
                        source=self.name,
                        kind="service_health",
                        subject=name,
                        predicate="health",
                        value=state,
                        confidence=1.0,
                        metadata={
                            "previous": old,
                            "url": url,
                            "dedupe_key": f"http:{name}:{hashlib.sha256(json.dumps(state, sort_keys=True, default=str).encode()).hexdigest()}",
                        },
                    )
                )
        return observations, {"endpoints": current}
