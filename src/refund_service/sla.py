"""SLA monitor (KAN-4): At risk at 20h, Breached at 24h, measured from approval.

The monitor evaluates every open refund against approval timestamps (never
"since last run"), so a missed run is self-healing. Intended to be scheduled
every 15 minutes, 24x7, including weekends and bank holidays.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, Iterable, Optional

from .models import ApprovalStatus, Refund, RefundStatus
from .notifications import (
    ENGINEERING_CHANNEL,
    ESCALATION_CHANNEL,
    OPS_CHANNEL,
    OPS_LEAD_CHANNEL,
    Notifier,
)

AT_RISK_AFTER = timedelta(hours=20)
BREACH_AFTER = timedelta(hours=24)
ESCALATE_AFTER = timedelta(minutes=30)
REALERT_EVERY = timedelta(hours=24)
JOB_STALE_AFTER = timedelta(minutes=30)


class SlaClass(str, Enum):
    OK = "OK"
    AT_RISK = "At risk"
    BREACHED = "Breached"


def sla_deadline(refund: Refund) -> datetime:
    return refund.approved_at + BREACH_AFTER


def classify(refund: Refund, now: datetime) -> SlaClass:
    end = refund.completed_at or now
    elapsed = end - refund.approved_at
    if elapsed >= BREACH_AFTER:
        return SlaClass.BREACHED
    if refund.completed_at is None and elapsed >= AT_RISK_AFTER:
        return SlaClass.AT_RISK
    return SlaClass.OK


def time_remaining(refund: Refund, now: datetime) -> timedelta:
    return max(timedelta(0), sla_deadline(refund) - now)


class SlaMonitor:
    def __init__(self, notifier: Notifier) -> None:
        self.notifier = notifier
        self.last_run: Optional[datetime] = None

    def run(self, refunds: Iterable[Refund], now: datetime) -> None:
        for refund in refunds:
            if refund.approval_status is not ApprovalStatus.APPROVED or refund.approved_at is None:
                continue
            if refund.status is RefundStatus.COMPLETED:
                refund.sla_alerts_open = False  # close open alerts; breach flag is kept
                continue
            self._evaluate(refund, now)
        self.last_run = now

    def check_heartbeat(self, now: datetime) -> bool:
        """Alert engineering if no run has completed for 30 minutes."""
        if self.last_run is None or now - self.last_run >= JOB_STALE_AFTER:
            self.notifier.ops(ENGINEERING_CHANNEL, "sla_job_stale",
                              last_run=self.last_run)
            return True
        return False

    def acknowledge(self, refund: Refund) -> None:
        refund.sla_acknowledged = True

    def _evaluate(self, refund: Refund, now: datetime) -> None:
        cls = classify(refund, now)
        remaining = time_remaining(refund, now)
        context = dict(
            approved_at=refund.approved_at,
            status=refund.status.value,
            gateway_reference=refund.provider_reference,
            time_remaining=remaining,
        )
        if cls is SlaClass.AT_RISK and refund.sla_state is None:
            refund.sla_state = SlaClass.AT_RISK.value
            refund.sla_alert_at = now
            refund.sla_alerts_open = True
            self.notifier.ops(OPS_CHANNEL, "refund_at_risk", refund.id, **context)
        elif cls is SlaClass.BREACHED:
            if refund.sla_state != SlaClass.BREACHED.value:
                refund.sla_state = SlaClass.BREACHED.value
                refund.sla_alerts_open = True
                refund.sla_last_breach_alert_at = now
                for channel in (OPS_LEAD_CHANNEL, ESCALATION_CHANNEL):
                    self.notifier.ops(channel, "refund_breached", refund.id, **context)
                self.notifier.customer(
                    refund.id, "refund_delayed",
                    prioritized=True, next_update_within_hours=24,
                )
            elif now - refund.sla_last_breach_alert_at >= REALERT_EVERY:
                refund.sla_last_breach_alert_at = now
                self.notifier.ops(OPS_LEAD_CHANNEL, "refund_breached_realert", refund.id,
                                  elapsed=now - refund.approved_at, **context)
        if (
            refund.sla_state == SlaClass.AT_RISK.value
            and not refund.sla_acknowledged
            and not refund.sla_escalated
            and refund.sla_alert_at is not None
            and now - refund.sla_alert_at >= ESCALATE_AFTER
        ):
            refund.sla_escalated = True
            self.notifier.ops(OPS_LEAD_CHANNEL, "at_risk_escalated", refund.id, **context)


def sla_report(refunds: Iterable[Refund], now: datetime) -> Dict[str, int]:
    """Within SLA vs Breached SLA. Completed-late refunds stay counted as breached."""
    report = {"Within SLA": 0, "Breached SLA": 0}
    for refund in refunds:
        if refund.approved_at is None:
            continue
        if classify(refund, now) is SlaClass.BREACHED:
            report["Breached SLA"] += 1
        elif refund.status is RefundStatus.COMPLETED:
            report["Within SLA"] += 1
    return report
