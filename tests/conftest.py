import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from refund_service.calendar import BusinessCalendar  # noqa: E402
from refund_service.gateway import GatewayRejected, GatewayUnavailable  # noqa: E402
from refund_service.models import (  # noqa: E402
    ApprovalStatus,
    AuditLog,
    Payment,
    Refund,
)
from refund_service.notifications import Notifier  # noqa: E402
from refund_service.processor import RefundProcessor  # noqa: E402


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


class FakeGateway:
    """Gateway mock with idempotency on the key."""

    def __init__(self):
        self.calls = []
        self.accepted = {}
        self.unavailable = False
        self.reject_code = None

    def submit_refund(self, idempotency_key, original_transaction_ref, amount, upi_handle):
        self.calls.append((idempotency_key, original_transaction_ref, amount, upi_handle))
        if self.unavailable:
            raise GatewayUnavailable()
        if self.reject_code:
            raise GatewayRejected(self.reject_code)
        self.accepted.setdefault(idempotency_key, amount)


@pytest.fixture
def now():
    return utc(2024, 6, 3, 4, 30)  # Mon 3 Jun 2024, 10:00 IST


@pytest.fixture
def calendar():
    return BusinessCalendar()


@pytest.fixture
def gateway():
    return FakeGateway()


@pytest.fixture
def payment():
    return Payment(id="PAY-1", upi_transaction_ref="TXN-1", refundable_balance=Decimal("1499.00"))


@pytest.fixture
def env(gateway, payment):
    notifier = Notifier()
    audit = AuditLog()
    processor = RefundProcessor(gateway, {payment.id: payment}, notifier, audit)
    return processor, notifier, audit


@pytest.fixture
def make_refund(now):
    def _make(**overrides):
        params = dict(
            id="R1",
            reference="RF1001",
            order_id="ORD-1001",
            customer_id="C1",
            payment_id="PAY-1",
            amount=Decimal("1499.00"),
            upi_handle="rahul@okbank",
            approval_status=ApprovalStatus.APPROVED,
            approved_at=now,
            customer_mobile="9876543210",
        )
        initiated = overrides.pop("initiated_at", now)
        params.update(overrides)
        return Refund.initiate(initiated, **params)

    return _make
