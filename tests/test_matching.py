"""Key trust: cases found in review where a weak key guard caused wrong repairs.

Every test here is a pair of files in which restoring by key would write a
value into the wrong record. The correct behaviour is to repair nothing.
"""

from __future__ import annotations

import time

import pytest

from csv_safe_restore import detection, matching
from csv_safe_restore.matching import collision_forms
from csv_safe_restore.repair import repaired_rows

from conftest import analyze_trusting_key as analyze
from conftest import make_table, table_from_text


def statuses(analysis: detection.Analysis) -> list[str]:
    return [row.status for row in analysis.matches.rows]


def test_composite_key_with_many_possible_sources_is_ambiguous() -> None:
    """Ten account numbers all become ...450; only one of them has type A."""
    accounts = [f"123456789012345{digit}" for digit in range(10)]
    reference = make_table(
        ["acct", "type", "sku"],
        [[acct, "B" if acct.endswith("7") else "A", f"00{i}"] for i, acct in enumerate(accounts)]
        + [[accounts[7], "A", "0777"]],
    )
    # The row that was (…457, A) lost its last digit and now looks like (…450, A).
    edited = make_table(["acct", "type", "sku"], [["1234567890123450", "A", "777"]])
    analysis = analyze(reference, edited, ("acct", "type"), ("sku",))
    assert statuses(analysis) == [matching.AMBIGUOUS_KEY]
    assert analysis.repairs == []
    assert "and" in analysis.matches.rows[0].detail and "more" in analysis.matches.rows[0].detail


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("1 000", "1000"),
        ("$5", "5"),
        ("50%", "0.5"),
        (" 7", "7"),
        ("7 ", "7"),
        ("1'000", "1000"),
    ],
)
def test_keys_that_could_end_up_looking_the_same_are_not_trusted(first: str, second: str) -> None:
    reference = make_table(["id", "sku"], [[first, "00123"], [second, "0123"]])
    edited = make_table(["id", "sku"], [[second, "123"]])
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert statuses(analysis) == [matching.AMBIGUOUS_KEY]
    assert repaired_rows(analysis) == [[second, "123"]]


def test_thousands_separator_and_decimal_comma_keys() -> None:
    reference = make_table(["id", "sku"], [["1,000", "00123"], ["1000", "0123"]])
    edited = make_table(["id", "sku"], [["1000", "123"]])
    assert statuses(analyze(reference, edited, ("id",), ("sku",))) == [matching.AMBIGUOUS_KEY]

    reference = make_table(["id", "sku"], [["1,50", "00123"], ["1,5", "0123"]])
    edited = make_table(["id", "sku"], [["1,5", "123"]])
    assert statuses(analyze(reference, edited, ("id",), ("sku",))) == [matching.AMBIGUOUS_KEY]


def test_collision_forms_keep_ordinary_keys_apart() -> None:
    for first, second in [("A1", "a1"), ("AB-1", "AB-2"), ("10", "100"), ("P1", "P01"), ("", "0")]:
        assert not collision_forms(first) & collision_forms(second), (first, second)


def test_key_far_above_float_precision_finds_its_source() -> None:
    reference = make_table(
        ["id", "sku"], [["6134327459162674864", "00123"], ["6134327459162675000", "0123"]]
    )
    edited = make_table(["id", "sku"], [["6.1343274591626752e+18", "123"]])
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert statuses(analysis) == [matching.SUSPECTED_KEY_DAMAGE]
    assert analysis.repairs == []


def test_rows_with_swapped_or_renumbered_keys_are_not_repaired() -> None:
    reference = table_from_text("id,name,sku\n1,apple,00123\n2,pear,0123\n3,fig,000123\n")
    edited = table_from_text("id,name,sku\n1,pear,123\n2,fig,123\n")
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert statuses(analysis) == [matching.KEY_REASSIGNED, matching.KEY_REASSIGNED]
    assert repaired_rows(analysis) == edited.rows
    assert "reference line 3" in analysis.matches.rows[0].detail
    assert analysis.review_count >= 2


def test_ordinary_edits_do_not_trigger_the_reassignment_check() -> None:
    reference = table_from_text("id,name,sku\n1,apple,00123\n2,pear,0123\n")
    # Renamed to something new, and identifier damaged: still a normal repair.
    edited = table_from_text("id,name,sku\n1,green apple,123\n2,pear,123\n")
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert statuses(analysis) == [matching.MATCHED, matching.MATCHED]
    assert len(analysis.repairs) == 2
    # Same content as another record but identifiers untouched: nothing to distrust.
    edited = table_from_text("id,name,sku\n1,pear,00123\n2,pear,0123\n")
    assert statuses(analyze(reference, edited, ("id",), ("sku",))) == [matching.MATCHED] * 2
    # Looking like a record whose identifier could not have become this value
    # is no reason for doubt: 0456 never turns into 123.
    reference = table_from_text("id,name,sku\n1,apple,00123\n3,pear,0456\n")
    edited = table_from_text("id,name,sku\n1,pear,123\n")
    assert statuses(analyze(reference, edited, ("id",), ("sku",))) == [matching.MATCHED]


