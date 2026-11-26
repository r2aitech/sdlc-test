from datetime import timedelta

from refund_service.models import RefundStatus
from refund_service.notifications import (
    ENGINEERING_CHANNEL, ESCALATION_CHANNEL, OPS_CHANNEL, OPS_LEAD_CHANNEL, Notifier,
)
from refund_service.sla import SlaClass, SlaMonitor, classify, sla_report


def events(notifier):
    return [(a["channel"], a["event"]) for a in notifier.ops_alerts]


def test_no_flag_before_20_hours(make_refund, now):
    refund = make_refund()
    monitor = SlaMonitor(Notifier())
    monitor.run([refund], now + timedelta(hours=19, minutes=45))
    assert refund.sla_state is None


def test_at_risk_at_20h_alerts_once(make_refund, now):
    refund = make_refund()
    notifier = Notifier()
    monitor = SlaMonitor(notifier)
    monitor.run([refund], now + timedelta(hours=20))
    monitor.run([refund], now + timedelta(hours=20, minutes=15))
    assert refund.sla_state == SlaClass.AT_RISK.value
    assert events(notifier).count((OPS_CHANNEL, "refund_at_risk")) == 1
    alert = notifier.ops_alerts[0]
    assert alert["refund_id"] == "R1" and alert["time_remaining"] == timedelta(hours=4)


def test_unacknowledged_at_risk_escalates_after_30_minutes(make_refund, now):
    refund = make_refund()
    notifier = Notifier()
    monitor = SlaMonitor(notifier)
    monitor.run([refund], now + timedelta(hours=20))
    monitor.run([refund], now + timedelta(hours=20, minutes=30))
    monitor.run([refund], now + timedelta(hours=20, minutes=45))
    assert events(notifier).count((OPS_LEAD_CHANNEL, "at_risk_escalated")) == 1


def test_acknowledged_at_risk_is_not_escalated(make_refund, now):
    refund = make_refund()
    notifier = Notifier()
    monitor = SlaMonitor(notifier)
    monitor.run([refund], now + timedelta(hours=20))
    monitor.acknowledge(refund)
    monitor.run([refund], now + timedelta(hours=21))
    assert (OPS_LEAD_CHANNEL, "at_risk_escalated") not in events(notifier)


def test_breach_at_24h_alerts_and_notifies_customer(make_refund, now):
    refund = make_refund()
    notifier = Notifier()
    SlaMonitor(notifier).run([refund], now + timedelta(hours=24))
    assert refund.sla_state == SlaClass.BREACHED.value
    assert (OPS_LEAD_CHANNEL, "refund_breached") in events(notifier)
    assert (ESCALATION_CHANNEL, "refund_breached") in events(notifier)
    msg = notifier.customer_messages[0]
    assert msg["event"] == "refund_delayed" and msg["next_update_within_hours"] == 24


def test_breached_realerts_every_24h(make_refund, now):
    refund = make_refund()
    notifier = Notifier()
    monitor = SlaMonitor(notifier)
    monitor.run([refund], now + timedelta(hours=24))
    monitor.run([refund], now + timedelta(hours=47))
    monitor.run([refund], now + timedelta(hours=48))
    assert events(notifier).count((OPS_LEAD_CHANNEL, "refund_breached_realert")) == 1


def test_missed_run_catches_up_from_approval_time(make_refund, now):
    refund = make_refund()
    notifier = Notifier()
    SlaMonitor(notifier).run([refund], now + timedelta(hours=30))  # first run after a long gap
    assert refund.sla_state == SlaClass.BREACHED.value


def test_completion_after_breach_closes_alerts_but_stays_breached(make_refund, now):
    refund = make_refund()
    monitor = SlaMonitor(Notifier())
    monitor.run([refund], now + timedelta(hours=25))
    done = now + timedelta(hours=26)
    refund.transition(RefundStatus.PROCESSING, done, "x")
    refund.transition(RefundStatus.COMPLETED, done, "x")
    monitor.run([refund], done)
    assert refund.sla_alerts_open is False
    assert classify(refund, done) is SlaClass.BREACHED
    assert sla_report([refund], done) == {"Within SLA": 0, "Breached SLA": 1}


def test_completed_within_sla_counted_within(make_refund, now):
    refund = make_refund()
    done = now + timedelta(minutes=40)
    refund.transition(RefundStatus.PROCESSING, done, "x")
    refund.transition(RefundStatus.COMPLETED, done, "x")
    assert sla_report([refund], now + timedelta(days=3)) == {"Within SLA": 1, "Breached SLA": 0}


def test_failed_refund_clock_continues_from_approval(make_refund, now):
    refund = make_refund()
    refund.transition(RefundStatus.FAILED, now + timedelta(hours=1), "x",
                      failure_code="U30", failure_reason="bad")
    assert classify(refund, now + timedelta(hours=21)) is SlaClass.AT_RISK


def test_heartbeat_alerts_engineering_after_30_minutes(now):
    notifier = Notifier()
    monitor = SlaMonitor(notifier)
    monitor.run([], now)
    assert monitor.check_heartbeat(now + timedelta(minutes=29)) is False
    assert monitor.check_heartbeat(now + timedelta(minutes=30)) is True
    assert notifier.ops_alerts[-1]["channel"] == ENGINEERING_CHANNEL
