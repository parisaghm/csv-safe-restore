"""Matching and classification: when a change is damage, an edit, or unknowable."""

from __future__ import annotations

import pytest

from csv_safe_restore import detection, matching
from csv_safe_restore.detection import Analysis
from csv_safe_restore.errors import CsvSafeRestoreError
from csv_safe_restore.repair import repaired_rows

from conftest import analyze_trusting_key as analyze
from conftest import table_from_text

ORIGINAL = """
id,sku,barcode,price
A,00123,0036000291452,4.50
B,0123,4006381333931,6.00
C,123,5901234123457,8.25
D,AB-0042,123456789012,2.10
"""


def run(original: str, edited: str, key: str = "id", identifiers: str | None = "sku") -> Analysis:
    columns = tuple(identifiers.split(",")) if identifiers else None
    return analyze(table_from_text(original), table_from_text(edited), tuple(key.split(",")), columns)


def cells(analysis: Analysis) -> list[tuple[str, str, str]]:
    """(category, key, column) for each cell finding."""
    return [(f.category, f.key[0], f.column) for f in analysis.cell_findings]


def rows(analysis: Analysis) -> list[tuple[str, str]]:
    return [(f.category, f.key[0]) for f in analysis.row_findings]


def test_identical_files_have_no_findings() -> None:
    analysis = run(ORIGINAL, ORIGINAL, identifiers="sku,barcode")
    assert analysis.cell_findings == []
    assert analysis.row_findings == []
    assert analysis.matched_records == 4
    assert analysis.unchanged_identifier_cells == 8
    assert analysis.review_count == 0


def test_the_ambiguous_example_is_resolved_only_through_the_key() -> None:
    """00123, 0123 and 123 all became 123. Only the key can tell them apart."""
    edited = """
id,sku,barcode,price
C,123,5901234123457,8.25
A,123,0036000291452,4.50
B,123,4006381333931,6.00
D,AB-0042,123456789012,2.10
"""
    analysis = run(ORIGINAL, edited)
    restored = {f.key[0]: f.original for f in analysis.repairs}
    assert restored == {"A": "00123", "B": "0123"}  # C really is 123 and is untouched
    assert [(c.current, c.originals) for c in analysis.collisions] == [("123", ["00123", "0123"])]
    # Reordered rows keep the edited order in the output.
    assert [row[:2] for row in repaired_rows(analysis)] == [
        ["C", "123"],
        ["A", "00123"],
        ["B", "0123"],
        ["D", "AB-0042"],
    ]


def test_long_identifier_and_scientific_notation_are_confirmed_damage() -> None:
    edited = ORIGINAL.replace("5901234123457", "5.90123E+12").replace("123456789012", "1.23457E+11")
    analysis = run(ORIGINAL, edited, identifiers="barcode")
    assert [(f.key[0], f.pattern) for f in analysis.repairs] == [
        ("C", "scientific_notation"),
        ("D", "scientific_notation"),
    ]


def test_intentional_identifier_edit_is_reported_not_reverted() -> None:
    edited = ORIGINAL.replace("AB-0042", "AB-0043")
    analysis = run(ORIGINAL, edited)
    assert cells(analysis) == [(detection.UNEXPLAINED_CHANGE, "D", "sku")]
    assert analysis.repairs == []
    assert repaired_rows(analysis)[3][1] == "AB-0043"


def test_damage_shaped_value_that_does_not_fit_the_reference_is_suspicious() -> None:
    edited = ORIGINAL.replace("AB-0042", "4.2E+01")
    analysis = run(ORIGINAL, edited)
    assert cells(analysis) == [(detection.SUSPICIOUS_CHANGE, "D", "sku")]
    assert repaired_rows(analysis)[3][1] == "4.2E+01"


def test_edits_in_other_columns_are_kept_and_counted() -> None:
    edited = ORIGINAL.replace("4.50", "4.75").replace("00123", "123")
    analysis = run(ORIGINAL, edited)
    assert analysis.other_edits == {"price": 1}
    assert repaired_rows(analysis)[0] == ["A", "00123", "0036000291452", "4.75"]


