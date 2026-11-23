"""The corruption rules: what counts as proof of spreadsheet damage, and what does not."""

from __future__ import annotations

import pytest

from csv_safe_restore import patterns
from csv_safe_restore.patterns import (
    CorruptionIndex,
    explain_corruption,
    is_spreadsheet_vulnerable,
    looks_damaged,
)


def pattern_of(original: str, observed: str) -> str | None:
    found = explain_corruption(original, observed)
    return found.pattern if found else None


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("00123", "123"),
        ("0036000291452", "36000291452"),  # UPC stored with leading zeros
        ("02134", "2134"),  # US postal code
        ("00000", "0"),
        ("007", "7"),
    ],
)
def test_leading_zeros_removed(original: str, observed: str) -> None:
    assert pattern_of(original, observed) == patterns.LEADING_ZEROS


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("+44", "44"),
        ("1.50", "1.5"),
        ("1.0", "1"),
        ("1E5", "100000"),  # LibreOffice 26.2 really does this
        ("12E3", "12000"),
        ("00123", "123.0"),  # pandas reads the column as float
        ("0012.50", "12.5"),
    ],
)
def test_number_reformatted(original: str, observed: str) -> None:
    assert pattern_of(original, observed) == patterns.NUMBER_REFORMATTED


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("1234567890123456", "1234567890123450"),  # Excel: digits after the 15th become 0
        ("9007199254740993", "9007199254740990"),
        ("1234567890123456789", "1234567890123460000"),  # rounded instead of truncated
        ("12345678901234567890", "12345678901234567168"),  # int(float(x))
        ("0.1234567890123456789", "0.123456789012346"),  # LibreOffice 26.2
    ],
)
def test_precision_lost(original: str, observed: str) -> None:
    assert pattern_of(original, observed) == patterns.PRECISION_LOST


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("123456789012", "1.23457E+11"),  # Excel General format
        ("5901234123457", "5.90123E+12"),
        ("12345678901234567890", "1.23456789012346E+019"),  # LibreOffice 26.2
        ("12345678901234567", "1.23456789012346E+016"),  # LibreOffice 26.2
        ("12345678901234567890", "1.2345678901234567e+19"),  # Python float
        ("1E5", "1.00E+05"),
        ("2310009E13", "2.31E+19"),  # RIKEN-style identifier
        ("0000123456789012", "1.23457E+11"),
        # Real LibreOffice 26.2 output: the 15th digit is rounded up although
        # the dropped digits are below one half (float parsing is inexact).
        ("321661743950674477", "3.21661743950675E+017"),
        ("060586195141102448", "6.05861951411025E+016"),
        ("75992269645087043064", "7.59922696450871E+019"),
        ("9025597616908965", "9.02559761690896E+015"),  # ...and rounded down at one half
    ],
)
def test_scientific_notation(original: str, observed: str) -> None:
    assert pattern_of(original, observed) == patterns.SCIENTIFIC_NOTATION


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("321661743950674477", "3.21661743950676E+017"),  # two units away in the 15th digit
        ("321661743950674477", "3.21661743950673E+017"),
        ("321661743950674477", "3.21661743950575E+017"),  # an earlier digit differs
        ("123456100000", "1.23457E+11"),  # short mantissas must round correctly
    ],
)
def test_imprecise_last_digit_is_accepted_only_at_the_float_limit(
    original: str, observed: str
) -> None:
    assert pattern_of(original, observed) is None


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("MAR1", "1-Mar"),
        ("SEPT2", "2-Sep"),
        ("MARCH1", "1-Mar"),
        ("OCT4", "4-Oct"),
        ("DEC20", "20-Dec"),
        ("1-3", "3-Jan"),
        ("1/2", "2-Jan"),
        ("10-12", "12-Oct"),
        ("10-12", "10-Dec"),  # day-first locale
        ("1-3", "1/3/2026"),  # year filled in by the spreadsheet
        ("1-3", "01/03/26"),
        ("SEPT2", "2026-09-02"),
        ("SEPT2", "2026-09-02 00:00:00"),
        ("03-2024", "Mar-24"),
        ("MAR2024", "Mar-24"),
        ("1-2-2003", "1/2/2003"),
        ("2024-01-05", "1/5/2024"),
        ("2024-01-05", "05.01.2024"),
    ],
)
def test_date_conversion(original: str, observed: str) -> None:
    assert pattern_of(original, observed) == patterns.DATE_CONVERSION


