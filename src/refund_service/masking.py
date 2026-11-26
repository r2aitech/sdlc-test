"""Shared server-side masking utility for UPI handles and mobile numbers.

Rule (KAN-5, length based): keep the first two characters of the handle's local
part and mask the rest; local parts of two characters or fewer are fully masked.
The domain part is never masked.
"""


def mask_upi_handle(handle: str) -> str:
    local, sep, domain = handle.partition("@")
    if not sep or not local or not domain:
        raise ValueError("Invalid UPI handle")
    if len(local) <= 2:
        masked = "*" * len(local)
    else:
        masked = local[:2] + "*" * (len(local) - 2)
    return f"{masked}@{domain}"


def mask_mobile(mobile: str) -> str:
    digits = "".join(ch for ch in mobile if ch.isdigit())
    if len(digits) <= 4:
        return "X" * len(digits)
    return "X" * (len(digits) - 4) + digits[-4:]