def test_number_like_edit_in_a_non_identifier_column_is_not_touched() -> None:
    """price 6.00 -> 6 looks like 'number reformatted', but price is not an identifier."""
    edited = ORIGINAL.replace("6.00", "6")
    analysis = run(ORIGINAL, edited)
    assert analysis.repairs == []
    assert analysis.other_edits == {"price": 1}


def test_new_and_deleted_records() -> None:
    edited = """
id,sku,barcode,price
A,00123,0036000291452,4.50
C,123,5901234123457,8.25
D,AB-0042,123456789012,2.10
E,0500,4.00638E+12,1.00
"""
    analysis = run(ORIGINAL, edited, identifiers="sku,barcode")
    assert rows(analysis) == [(matching.NEW_RECORD, "E"), (detection.DELETED_RECORD, "B")]
    # The new record has a damaged barcode, but there is nothing to restore it from.
    assert cells(analysis) == [(detection.UNRECOVERABLE, "E", "barcode")]
    assert analysis.repairs == []
    assert len(repaired_rows(analysis)) == 4  # deleted record is not added back


def test_duplicate_key_in_edited_file_blocks_repair_for_those_rows() -> None:
    edited = """
id,sku,barcode,price
A,123,0036000291452,4.50
A,123,0036000291452,4.50
B,123,4006381333931,6.00
"""
    analysis = run(ORIGINAL, edited)
    assert [r for r in rows(analysis) if r[0] == matching.DUPLICATE_KEY] == [
        (matching.DUPLICATE_KEY, "A"),
        (matching.DUPLICATE_KEY, "A"),
    ]
    assert [f.key[0] for f in analysis.repairs] == ["B"]


def test_duplicate_key_in_reference_file_blocks_repair_for_that_key() -> None:
    original = ORIGINAL + "A,0999,0000000000000,1.00\n"
    edited = ORIGINAL.replace("00123", "123").replace("0123,", "123,")
    analysis = run(original, edited)
    assert (matching.DUPLICATE_KEY, "A") in rows(analysis)
    assert [f.key[0] for f in analysis.repairs] == ["B"]
    assert any("more than once" in warning for warning in analysis.warnings)


def test_missing_key_blocks_repair_for_that_row() -> None:
    edited = ORIGINAL.replace("A,00123", ",123").replace("B,0123", "  ,123")
    analysis = run(ORIGINAL, edited)
    assert rows(analysis)[:2] == [(matching.MISSING_KEY, ""), (matching.MISSING_KEY, "  ")]
    assert analysis.repairs == []


def test_damaged_key_is_flagged_and_never_matched() -> None:
    original = "id,sku\n007,00123\n008,0456\n"
    edited = "id,sku\n7,123\n008,456\n"
    analysis = run(original, edited)
    assert rows(analysis) == [
        (matching.SUSPECTED_KEY_DAMAGE, "7"),
        (detection.DELETED_RECORD, "007"),
    ]
    assert [f.key[0] for f in analysis.repairs] == ["008"]
    assert repaired_rows(analysis) == [["7", "123"], ["008", "0456"]]


def test_key_collision_makes_the_match_ambiguous() -> None:
    """Reference keys 007 and 7. An edited row '7' may be either record."""
    original = "id,sku\n007,00123\n7,0123\n9,0999\n"
    # The real '7' row was deleted and the '007' key lost its zeros.
    edited = "id,sku\n7,123\n9,999\n"
    analysis = run(original, edited)
    assert (matching.AMBIGUOUS_KEY, "7") in rows(analysis)
    assert [f.key[0] for f in analysis.repairs] == ["9"]
    assert repaired_rows(analysis)[0] == ["7", "123"]  # not "restored" to 0123


def test_identifier_cannot_be_its_own_key() -> None:
    with pytest.raises(CsvSafeRestoreError, match="cannot be both a key and an identifier"):
        run(ORIGINAL, ORIGINAL, key="sku", identifiers="sku")


def test_using_the_damaged_identifier_as_key_repairs_nothing() -> None:
    edited = ORIGINAL.replace("00123", "123").replace("0123,", "123,")
    analysis = run(ORIGINAL, edited, key="sku", identifiers="barcode")
    assert analysis.repairs == []
    assert matching.DUPLICATE_KEY in {category for category, _ in rows(analysis)}


