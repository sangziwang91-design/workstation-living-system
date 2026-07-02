from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .channel_gateway import ChannelGateway, ChannelMessage
from .schemas import Event, digest_json


@dataclass(slots=True)
class WeChatW0W1Adapter:
    enabled_level: str = "W1"

    def outbound_notification(self, body: str) -> dict[str, str]:
        if self.enabled_level not in {"W0", "W1"}:
            raise PermissionError("only W0/W1 are enabled")
        return {"status": "DRAFT_NOTIFICATION", "body": body}

    def console_digest_notification(self, projection: dict[str, Any]) -> dict[str, Any]:
        if self.enabled_level not in {"W0", "W1"}:
            raise PermissionError("only W0/W1 are enabled")
        if projection.get("mode") != "READ_ONLY_PROJECTION":
            raise PermissionError("WeChat notification requires read-only projection")
        if projection.get("writes_canonical_state") or projection.get("direct_tool_execution"):
            raise PermissionError("WeChat W0/W1 cannot execute or write canonical state")
        panel_ids = projection.get("panel_ids", [])
        return {
            "status": "DRAFT_NOTIFICATION",
            "channel": "wechat",
            "enabled_level": self.enabled_level,
            "projection_digest": projection.get("projection_digest")
            or digest_json(projection),
            "summary": {
                "surface": projection.get("surface"),
                "panel_count": len(panel_ids) if isinstance(panel_ids, list) else 0,
                "panel_ids": panel_ids if isinstance(panel_ids, list) else [],
            },
            "writes_canonical_state": False,
            "direct_tool_execution": False,
        }

    def read_only_query_event(self, message: ChannelMessage) -> Event:
        if self.enabled_level != "W1":
            raise PermissionError("W1 is required for read-only query ingress")
        return ChannelGateway().to_event(message)
