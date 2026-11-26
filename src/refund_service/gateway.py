"""UPI gateway client contract, errors and failure-code mapping."""
from __future__ import annotations

from decimal import Decimal
from typing import Protocol


class GatewayUnavailable(Exception):
    """Gateway timeout / outage: the refund stays retryable."""


class GatewayRejected(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class UpiGateway(Protocol):
    def submit_refund(
        self,
        idempotency_key: str,
        original_transaction_ref: str,
        amount: Decimal,
        upi_handle: str,
    ) -> None:
        """Submit a refund. Must be idempotent on idempotency_key."""


# TODO(product): the real failure-code to text mapping has not been supplied.
FAILURE_REASONS = {
    "U30": "Transaction failed at the beneficiary bank (U30)",
    "INVALID_VPA": "UPI ID is invalid",
}


def failure_reason_for(code: str) -> str:
    return FAILURE_REASONS.get(code, f"Refund failed (code {code})")


def backoff_seconds(attempt: int, base: float = 30.0, cap: float = 900.0) -> float:
    """Exponential backoff for gateway retries (same idempotency key is reused)."""
    return min(cap, base * (2 ** max(0, attempt - 1)))
