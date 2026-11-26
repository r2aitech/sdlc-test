"""Refund domain model, state machine and refundable-balance check.

All timestamps are timezone-aware; store in UTC and display in IST.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


class RefundStatus(str, Enum):
    INITIATED = "Initiated"
    PROCESSING = "Processing"
    COMPLETED = "Completed"
    FAILED = "Failed"


class ApprovalStatus(str, Enum):
    PENDING = "Pending"
    APPROVED = "Approved"
    REJECTED = "Rejected"


ALLOWED_TRANSITIONS = {
    RefundStatus.INITIATED: {RefundStatus.PROCESSING, RefundStatus.FAILED},
    RefundStatus.PROCESSING: {RefundStatus.COMPLETED, RefundStatus.FAILED},
    # Failed may return to Initiated on a customer retry (KAN-3).
    RefundStatus.FAILED: {RefundStatus.INITIATED},
    RefundStatus.COMPLETED: set(),
}

FINAL_STATUSES = {RefundStatus.COMPLETED, RefundStatus.FAILED}


class InvalidTransition(Exception):
    pass


class InsufficientRefundableBalance(Exception):
    pass


@dataclass
class Payment:
    id: str
    upi_transaction_ref: str
    refundable_balance: Decimal
    captured: bool = True

    def reserve(self, amount: Decimal) -> None:
        if amount > self.refundable_balance:
            raise InsufficientRefundableBalance("Amount exceeds refundable balance")
        self.refundable_balance -= amount

    def release(self, amount: Decimal) -> None:
        self.refundable_balance += amount


@dataclass
class HistoryEntry:
    status: RefundStatus
    at: datetime
    source: str
    failure_code: Optional[str] = None


class AuditLog:
    def __init__(self) -> None:
        self.entries: List[dict] = []

    def record(self, refund: "Refund", payment_txn_ref: Optional[str], at: datetime) -> None:
        self.entries.append(
            {
                "refund_id": refund.id,
                "amount": refund.amount,
                "original_transaction_reference": payment_txn_ref,
                "status": refund.status,
                "approved_at": refund.approved_at,
                "initiated_at": refund.initiated_at,
                "recorded_at": at,
            }
        )


@dataclass
class Refund:
    id: str
    reference: str
    order_id: str
    customer_id: str
    payment_id: str
    amount: Decimal
    upi_handle: str
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED
    approved_at: Optional[datetime] = None
    customer_mobile: Optional[str] = None
    status: RefundStatus = RefundStatus.INITIATED
    initiated_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    failure_code: Optional[str] = None
    failure_reason: Optional[str] = None
    provider_reference: Optional[str] = None
    submitted: bool = False
    retry_round: int = 0
    submit_attempts: int = 0
    history: List[HistoryEntry] = field(default_factory=list)
    # SLA tracking (see sla.py)
    sla_state: Optional[str] = None
    sla_alert_at: Optional[datetime] = None
    sla_last_breach_alert_at: Optional[datetime] = None
    sla_acknowledged: bool = False
    sla_escalated: bool = False
    sla_alerts_open: bool = False

    @classmethod
    def initiate(cls, now: datetime, source: str = "refund-service", **kwargs) -> "Refund":
        refund = cls(initiated_at=now, updated_at=now, **kwargs)
        refund.history.append(HistoryEntry(RefundStatus.INITIATED, now, source))
        return refund

    @property
    def idempotency_key(self) -> str:
        """Deterministic per refund; a customer retry with a new UPI ID gets a new round."""
        if self.retry_round == 0:
            return f"refund-{self.id}"
        return f"refund-{self.id}-r{self.retry_round}"

    def transition(
        self,
        new: RefundStatus,
        now: datetime,
        source: str,
        audit: Optional[AuditLog] = None,
        payment_txn_ref: Optional[str] = None,
        failure_code: Optional[str] = None,
        failure_reason: Optional[str] = None,
    ) -> None:
        if new not in ALLOWED_TRANSITIONS[self.status]:
            raise InvalidTransition(f"{self.status.value} -> {new.value}")
        self.status = new
        self.updated_at = now
        self.history.append(HistoryEntry(new, now, source, failure_code))
        if new is RefundStatus.FAILED:
            self.failure_code = failure_code
            self.failure_reason = failure_reason
        elif new is RefundStatus.COMPLETED:
            self.completed_at = now
        elif new is RefundStatus.INITIATED:
            self.failure_code = None
            self.failure_reason = None
        if new in FINAL_STATUSES and audit is not None:
            audit.record(self, payment_txn_ref, now)
