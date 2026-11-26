from datetime import timedelta
from decimal import Decimal

from refund_service.models import ApprovalStatus, RefundStatus
from refund_service.processor import Outcome


def test_full_refund_sent_to_original_transaction(env, gateway, payment, make_refund, now):
    processor, notifier, _ = env
    refund = make_refund()
    assert processor.process(refund, now) is Outcome.SUBMITTED
    key, txn_ref, amount, handle = gateway.calls[0]
    assert (txn_ref, amount) == ("TXN-1", Decimal("1499.00"))
    assert refund.status is RefundStatus.PROCESSING
    assert notifier.customer_messages[0]["event"] == "refund_initiated"
    assert notifier.customer_messages[0]["expected_within_hours"] == 24


def test_partial_refund_reduces_balance(env, gateway, payment, make_refund, now):
    processor, _, _ = env
    refund = make_refund(amount=Decimal("500.00"))
    processor.process(refund, now)
    assert gateway.calls[0][2] == Decimal("500.00")
    assert payment.refundable_balance == Decimal("999.00")


def test_unapproved_refunds_not_sent(env, gateway, make_refund, now):
    processor, _, _ = env
    for status in (ApprovalStatus.PENDING, ApprovalStatus.REJECTED):
        refund = make_refund(approval_status=status)
        assert processor.process(refund, now) is Outcome.SKIPPED
    assert gateway.calls == []


def test_amount_exceeding_balance_fails_without_gateway_call(env, gateway, make_refund, now):
    processor, notifier, _ = env
    refund = make_refund(amount=Decimal("2000.00"))
    assert processor.process(refund, now) is Outcome.FAILED
    assert gateway.calls == []
    assert refund.status is RefundStatus.FAILED
    assert refund.failure_reason == "Amount exceeds refundable balance"


def test_duplicate_processing_submits_once(env, gateway, payment, make_refund, now):
    processor, _, _ = env
    refund = make_refund()
    processor.process(refund, now)
    assert processor.process(refund, now) is Outcome.DUPLICATE
    assert len(gateway.calls) == 1
    assert payment.refundable_balance == Decimal("0.00")


def test_outage_keeps_refund_retryable_with_same_key(env, gateway, payment, make_refund, now):
    processor, _, _ = env
    refund = make_refund()
    gateway.unavailable = True
    assert processor.process(refund, now) is Outcome.RETRY
    assert refund.status is RefundStatus.INITIATED
    assert payment.refundable_balance == Decimal("1499.00")
    gateway.unavailable = False
    assert processor.process(refund, now + timedelta(minutes=1)) is Outcome.SUBMITTED
    assert gateway.calls[0][0] == gateway.calls[1][0]
    assert len(gateway.accepted) == 1


def test_gateway_confirmation_completes_and_audits(env, payment, make_refund, now):
    processor, notifier, audit = env
    refund = make_refund()
    processor.process(refund, now)
    assert processor.handle_callback(refund, now, True, provider_reference="UPI123456789012")
    assert refund.status is RefundStatus.COMPLETED
    assert refund.provider_reference == "UPI123456789012"
    assert notifier.customer_messages[-1]["event"] == "refund_completed"
    assert audit.entries[0]["original_transaction_reference"] == "TXN-1"
    # duplicate callback ignored
    assert processor.handle_callback(refund, now, True, provider_reference="X") is False


def test_gateway_failure_records_reason_alerts_and_releases_balance(env, payment, make_refund, now):
    processor, notifier, audit = env
    refund = make_refund()
    processor.process(refund, now)
    processor.handle_callback(refund, now, False, failure_code="U30")
    assert refund.status is RefundStatus.FAILED
    assert refund.failure_code == "U30" and refund.failure_reason
    assert payment.refundable_balance == Decimal("1499.00")
    assert any(a["event"] == "refund_failed" for a in notifier.ops_alerts)
    assert len(audit.entries) == 1


def test_retry_after_failure_uses_new_idempotency_key(make_refund, now):
    refund = make_refund()
    first = refund.idempotency_key
    refund.retry_round += 1
    assert refund.idempotency_key != first