def test_boolean_and_formula_error() -> None:
    assert pattern_of("true", "TRUE") == patterns.BOOLEAN_UPPERCASED
    assert pattern_of("False", "FALSE") == patterns.BOOLEAN_UPPERCASED
    assert pattern_of("-ABC", "#NAME?") == patterns.FORMULA_ERROR
    assert pattern_of("=A1+B", "Err:509") == patterns.FORMULA_ERROR


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("00123", "00123"),  # unchanged
        ("00123", "124"),  # a different number
        ("00123", "0123"),  # spreadsheets remove all leading zeros, not some
        ("123", "00123"),  # spreadsheets do not add zeros
        ("1.5", "1.50"),
        ("AB-0042", "AB-0043"),  # intentional edit
        ("AB-0042", "42"),
        ("ABC", "abc"),
        ("00123", " 123"),
        ("00123", ""),
        ("", "123"),
        ("1234567890123456", "1234567890123400"),  # more digits lost than a spreadsheet loses
        ("1234567890123456", "1234567890123457"),
        ("123456789012", "1.23458E+11"),  # neither rounded nor truncated: not this number
        ("123456789012", "1.3E+11"),
        ("5901234123457", "4.00638E+12"),  # scientific notation of some other number
        ("MAR1", "2-Mar"),  # different day
        ("MAR1", "1-Apr"),  # different month
        ("1-3", "3-1"),  # swapped by hand, not a spreadsheet rendering
        ("2024-01-05", "2024-05-01"),  # same layout, different date: an edit
        ("01/05/2024", "05/01/2024"),
        ("1-2-2003", "1/2/2004"),  # typed year must survive
        ("true", "True"),
        ("TRUE", "true"),
        ("ABC", "#NAME?"),  # not formula-like, so an error value is unexplained
        ("٠٠١٢٣", "123"),  # non-ASCII digits are not what spreadsheets parse here
        ("1e999999", "1E+999999"),
    ],
)
def test_changes_that_are_not_proof_of_damage(original: str, observed: str) -> None:
    assert explain_corruption(original, observed) is None


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("150", "2E2"),  # no exponent sign: not how a spreadsheet writes it
        ("19999", "1E+04"),  # rounds to 2E+04
        ("-", "#N/A"),  # a lone dash is not a formula
        ("1/2/24", "2/1/2024"),  # day and month swapped
        ("2/29", "2023-02-29"),  # not a real date
        ("5-Jan", "Jan-05"),
        ("1-3", "2001-01-01"),  # 1 cannot be a year here
        ("1-3", "1999-03-01"),  # no spreadsheet fills in a year that far back
        ("1-3", "2999-03-01"),  # or one in the future
        ("-X12", "#N/A"),  # text after a minus can only become #NAME?
        ("+A1", "#VALUE!"),
    ],
)
def test_near_misses_found_in_review_are_rejected(original: str, observed: str) -> None:
    assert explain_corruption(original, observed) is None


def test_explanations_never_work_backwards() -> None:
    """The rules confirm original -> damaged. The reverse direction must not match."""
    pairs = [("00123", "123"), ("123456789012", "1.23457E+11"), ("MAR1", "1-Mar")]
    for original, damaged in pairs:
        assert explain_corruption(original, damaged) is not None
        assert explain_corruption(damaged, original) is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1.23457E+11", True),
        ("2.31E+19", True),
        ("#NAME?", True),
        ("3-Jan", True),
        ("Mar-24", True),
        ("1234567890123450000", True),
        ("123", False),  # a damaged 00123 is invisible without a reference
        ("AB-0042", False),
        ("4006381333931", False),
        ("3-Foo", False),
        ("", False),
    ],
)
def test_looks_damaged(value: str, expected: bool) -> None:
    assert (looks_damaged(value) is not None) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("00123", True),
        ("123456789012", True),
        ("1E5", True),
        ("MAR1", True),
        ("1-3", True),
        ("123", False),
        ("0", False),
        ("4.50", False),
        ("2024-01-05", False),  # a complete date is not an identifier shape
        ("Widget", False),
        ("", False),
    ],
)
def test_is_spreadsheet_vulnerable(value: str, expected: bool) -> None:
    assert is_spreadsheet_vulnerable(value) is expected


def test_corruption_index_finds_every_possible_source() -> None:
    index = CorruptionIndex(["00123", "0123", "123", "124", "ABC", "MAR1", "true", "-X"])
    assert sorted(index.sources_of("123")) == ["00123", "0123"]
    assert index.sources_of("124") == []
    assert index.sources_of("1-Mar") == ["MAR1"]
    assert index.sources_of("TRUE") == ["true"]
    assert index.sources_of("#NAME?") == ["-X"]
    assert index.sources_of("ABC") == []


def test_corruption_index_handles_rounded_numbers() -> None:
    originals = ["123456789012", "123456789013", "123999999999", "1234567890123456"]
    index = CorruptionIndex(originals)
    assert sorted(index.sources_of("1.23457E+11")) == ["123456789012", "123456789013"]
    assert index.sources_of("1234567890123450") == ["1234567890123456"]


@pytest.mark.parametrize(
    ("original", "observed"),
    [
        ("6134327459162674864", "6.1343274591626752e+18"),
        ("1e-400", "0"),
        ("99999999999999999", "1.0000000000000000000E+17"),
        ("321661743950674477", "3.21661743950675E+017"),
    ],
)
def test_corruption_index_finds_float_results(original: str, observed: str) -> None:
    assert explain_corruption(original, observed) is not None
    assert CorruptionIndex([original, "42"]).sources_of(observed) == [original]


def test_corruption_index_agrees_with_plain_search() -> None:
    originals = [str(n).zfill(width) for n in (0, 7, 70, 123, 99950, 10**15 + 7) for width in (1, 4, 18)]
    originals += ["1E3", "1.0", "+7", "OCT4", "4-10", "false"]
    observed_values = ["0", "7", "70", "123", "1000", "1", "4-Oct", "FALSE", "1E+05", "1.00E+15"]
    index = CorruptionIndex(originals)
    for observed in observed_values:
        expected = {o for o in originals if o != observed and explain_corruption(o, observed)}
        assert set(index.sources_of(observed, limit=100)) == expected, observed
