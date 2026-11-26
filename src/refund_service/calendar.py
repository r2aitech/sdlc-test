"""Business-day / holiday calendar used for expected-completion dates (IST)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable


class BusinessCalendar:
    def __init__(self, holidays: Iterable[date] = ()) -> None:
        self.holidays = frozenset(holidays)

    def is_business_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self.holidays

    def add_business_days(self, start: date, days: int) -> date:
        current = start
        remaining = days
        while remaining > 0:
            current += timedelta(days=1)
            if self.is_business_day(current):
                remaining -= 1
        return current
