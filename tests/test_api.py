from datetime import timedelta

from refund_service.api import (
    User, agent_get_refund, agent_lookup_by_reference, customer_get_refund, customer_retry_refund,
)
from refund_service.models import RefundStatus

AGENT = User("A1", {"Refund View"})
CUSTOMER = User("C1")


def test_customer_sees_initiated_refund(make_refund, calendar):
    status, body = customer_get_refund(make_refund(), CUSTOMER, calendar)
    assert status == 200
    assert body["status"] == "Initiated"
    assert body["amount"] == "\u20b91,499.00"
    assert body["initiatedDate"] == "3 Jun 2024"
    assert "retryRefundOption" not in body


def test_full_upi_id_never_returned(make_refund, calendar):
    _, body = customer_get_refund(make_refund(), CUSTOMER, calendar)
    assert "rahul@okbank" not in str(body)
    assert body["upiIdMasked"].endswith("@okbank")


def test_processing_shows_expected_date_and_no_retry(make_refund, calendar, now):
    refund = make_refund()
    refund.transition(RefundStatus.PROCESSING, now, "x")
    _, body = customer_get_refund(refund, CUSTOMER, calendar)
    assert body["expectedCompletionDate"] == "10 Jun 2024"
    assert "retryRefundOption" not in body


def test_completed_shows_credit_date_and_bank_reference(make_refund, calendar, now):
    refund = make_refund()
    refund.transition(RefundStatus.PROCESSING, now, "x")
    refund.provider_reference = "UPI123456789012"
    refund.transition(RefundStatus.COMPLETED, now + timedelta(days=1), "x")
    _, body = customer_get_refund(refund, CUSTOMER, calendar)
    assert body["creditedDate"] == "4 Jun 2024"
    assert body["bankReferenceNumber"] == "UPI123456789012"


def test_failed_shows_reason_and_retry(make_refund, calendar, now):
    refund = make_refund()
    refund.transition(RefundStatus.FAILED, now, "x", failure_code="INVALID_VPA",
                      failure_reason="UPI ID is invalid")
    _, body = customer_get_refund(refund, CUSTOMER, calendar)
    assert body["failureReason"] == "UPI ID is invalid"
    assert body["retryRefundOption"] is True


def test_unauthenticated_redirected_without_data(make_refund, calendar):
    status, body = customer_get_refund(make_refund(), None, calendar)
    assert status == 302 and body == {"redirect": "/login"}


def test_other_customers_refund_denied(make_refund, calendar):
    status, body = customer_get_refund(make_refund(customer_id="C2"), CUSTOMER, calendar)
    assert status == 403 and body == {"error": "Access denied"}


def failed(make_refund, now):
    refund = make_refund()
    refund.transition(RefundStatus.FAILED, now, "x", failure_code="INVALID_VPA",
                      failure_reason="UPI ID is invalid")
    return refund


def test_retry_with_valid_upi_id(make_refund, calendar, now):
    refund = failed(make_refund, now)
    status, body = customer_retry_refund(refund, CUSTOMER, "rahul@newbank", now, calendar)
    assert status == 200 and body["status"] == "Initiated"
    assert body["upiIdMasked"].endswith("@newbank")
    assert refund.submitted is False


def test_retry_with_invalid_upi_id_rejected(make_refund, calendar, now):
    refund = failed(make_refund, now)
    status, body = customer_retry_refund(refund, CUSTOMER, "rahul@@", now, calendar)
    assert status == 422 and body["error"] == "Enter a valid UPI ID"
    assert refund.status is RefundStatus.FAILED


def test_retry_only_for_failed(make_refund, calendar, now):
    status, _ = customer_retry_refund(make_refund(), CUSTOMER, "rahul@newbank", now, calendar)
    assert status == 409


def test_agent_detail_masks_and_includes_history(make_refund, calendar, now):
    refund = make_refund()
    refund.transition(RefundStatus.PROCESSING, now, "refund-processor")
    status, body = agent_get_refund(refund, AGENT, calendar, now)
    assert status == 200
    assert body["customer"]["mobileMasked"] == "XXXXXX3210"
    assert "9876543210" not in str(body) and "rahul@okbank" not in str(body)
    assert [h["status"] for h in body["history"]] == ["Initiated", "Processing"]
    assert body["history"][1]["source"] == "refund-processor"
    assert body["sla"]["classification"] == "OK"


def test_agent_expected_date_five_business_days(make_refund, calendar, now):
    _, body = agent_get_refund(make_refund(), AGENT, calendar, now)
    assert body["expectedCompletionDate"] == "2024-06-10"


def test_closed_refund_has_no_expected_date(make_refund, calendar, now):
    status, body = agent_get_refund(failed(make_refund, now), AGENT, calendar, now)
    assert "expectedCompletionDate" not in body and "failedAt" in body
    assert body["failureReason"] == "UPI ID is invalid"


def test_overdue_flag_and_days(make_refund, calendar, now):
    later = now + timedelta(days=9)  # 12 Jun IST; expected 10 Jun
    _, body = agent_get_refund(make_refund(), AGENT, calendar, later)
    assert body["overdue"] is True and body["daysOverdue"] == 2


def test_due_today_is_not_overdue(make_refund, calendar, now):
    _, body = agent_get_refund(make_refund(), AGENT, calendar, now + timedelta(days=7))
    assert body["overdue"] is False


def test_agent_without_permission_gets_403(make_refund, calendar, now):
    status, body = agent_get_refund(make_refund(), User("A2"), calendar, now)
    assert status == 403 and "amount" not in body


def test_lookup_no_match(calendar, now):
    status, body = agent_lookup_by_reference({}, "RF9999", AGENT, calendar, now)
    assert status == 404 and body == {"message": "No refund found for this reference"}
