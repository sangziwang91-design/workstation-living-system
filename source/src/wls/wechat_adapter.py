from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .channel_gateway import ChannelGateway, ChannelMessage
from .schemas import Event, digest_json, utc_now


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

    def approval_request_notification(self, action: dict[str, Any]) -> dict[str, Any]:
        if self.enabled_level != "W2":
            raise PermissionError("W2 is required for approval request drafts")
        if action.get("status") != "WAITING_APPROVAL":
            raise ValueError("approval request requires WAITING_APPROVAL action")
        digest = digest_json(
            {
                "action_id": action.get("action_id"),
                "tool": action.get("tool"),
                "purpose": action.get("purpose"),
                "risk": action.get("risk"),
            }
        )
        return {
            "status": "DRAFT_APPROVAL_REQUEST",
            "channel": "wechat",
            "enabled_level": self.enabled_level,
            "action_id": action.get("action_id"),
            "plan_id": action.get("plan_id"),
            "tool": action.get("tool"),
            "risk": action.get("risk"),
            "action_digest": digest,
            "approval_authority": "ApprovalManager",
            "writes_canonical_state": False,
            "direct_tool_execution": False,
            "created_at": utc_now(),
        }

    def approval_decision_event(
        self,
        message: ChannelMessage,
        *,
        action_id: str,
        decision: str,
        reason: str,
    ) -> Event:
        if self.enabled_level != "W2":
            raise PermissionError("W2 is required for approval decision ingress")
        normalized = decision.upper()
        if normalized not in {"APPROVE", "REJECT"}:
            raise ValueError("approval decision must be APPROVE or REJECT")
        return Event(
            event_type="wechat.approval_decision.requested",
            source="channel:wechat",
            payload={
                "message_id": message.message_id,
                "sender_id": message.sender_id,
                "action_id": action_id,
                "decision": normalized,
                "reason": reason,
                "allowed_next_authority": "ApprovalManager",
                "direct_tool_execution": False,
                "writes_canonical_state": False,
            },
            salience_hint=0.8,
            dedupe_key=digest_json(
                {
                    "message_id": message.message_id,
                    "action_id": action_id,
                    "decision": normalized,
                }
            ),
        )
