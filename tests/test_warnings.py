"""Warnings added in 0.1.0a2. They only inform: no repair decision depends on them."""

from __future__ import annotations

import json

import pytest

from csv_safe_restore import detection
from csv_safe_restore.detection import Analysis, analyze
from csv_safe_restore.repair import repaired_rows

from conftest import Workspace, table_from_text


def run(original: str, edited: str, identifiers: str | None = "sku", strict: bool = True) -> Analysis:
    columns = tuple(identifiers.split(",")) if identifiers else None
    return analyze(
        table_from_text(original), table_from_text(edited), ("id",), columns, strict=strict
    )


def warnings_with(analysis: Analysis, text: str) -> list[str]:
    return [warning for warning in analysis.warnings if text in warning]


# --------------------------------------------------------------------------
# 1. Restorations that may undo a deliberate edit
# --------------------------------------------------------------------------

# "name" is unique and unchanged, so strict mode confirms every record.
REFERENCE = """
id,sku,name
A,00123,Alpha
B,00456,Beta
C,5901234123457,Gamma
"""


def test_restored_leading_zeros_come_with_a_note_that_they_may_have_been_deliberate() -> None:
    edited = REFERENCE.replace("00123", "123").replace("00456", "456")
    analysis = run(REFERENCE, edited)
    assert [row[1] for row in repaired_rows(analysis)] == ["00123", "00456", "5901234123457"]
    (note,) = warnings_with(analysis, "could also have changed on purpose")
    assert note.startswith("2 of the 2 restoration(s)")
    assert "leading zeros removed" in note
    assert "'123' -> '00123'" in note
    assert "cannot tell" in note


def test_damage_nobody_types_on_purpose_gets_no_such_note() -> None:
    edited = REFERENCE.replace("5901234123457", "5.90123E+12")
    analysis = run(REFERENCE, edited)
    assert len(analysis.repairs) == 1
    assert not warnings_with(analysis, "on purpose")


def test_values_left_for_review_are_not_counted_as_restorations() -> None:
    # Without a confirming column, strict mode restores nothing, so there is
    # nothing that could undo a deliberate edit.
    reference = "id,sku\nA,00123\nB,00456\n"
    analysis = run(reference, "id,sku\nA,123\nB,456\n")
    assert analysis.repairs == []
    assert not warnings_with(analysis, "on purpose")


def test_kept_leading_zeros_in_matched_rows_point_to_possible_deliberate_edits() -> None:
    # B kept its zeros, so no spreadsheet stripped the whole column: A's
    # missing zeros may have been removed by hand.
    edited = REFERENCE.replace("00123", "123")
    analysis = run(REFERENCE, edited)
    assert [row[1] for row in repaired_rows(analysis)] == ["00123", "00456", "5901234123457"]
    (hint,) = warnings_with(analysis, "still have their leading zeros")
    assert "column 'sku', 1 matched value(s)" in hint
    assert "some may have been removed on purpose" in hint


def test_a_new_record_with_leading_zeros_does_not_trigger_the_hint() -> None:
    edited = REFERENCE.replace("00123", "123").replace("00456", "456") + "N,0077,New\n"
    analysis = run(REFERENCE, edited)
    assert warnings_with(analysis, "could also have changed on purpose")
    assert not warnings_with(analysis, "still have their leading zeros")


# --------------------------------------------------------------------------
# 2. Columns that are not checked but show spreadsheet damage
# --------------------------------------------------------------------------

ORDERS = """
id,sku,postal_code,phone,price,active,note
A,00123,00501,+447700900001,4.50,true,first
B,00456,02134,+447700900002,6.00,false,second
"""


def test_unchecked_column_with_lost_information_is_named_but_not_repaired() -> None:
    edited = ORDERS.replace("00501", "501").replace("02134", "2134")
    analysis = run(ORDERS, edited)
    (warning,) = warnings_with(analysis, "'postal_code' was not checked")
    assert "2 of its value(s)" in warning
    assert "leading zeros removed" in warning
    assert "line 2: '00501' -> '501'" in warning
    assert "--identifier-columns" in warning
    assert [row[2] for row in repaired_rows(analysis)] == ["501", "2134"]


