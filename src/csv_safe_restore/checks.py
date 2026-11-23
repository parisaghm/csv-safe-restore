"""Optional format checks for identifiers.

These checks only produce warnings. A valid check digit does not prove that a
barcode is the *right* barcode, so nothing here is ever used to repair a value.
"""

from __future__ import annotations

GTIN_LENGTHS = (8, 12, 13, 14)  # EAN-8, UPC-A, EAN-13, GTIN-14


def gtin_problem(value: str) -> str | None:
    """Return why ``value`` is not a valid GTIN, or None if it is valid."""
    if not value.isascii() or not value.isdigit():
        return "not all digits"
    if len(value) not in GTIN_LENGTHS:
        return f"{len(value)} digits (expected 8, 12, 13 or 14)"
    if gtin_check_digit(value[:-1]) != value[-1]:
        return "check digit does not match"
    return None


def gtin_check_digit(body: str) -> str:
    """Compute the GS1 check digit for the digits before it.

    Starting from the digit next to the check digit and moving left, digits are
    weighted 3, 1, 3, 1, ... The check digit brings the total to a multiple of 10.
    """
    total = sum(int(digit) * (3 if i % 2 == 0 else 1) for i, digit in enumerate(reversed(body)))
    return str((10 - total % 10) % 10)
