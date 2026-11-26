from datetime import date

from refund_service.calendar import BusinessCalendar


def test_five_business_days_from_monday_is_next_monday():
    assert BusinessCalendar().add_business_days(date(2024, 6, 3), 5) == date(2024, 6, 10)


def test_weekends_skipped():
    assert BusinessCalendar().add_business_days(date(2024, 6, 7), 1) == date(2024, 6, 10)


def test_holidays_skipped():
    cal = BusinessCalendar(holidays=[date(2024, 6, 5)])
    assert cal.add_business_days(date(2024, 6, 3), 5) == date(2024, 6, 11)