def test_composite_key() -> None:
    original = "store,item,sku\nS1,1,00123\nS2,1,0123\n"
    edited = "store,item,sku\nS2,1,123\nS1,1,123\n"
    analysis = run(original, edited, key="store,item")
    assert {f.key: f.original for f in analysis.repairs} == {
        ("S1", "1"): "00123",
        ("S2", "1"): "0123",
    }


def test_reordered_extra_and_missing_columns() -> None:
    original = "id,sku,price,old\nA,00123,4.50,x\n"
    edited = "note,price,sku,id\nhello,4.75,123,A\n"
    analysis = run(original, edited)
    assert repaired_rows(analysis) == [["hello", "4.75", "00123", "A"]]
    assert analysis.columns_only_in_reference == ["old"]
    assert analysis.columns_only_in_edited == ["note"]


def test_identifier_columns_are_detected_from_the_reference_file() -> None:
    analysis = run(ORIGINAL, ORIGINAL, identifiers=None)
    # Only 2 of 4 sku values have a fragile shape: not a majority, so the
    # column is not checked, and the report says so.
    assert analysis.identifier_columns == ("barcode",)
    assert analysis.identifiers_auto_detected is True
    assert any("'sku' has 2 of 4" in warning for warning in analysis.warnings)


def test_no_identifier_columns_is_an_error_not_a_silent_pass() -> None:
    with pytest.raises(CsvSafeRestoreError, match="No identifier columns"):
        run("id,name\n1,x\n", "id,name\n1,y\n", identifiers=None)


@pytest.mark.parametrize(
    ("key", "identifiers", "message"),
    [
        ("nope", "sku", "Key column(s) 'nope' not found"),
        ("id", "nope", "Identifier column(s) 'nope' not found"),
    ],
)
def test_unknown_columns_are_errors(key: str, identifiers: str, message: str) -> None:
    with pytest.raises(CsvSafeRestoreError) as error:
        run(ORIGINAL, ORIGINAL, key=key, identifiers=identifiers)
    assert message in str(error.value)


def test_identifier_column_missing_from_edited_file_is_an_error() -> None:
    with pytest.raises(CsvSafeRestoreError, match="not found in the edited file"):
        run(ORIGINAL, "id,barcode,price\nA,1,2\n")


def test_empty_values_are_never_filled_in() -> None:
    edited = ORIGINAL.replace("00123", "")
    analysis = run(ORIGINAL, edited)
    assert cells(analysis) == [(detection.UNEXPLAINED_CHANGE, "A", "sku")]
    assert repaired_rows(analysis)[0][1] == ""


def test_reference_file_that_looks_damaged_itself_gets_a_warning() -> None:
    reference = ORIGINAL.replace("00123", "1.23E+02")
    analysis = run(reference, reference)
    assert any("reference file itself" in warning for warning in analysis.warnings)
    assert not any("reference file itself" in w for w in run(ORIGINAL, ORIGINAL).warnings)


def test_gtin_check_adds_warnings_but_never_repairs() -> None:
    edited = """
id,sku,barcode,price
A,00123,0036000291453,4.50
B,0123,4006381333931,6.00
E,0500,4006381333932,1.00
"""
    analysis = analyze(
        table_from_text(ORIGINAL), table_from_text(edited), ("id",), ("sku",), ("barcode",)
    )
    found = {(f.category, f.key[0]): f.detail for f in analysis.cell_findings}
    assert "not a valid GTIN" in found[(detection.UNEXPLAINED_CHANGE, "A")]
    assert "not a valid GTIN" in found[(detection.INVALID_GTIN, "E")]
    assert analysis.repairs == []


def test_valid_check_digit_is_not_treated_as_proof() -> None:
    """Barcode changed to another *valid* barcode: still only reported."""
    edited = ORIGINAL.replace("0036000291452", "4006381333931")
    analysis = analyze(
        table_from_text(ORIGINAL), table_from_text(edited), ("id",), ("sku",), ("barcode",)
    )
    assert cells(analysis) == [(detection.UNEXPLAINED_CHANGE, "A", "barcode")]
    assert analysis.repairs == []
