"""Async refund processor: submits approved refunds to the UPI gateway.

Guarantees: only approved refunds are sent, the amount must fit the refundable
balance, a refund is submitted at most once (claim under a lock + deterministic
idempotency key) and gateway outages leave the refund retryable with the same key.
"""
from __future__ import annotations

import threading
from datetime import datetime
from enum import Enum
from typing import Dict, Iterable, Optional

from .gateway import GatewayRejected, GatewayUnavailable, UpiGateway, failure_reason_for
from .models import (
    ApprovalStatus,
    AuditLog,
    InsufficientRefundableBalance,
    Payment,
    Refund,
    RefundStatus,
)
from .notifications import OPS_CHANNEL, SUPPORT_CHANNEL, Notifier

SOURCE = "refund-processor"


class Outcome(str, Enum):
    SKIPPED = "skipped"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    RETRY = "retry"
    SUBMITTED = "submitted"


class RefundProcessor:
    def __init__(
        self,
        gateway: UpiGateway,
        payments: Dict[str, Payment],
        notifier: Notifier,
        audit: AuditLog,
    ) -> None:
        self.gateway = gateway
        self.payments = payments
        self.notifier = notifier
        self.audit = audit
        self._lock = threading.Lock()

    def run_pending(self, refunds: Iterable[Refund], now: datetime) -> Dict[str, Outcome]:
        """Worker tick: process every approved, unsubmitted refund."""
        return {r.id: self.process(r, now) for r in refunds}

    def process(self, refund: Refund, now: datetime) -> Outcome:
        payment = self.payments[refund.payment_id]
        with self._lock:  # conditional claim: prevents duplicate submission
            if refund.approval_status is not ApprovalStatus.APPROVED:
                return Outcome.SKIPPED
            if refund.status is not RefundStatus.INITIATED or refund.submitted:
                return Outcome.DUPLICATE
            try:
                payment.reserve(refund.amount)
            except InsufficientRefundableBalance:
                self._fail(refund, payment, now, "EXCEEDS_BALANCE",
                           "Amount exceeds refundable balance", release=False)
                return Outcome.FAILED
            refund.submitted = True

        try:
            self.gateway.submit_refund(
                refund.idempotency_key,
                payment.upi_transaction_ref,
                refund.amount,
                refund.upi_handle,
            )
        except GatewayUnavailable:
            with self._lock:  # stay retryable; same idempotency key on next attempt
                payment.release(refund.amount)
                refund.submitted = False
                refund.submit_attempts += 1
            return Outcome.RETRY
        except GatewayRejected as exc:
            with self._lock:
                self._fail(refund, payment, now, exc.code, failure_reason_for(exc.code), release=True)
            return Outcome.FAILED

        with self._lock:
            refund.transition(RefundStatus.PROCESSING, now, SOURCE)
        self.notifier.customer(
            refund.id, "refund_initiated", expected_within_hours=24, amount=refund.amount
        )
        return Outcome.SUBMITTED

    def handle_callback(
        self,
        refund: Refund,
        now: datetime,
        success: bool,
        provider_reference: Optional[str] = None,
        failure_code: Optional[str] = None,
    ) -> bool:
        """Gateway webhook/poller result. Signature verification happens at the HTTP edge.

        Returns False if the refund is not Processing (duplicate/late callback ignored).
        """
        payment = self.payments[refund.payment_id]
        with self._lock:
            if refund.status is not RefundStatus.PROCESSING:
                return False
            if success:
                refund.provider_reference = provider_reference
                refund.transition(
                    RefundStatus.COMPLETED, now, "upi-gateway",
                    audit=self.audit, payment_txn_ref=payment.upi_transaction_ref,
                )
            else:
                code = failure_code or "UNKNOWN"
                self._fail(refund, payment, now, code, failure_reason_for(code),
                           release=True, source="upi-gateway")
        if success:
            self.notifier.customer(
                refund.id, "refund_completed",
                amount=refund.amount, upi_reference=provider_reference,
            )
        return True

    def _fail(self, refund, payment, now, code, reason, release, source=SOURCE) -> None:
        if release:
            payment.release(refund.amount)
        refund.submitted = False
        refund.transition(
            RefundStatus.FAILED, now, source,
            audit=self.audit, payment_txn_ref=payment.upi_transaction_ref,
            failure_code=code, failure_reason=reason,
        )
        self.notifier.ops(SUPPORT_CHANNEL, "refund_failed", refund.id, reason=reason)
        self.notifier.ops(OPS_CHANNEL, "refund_failed", refund.id, reason=reason)
        self.notifier.customer(refund.id, "refund_failed", reason=reason, can_retry=True)