def test_a_dropped_plus_sign_is_named() -> None:
    analysis = run(ORDERS, ORDERS.replace("+447700900001", "447700900001"))
    (warning,) = warnings_with(analysis, "'phone' was not checked")
    assert "'+447700900001' -> '447700900001'" in warning
    assert [row[3] for row in repaired_rows(analysis)][0] == "447700900001"


def test_formatting_only_changes_get_one_softer_combined_note() -> None:
    edited = ORDERS.replace("4.50", "4.5").replace("6.00", "6").replace("true", "TRUE")
    analysis = run(ORDERS, edited)
    assert not warnings_with(analysis, "was not checked, but")
    (note,) = warnings_with(analysis, "Only the formatting changed")
    assert "'price' (2, for example '4.50' -> '4.5')" in note
    assert "'active' (1, for example 'true' -> 'TRUE')" in note
    assert "usually harmless" in note
    assert [row[4:6] for row in repaired_rows(analysis)] == [["4.5", "TRUE"], ["6", "false"]]


def test_a_column_with_both_kinds_is_named_once_with_every_change_counted() -> None:
    reference = "id,sku,hs_code\nA,00123,0964.21\nB,00456,8471.30\n"
    edited = "id,sku,hs_code\nA,00123,964.21\nB,00456,8471.3\n"
    analysis = run(reference, edited)
    (warning,) = warnings_with(analysis, "'hs_code'")
    assert "was not checked, but 2 of its value(s)" in warning


def test_ordinary_edits_in_unchecked_columns_give_no_warning() -> None:
    edited = ORDERS.replace("4.50", "4.75").replace("first", "1st").replace("true", "false")
    analysis = run(ORDERS, edited)
    assert analysis.unchecked_damage == {}
    assert not warnings_with(analysis, "not checked")
    assert not warnings_with(analysis, "formatting")


def test_new_records_are_not_counted() -> None:
    analysis = run(ORDERS, ORDERS + "N,00789,501,447700900009,1.5,TRUE,new\n")
    assert analysis.unchecked_damage == {}


def test_detected_columns_are_not_named_twice() -> None:
    # Auto-detection skips postal_code (too few fragile values). The new
    # warning, which says what changed, replaces the old one for that column.
    reference = "id,sku,postal_code\nA,00123,00501\nB,00456,90210\nC,00789,10001\n"
    edited = "id,sku,postal_code\nA,00123,501\nB,00456,90210\nC,00789,10001\n"
    analysis = run(reference, edited, identifiers=None)
    assert analysis.identifier_columns == ("sku",)
    assert len(warnings_with(analysis, "'postal_code'")) == 1
    assert warnings_with(analysis, "'postal_code' was not checked, but 1 of its value(s)")


# --------------------------------------------------------------------------
# 3. Long numbers in the reference that end in zeros by chance
# --------------------------------------------------------------------------


def accounts(count: int, zeros_every: int, digits: int = 16) -> list[str]:
    """Distinct ``digits``-digit numbers; every ``zeros_every``-th one ends in zeros."""
    values = []
    for number in range(count):
        value = str(10 ** (digits - 1) + 7919 * number * 10 + 1)
        if number % zeros_every == 0:
            value = value[:15] + "0" * (digits - 15)
        values.append(value)
    return values


def reference_with(values: list[str]) -> str:
    lines = ["id,account"] + [f"R{i},{value}" for i, value in enumerate(values)]
    return "\n".join(lines) + "\n"


def test_genuine_long_numbers_ending_in_zero_by_chance_give_no_warning() -> None:
    # One in ten 16-digit numbers ends in 0 by chance: that is no sign of damage.
    reference = reference_with(accounts(200, zeros_every=10))
    analysis = run(reference, reference, identifiers="account")
    assert not warnings_with(analysis, "reference file itself")


def test_a_single_number_ending_in_zero_gives_no_warning() -> None:
    reference = reference_with(["6725461496159140", "6725461496159141"])
    assert not warnings_with(run(reference, reference, identifiers="account"), "reference file")


def test_a_column_of_cut_off_numbers_still_gets_a_warning() -> None:
    reference = reference_with(accounts(20, zeros_every=1))
    (warning,) = warnings_with(run(reference, reference, identifiers="account"), "reference file")
    assert "20 long number(s) in column 'account'" in warning
    assert "more than chance explains" in warning