def test_renumbered_rows_are_caught_even_when_other_columns_were_reformatted() -> None:
    """A real LibreOffice round trip, then one row deleted and the ids renumbered."""
    reference = table_from_text(
        "id,account,name,price\n"
        "1,1234567890123456781,Alpha,9.90\n"
        "2,1234567890123456782,Beta,19.50\n"
        "3,1234567890123456783,Gamma,29.90\n"
        "4,1234567890123456784,Delta,39.10\n"
    )
    edited = table_from_text(
        "id,account,name,price\n"
        "1,1.23456789012346E+018,Alpha,9.9\n"
        "2,1.23456789012346E+018,Gamma,29.9\n"
        "3,1.23456789012346E+018,Delta,39.1\n"
    )
    analysis = analyze(reference, edited, ("id",), ("account",))
    assert statuses(analysis) == [matching.MATCHED, matching.KEY_REASSIGNED, matching.KEY_REASSIGNED]
    assert [f.original for f in analysis.repairs] == ["1234567890123456781"]


def test_shifted_rows_with_shared_or_edited_other_columns_are_caught() -> None:
    reference = table_from_text(
        "id,account,color\n1,1234567890123451,red\n2,1234567890123452,blue\n"
        "3,1234567890123453,red\n4,1234567890123454,blue\n5,1234567890123455,green\n"
    )
    edited = table_from_text(
        "id,account,color\n1,1234567890123450,red\n2,1234567890123450,red\n"
        "3,1234567890123450,blue\n4,1234567890123450,green\n"
    )
    analysis = analyze(reference, edited, ("id",), ("account",))
    assert statuses(analysis) == [matching.MATCHED] + [matching.KEY_REASSIGNED] * 3

    # The shifted row also has a hand edit (stock 7 -> 8): it still fits the
    # other record better.
    reference = table_from_text(
        "id,sku,name,stock\n1,00123,apple,5\n2,0123,pear,7\n3,000123,fig,9\n"
    )
    edited = table_from_text("id,sku,name,stock\n1,123,pear,8\n")
    assert statuses(analyze(reference, edited, ("id",), ("sku",))) == [matching.KEY_REASSIGNED]


def test_a_second_identifier_column_that_contradicts_the_match_blocks_the_repair() -> None:
    reference = table_from_text("id,sku,ean\n1,00111,4006381333931\n2,0111,5901234123457\n")
    edited = table_from_text("id,sku,ean\n2,111,4006381333931\n1,111,5901234123457\n")
    analysis = analyze(reference, edited, ("id",), ("sku", "ean"))
    assert statuses(analysis) == [matching.KEY_REASSIGNED, matching.KEY_REASSIGNED]
    assert analysis.repairs == []


def test_an_edited_row_with_a_value_of_unclear_origin_is_still_repaired() -> None:
    """A price edit alone must not block a repair: no other record fits better."""
    reference = table_from_text("id,sku,price\n1,00123,4.50\n2,0123,6.00\n")
    edited = table_from_text("id,sku,price\n1,123,4.75\n2,123,6.00\n")
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert [f.original for f in analysis.repairs] == ["00123", "0123"]


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("(7)", "-7"),
        ("7-", "-7"),
        ("\u22127", "-7"),
        ("=7", "7"),
        ('="007"', "007"),
        (" true", "TRUE"),
        ("-$7", "$-7"),
        ("($7.00)", "-$7.00"),
        ("1:30", "01:30:00 AM"),
        ("12:00", "12:00:00 PM"),
        ("0 1/2", "1/2"),
        ("1-3", "JAN3"),
    ],
)
def test_more_keys_that_a_spreadsheet_writes_the_same_way(first: str, second: str) -> None:
    assert collision_forms(first) & collision_forms(second)
    reference = make_table(["id", "sku"], [[first, "00111"], [second, "0111"]])
    edited = make_table(["id", "sku"], [[second, "111"]])
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert statuses(analysis) == [matching.AMBIGUOUS_KEY]
    assert analysis.repairs == []


def test_many_duplicate_keys_stay_fast_and_reports_stay_short() -> None:
    reference = make_table(["id", "sku"], [[str(i), f"0{i}"] for i in range(3000)])
    edited = make_table(["id", "sku"], [["1", "1"] for _ in range(3000)])
    start = time.perf_counter()
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert time.perf_counter() - start < 5
    assert set(statuses(analysis)) == {matching.DUPLICATE_KEY}
    assert len(analysis.matches.rows[0].detail) < 200


def test_long_sequential_numeric_keys_stay_fast() -> None:
    base = 12345678901234000
    reference = make_table(["id", "sku"], [[str(base + i), f"0{i}"] for i in range(4000)])
    edited = make_table(["id", "sku"], [[str(base + i), str(i)] for i in range(4000)])
    start = time.perf_counter()
    analysis = analyze(reference, edited, ("id",), ("sku",))
    assert time.perf_counter() - start < 10
    # Keys that a 64-bit float cannot tell from a neighbour are refused; the rest match.
    found = set(statuses(analysis))
    assert found == {matching.MATCHED, matching.AMBIGUOUS_KEY}
    assert len(analysis.repairs) == statuses(analysis).count(matching.MATCHED)
