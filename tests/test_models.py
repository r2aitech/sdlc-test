import pytest

from refund_service.models import AuditLog, InvalidTransition, RefundStatus


def test_valid_lifecycle_records_history(make_refund, now):
    refund = make_refund()
    refund.transition(RefundStatus.PROCESSING, now, "refund-processor")
    refund.transition(RefundStatus.COMPLETED, now, "upi-gateway")
    assert [h.status for h in refund.history] == [
        RefundStatus.INITIATED, RefundStatus.PROCESSING, RefundStatus.COMPLETED,
    ]
    assert refund.completed_at == now


@pytest.mark.parametrize("new", [RefundStatus.PROCESSING, RefundStatus.INITIATED])
def test_completed_is_terminal(make_refund, now, new):
    refund = make_refund()
    refund.transition(RefundStatus.PROCESSING, now, "x")
    refund.transition(RefundStatus.COMPLETED, now, "x")
    with pytest.raises(InvalidTransition):
        refund.transition(new, now, "x")


def test_failed_can_return_to_initiated_and_clears_failure(make_refund, now):
    refund = make_refund()
    refund.transition(RefundStatus.FAILED, now, "x", failure_code="U30", failure_reason="bad")
    refund.transition(RefundStatus.INITIATED, now, "customer-retry")
    assert refund.failure_reason is None


def test_final_state_writes_audit(make_refund, now):
    audit = AuditLog()
    refund = make_refund()
    refund.transition(RefundStatus.FAILED, now, "x", audit=audit, payment_txn_ref="TXN-1",
                      failure_code="U30", failure_reason="bad")
    entry = audit.entries[0]
    assert entry["original_transaction_reference"] == "TXN-1"
    assert entry["status"] is RefundStatus.FAILED
    assert entry["amount"] == refund.amount