def test_a_few_cut_off_numbers_of_18_digits_still_get_a_warning() -> None:
    # Ending in 000 by chance happens to one 18-digit number in 1000.
    reference = reference_with(accounts(3, zeros_every=1, digits=18))
    assert warnings_with(run(reference, reference, identifiers="account"), "more than chance")


def test_obvious_damage_in_the_reference_is_still_reported_one_value_at_a_time() -> None:
    reference = reference_with(["1.23457E+15", "6725461496159141"])
    (warning,) = warnings_with(run(reference, reference, identifiers="account"), "reference file")
    assert "1 value(s) in column 'account' that look damaged" in warning


def test_a_repeated_value_counts_once() -> None:
    # The same account on 30 order lines is one number, not 30 coincidences.
    reference = reference_with(["6725461496159140"] * 30)
    assert not warnings_with(run(reference, reference, identifiers="account"), "reference file")


def test_what_is_copied_back_does_not_depend_on_the_reference_warning() -> None:
    reference = reference_with(["6725461496159140"])
    edited = reference_with(["6.72546149615914E+15"])
    for strict in (True, False):
        analysis = run(reference, edited, identifiers="account", strict=strict)
        # The cut-off shape is weak evidence, so the value is still compared
        # and restored exactly as the reference has it (with --trust-key), or
        # held back for lack of a confirming column (strict).
        expected = "6725461496159140" if not strict else "6.72546149615914E+15"
        assert repaired_rows(analysis) == [["R0", expected]]


def test_a_cut_off_number_too_long_for_a_float_chance_does_not_crash() -> None:
    # 10 ** -386 is 0.0 as a float; the check must still work.
    reference = reference_with(["1" + "2" * 14 + "0" * 386])
    assert warnings_with(run(reference, reference, identifiers="account"), "more than chance")


@pytest.mark.parametrize(
    ("count", "expected", "low", "high"),
    [
        (1, 0.1, 0.095, 0.096),  # 1 - e^-0.1
        (3, 5.0, 1.0, 1.0),  # fewer than expected: no evidence at all
        (64, 55.5, 0.1, 0.2),  # the Phase 2 supplier accounts
        (200_000, 100_000.0, 0.0, 1e-300),  # very large numbers do not overflow
    ],
)
def test_chance_of_at_least(count: int, expected: float, low: float, high: float) -> None:
    assert low <= detection._chance_of_at_least(count, expected) <= high


# --------------------------------------------------------------------------
# Exit codes and the JSON report keep their meaning and shape
# --------------------------------------------------------------------------

JSON_KEYS = [
    "tool", "version", "schema_version", "command", "strict", "reference_file",
    "edited_file", "output_file", "key_columns", "identifier_columns",
    "identifier_columns_auto_detected", "gtin_columns", "summary", "repairs_applied",
    "confirmed_damage", "review_cells", "review_records", "new_records", "deleted_records",
    "collapsed_values", "other_column_edits_kept", "columns_only_in_reference",
    "columns_only_in_edited", "warnings", "result", "exit_code",
]


def test_new_warnings_do_not_change_exit_codes_or_the_json_shape(workspace: Workspace) -> None:
    reference = workspace.write("reference.csv", "id,sku,name,phone\nA,00123,Alpha,+4477\n")
    edited = workspace.write("edited.csv", "id,sku,name,phone\nA,123,Alpha,4477\n")
    options = ("--key", "id", "--identifier-columns", "sku")

    code, out, _ = workspace.run("check", reference, edited, *options, "--format", "json")
    report = json.loads(out)
    assert code == report["exit_code"] == 1  # repairable damage, as before
    assert list(report) == JSON_KEYS
    assert report["schema_version"] == 1
    assert len(report["warnings"]) == 2
    assert all(isinstance(warning, str) for warning in report["warnings"])

    output = workspace.folder / "repaired.csv"
    code, out, _ = workspace.run("repair", reference, edited, *options, "--output", output)
    assert code == 0  # everything repaired; warnings never make it 1
    assert "\nNOTES\n" in out and "could also have changed on purpose" in out
    assert output.read_text(encoding="utf-8") == "id,sku,name,phone\nA,00123,Alpha,4477\n"
