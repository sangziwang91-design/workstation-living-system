from __future__ import annotations

from dataclasses import dataclass

from .channel_gateway import ChannelGateway, ChannelMessage
from .schemas import Event


@dataclass(slots=True)
class WeChatW0W1Adapter:
    enabled_level: str = "W1"

    def outbound_notification(self, body: str) -> dict[str, str]:
        if self.enabled_level not in {"W0", "W1"}:
            raise PermissionError("only W0/W1 are enabled")
        return {"status": "DRAFT_NOTIFICATION", "body": body}

    def read_only_query_event(self, message: ChannelMessage) -> Event:
        if self.enabled_level != "W1":
            raise PermissionError("W1 is required for read-only query ingress")
        return ChannelGateway().to_event(message)
