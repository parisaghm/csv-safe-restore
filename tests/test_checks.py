from __future__ import annotations

import pytest

from csv_safe_restore.checks import gtin_check_digit, gtin_problem


@pytest.mark.parametrize(
    "value",
    ["036000291452", "4006381333931", "5901234123457", "96385074", "00012345600012", "0036000291452"],
)
def test_valid_gtins(value: str) -> None:
    assert gtin_problem(value) is None


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("4006381333932", "check digit"),
        ("36000291452", "11 digits"),  # leading zero lost
        ("4.00638E+12", "not all digits"),
        ("ABC", "not all digits"),
        ("", "not all digits"),
    ],
)
def test_invalid_gtins(value: str, reason: str) -> None:
    assert reason in gtin_problem(value)


def test_check_digit() -> None:
    assert gtin_check_digit("400638133393") == "1"
    assert gtin_check_digit("03600029145") == "2"
