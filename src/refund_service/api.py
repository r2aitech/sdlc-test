"""Customer and support-agent refund endpoints (framework-agnostic handlers).

Each handler returns (http_status, body). Masking happens here, server-side, via
the shared masking utility; the full UPI ID and mobile number are never returned.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Dict, Optional, Set, Tuple

from .calendar import BusinessCalendar
from .masking import mask_mobile, mask_upi_handle
from .models import IST, Refund, RefundStatus
from .sla import classify, sla_deadline, time_remaining

EXPECTED_BUSINESS_DAYS = 5
UPI_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9]{1,63}$")
REFUND_VIEW_PERMISSION = "Refund View"

Response = Tuple[int, dict]


@dataclass
class User:
    id: str
    permissions: Set[str] = field(default_factory=set)


def fmt_date(value: datetime) -> str:
    local = value.astimezone(IST)
    return f"{local.day} {local:%b %Y}"


def fmt_amount(amount: Decimal) -> str:
    return f"\u20b9{amount:,.2f}"


def expected_completion_date(refund: Refund, calendar: BusinessCalendar) -> Optional[date]:
    # NOTE: KAN-4 (24h) vs KAN-5 (5 business days) conflict; KAN-5 rule used here.
    if refund.status not in (RefundStatus.INITIATED, RefundStatus.PROCESSING):
        return None
    start = refund.initiated_at.astimezone(IST).date()
    return calendar.add_business_days(start, EXPECTED_BUSINESS_DAYS)


def is_valid_upi_id(value: str) -> bool:
    return bool(UPI_ID_PATTERN.match(value or ""))


def customer_get_refund(
    refund: Optional[Refund], user: Optional[User], calendar: BusinessCalendar
) -> Response:
    if user is None:
        return 302, {"redirect": "/login"}  # no refund data for unauthenticated users
    if refund is None or refund.customer_id != user.id:
        return 403, {"error": "Access denied"}
    body = {
        "status": refund.status.value,
        "amount": fmt_amount(refund.amount),
        "upiIdMasked": mask_upi_handle(refund.upi_handle),
        "initiatedDate": fmt_date(refund.initiated_at),
    }
    expected = expected_completion_date(refund, calendar)
    if expected is not None and refund.status is RefundStatus.PROCESSING:
        body["expectedCompletionDate"] = f"{expected.day} {expected:%b %Y}"
    if refund.status is RefundStatus.COMPLETED:
        body["creditedDate"] = fmt_date(refund.completed_at)
        body["bankReferenceNumber"] = refund.provider_reference
    if refund.status is RefundStatus.FAILED:
        body["failureReason"] = refund.failure_reason
        body["retryRefundOption"] = True
    return 200, body


def customer_retry_refund(
    refund: Optional[Refund],
    user: Optional[User],
    new_upi_id: str,
    now: datetime,
    calendar: BusinessCalendar,
) -> Response:
    if user is None:
        return 302, {"redirect": "/login"}
    if refund is None or refund.customer_id != user.id:
        return 403, {"error": "Access denied"}
    if refund.status is not RefundStatus.FAILED:
        return 409, {"error": "Only failed refunds can be retried"}
    if not is_valid_upi_id(new_upi_id):
        return 422, {"error": "Enter a valid UPI ID", "status": refund.status.value}
    # Policy note: refunding to a different handle needs fraud/compliance review.
    refund.upi_handle = new_upi_id
    refund.retry_round += 1
    refund.submitted = False
    refund.transition(RefundStatus.INITIATED, now, "customer-retry")
    return customer_get_refund(refund, user, calendar)


def agent_get_refund(
    refund: Optional[Refund], user: Optional[User], calendar: BusinessCalendar, now: datetime
) -> Response:
    if user is None or REFUND_VIEW_PERMISSION not in user.permissions:
        return 403, {"error": "Forbidden"}
    if refund is None:
        return 404, {"message": "No refund found for this reference"}
    expected = expected_completion_date(refund, calendar)
    today = now.astimezone(IST).date()
    overdue = expected is not None and expected < today
    body = {
        "id": refund.id,
        "reference": refund.reference,
        "amount": fmt_amount(refund.amount),
        "status": refund.status.value,
        "initiatedDate": fmt_date(refund.initiated_at),
        "lastUpdated": refund.updated_at.astimezone(IST).isoformat(),
        "destination": {"upiHandleMasked": mask_upi_handle(refund.upi_handle)},
        "customer": {
            "mobileMasked": mask_mobile(refund.customer_mobile) if refund.customer_mobile else None
        },
        "history": [
            {
                "status": h.status.value,
                "timestamp": h.at.astimezone(IST).isoformat(),
                "source": h.source,
            }
            for h in sorted(refund.history, key=lambda h: h.at)
        ],
        "overdue": overdue,
    }
    if expected is not None:
        body["expectedCompletionDate"] = expected.isoformat()
    if overdue:
        body["daysOverdue"] = (today - expected).days
    if refund.status is RefundStatus.COMPLETED:
        body["completedAt"] = refund.completed_at.astimezone(IST).isoformat()
    if refund.status is RefundStatus.FAILED:
        body["failureReason"] = refund.failure_reason
        body["failedAt"] = refund.updated_at.astimezone(IST).isoformat()
    if refund.approved_at is not None:
        body["sla"] = {
            "approvedAt": refund.approved_at.astimezone(IST).isoformat(),
            "deadline": sla_deadline(refund).astimezone(IST).isoformat(),
            "timeRemainingSeconds": int(time_remaining(refund, now).total_seconds()),
            "classification": classify(refund, now).value,
        }
    return 200, body


def agent_lookup_by_reference(
    refunds: Dict[str, Refund], reference: str, user: Optional[User],
    calendar: BusinessCalendar, now: datetime,
) -> Response:
    return agent_get_refund(refunds.get(reference), user, calendar, now)
