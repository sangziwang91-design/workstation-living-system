from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .schemas import Event, digest_json, utc_now


@dataclass(slots=True)
class ChannelMessage:
    channel: str
    sender_id: str
    content: str
    message_id: str
    received_at: str = field(default_factory=utc_now)
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelGateway:
    """Normalize external channel input into Events only."""

    def to_event(self, message: ChannelMessage) -> Event:
        if not message.channel.strip() or not message.message_id.strip():
            raise ValueError("channel and message_id are required")
        payload = {
            "channel": message.channel,
            "sender_id": message.sender_id,
            "content": message.content,
            "message_id": message.message_id,
            "metadata": dict(message.metadata),
        }
        dedupe_key = digest_json(
            {
                "channel": message.channel,
                "message_id": message.message_id,
                "sender_id": message.sender_id,
            }
        )
        return Event(
            event_type="channel.message",
            source=f"channel:{message.channel}",
            payload=payload,
            salience_hint=0.6,
            occurred_at=message.received_at,
            dedupe_key=dedupe_key,
        )
