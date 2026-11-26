"""Single notification service: customer notifications and ops/escalation alerts.

This in-memory implementation records messages; swap the delivery layer for the
real channels (email/SMS/push, on-call tooling) once they are known.
"""
from __future__ import annotations

from typing import List

OPS_CHANNEL = "payments-ops-oncall"
OPS_LEAD_CHANNEL = "payments-ops-lead"
ESCALATION_CHANNEL = "payments-escalation"
ENGINEERING_CHANNEL = "engineering-oncall"
SUPPORT_CHANNEL = "support-refund-review"


class Notifier:
    def __init__(self) -> None:
        self.customer_messages: List[dict] = []
        self.ops_alerts: List[dict] = []

    def customer(self, refund_id: str, event: str, **context) -> None:
        self.customer_messages.append({"refund_id": refund_id, "event": event, **context})

    def ops(self, channel: str, event: str, refund_id: str = "", **context) -> None:
        self.ops_alerts.append(
            {"channel": channel, "event": event, "refund_id": refund_id, **context}
        )
