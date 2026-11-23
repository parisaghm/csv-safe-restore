"""Regression tests for problems found in the third (pre-release) review.

Each test is the smallest reproduction of a problem that was found by running
the tool, named after what went wrong. Findings F1, F2, ... refer to
PRE_RELEASE_REVIEW.md.
"""

from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from csv_safe_restore import detection, matching
from csv_safe_restore.detection import Analysis, analyze
from csv_safe_restore.patterns import explain_corruption
from csv_safe_restore.repair import repaired_rows

from conftest import Workspace, digest, table_from_text


def run(
    original: str, edited: str, identifiers: str | None = "sku", strict: bool = False
) -> Analysis:
    """Analyse in --trust-key mode unless ``strict`` is given.

    Most of these bugs were in the key-matching layer that both modes share,
    and the less conservative mode is where they could cause a wrong repair.
    Strict mode (the default) builds on the same layer.
    """
    columns = tuple(identifiers.split(",")) if identifiers else None
    return analyze(
        table_from_text(original), table_from_text(edited), ("id",), columns, strict=strict
    )


def statuses(analysis: Analysis) -> list[str]:
    return [row.status for row in analysis.matches.rows]


# F1 - swapped keys were trusted when the row only loosely matched its own
# record (equal after a spreadsheet-style normalisation) while another
# reference record matched it exactly.
@pytest.mark.parametrize(
    ("first", "second"),
    [
        ('"1,000"', "1"),  # thousands separator vs. decimal comma reading
        ("5%", "5"),
        (" Widget", "Widget"),
        ("1.50", "1.5"),
    ],
)
def test_swapped_keys_are_caught_even_when_the_other_column_only_loosely_differs(
    first: str, second: str
) -> None:
    import csv
    import io

    def table(text: str):
        rows = list(csv.reader(io.StringIO(text)))
        from conftest import make_table

        return make_table(rows[0], rows[1:])

    reference = f"id,sku,other\nP1,00123,{first}\nP2,0123,{second}\n"
    edited = f"id,sku,other\nP1,123,{second}\nP2,123,{first}\n"  # the keys were swapped
    analysis = analyze(table(reference), table(edited), ("id",), ("sku",))
    assert analysis.repairs == []
    assert statuses(analysis) == [matching.KEY_REASSIGNED, matching.KEY_REASSIGNED]


# F2 - numbers with more than 28 significant digits were invisible to the
# rival search and the key-ambiguity check (Decimal's default precision).
def test_rivals_are_found_for_numbers_longer_than_28_digits() -> None:
    x = "123456789012345678901234567891"  # 30 digits
    reference = f"id,acct,name\nK1,000{x},A\nK2,00{x},B\nK3,5,C"
    edited = f"id,acct,name\nK1,{x},B\nK3,5,C"  # K2's record now carries key K1
    analysis = run(reference, edited, identifiers="acct")
    assert analysis.repairs == []
    assert statuses(analysis)[0] == matching.KEY_REASSIGNED


def test_key_ambiguity_is_found_for_very_long_numeric_keys() -> None:
    long = "1" + "2" * 60  # 61 digits
    reference = f"id,sku\n000{long},001\n{long},002"
    edited = f"id,sku\n{long},2"
    analysis = run(reference, edited)
    assert analysis.repairs == []
    assert statuses(analysis) == [matching.AMBIGUOUS_KEY]


# F3 - one fragile-looking value made a whole column a repair target, and
# legitimate edits in it were reverted.
def test_one_date_like_value_does_not_make_a_date_column_an_identifier() -> None:
    reference = "id,phone,birthday\nA,0401234567,10-11\nB,0409999999,1990-05-06"
    edited = "id,phone,birthday\nA,0401234567,2011-10-11\nB,0409999999,1990-05-06"
    analysis = run(reference, edited, identifiers=None)
    assert analysis.identifier_columns == ("phone",)
    assert repaired_rows(analysis)[0][2] == "2011-10-11"
    assert any("'birthday'" in warning and "not checked" in warning for warning in analysis.warnings)


def test_one_long_number_does_not_make_an_amount_column_an_identifier() -> None:
    reference = "id,sku,amount\nA,0012,1500\nB,0013,10\nC,0014,123456789012"
    edited = "id,sku,amount\nA,12,1500.00\nB,13,10.0\nC,14,123456789012"
    analysis = run(reference, edited, identifiers=None)
    assert analysis.identifier_columns == ("sku",)
    assert [row[2] for row in repaired_rows(analysis)] == ["1500.00", "10.0", "123456789012"]


def test_repairs_in_detected_columns_come_with_a_note() -> None:
    reference = "id,sku\nA,0012\nB,0013"
    edited = "id,sku\nA,12\nB,0013"
    analysis = run(reference, edited, identifiers=None)
    assert len(analysis.repairs) == 1
    assert any("detected automatically" in warning for warning in analysis.warnings)


# F4 - a reference value that itself looks damaged was copied back over a
# hand-fixed value.
def test_damaged_looking_reference_value_is_never_copied_back() -> None:
    reference = "id,sku\nA,5.90123E+12\nB,00123"
    edited = "id,sku\nA,5901230000000\nB,123"
    analysis = run(reference, edited)
    assert [row[1] for row in repaired_rows(analysis)] == ["5901230000000", "00123"]
    finding = next(f for f in analysis.cell_findings if f.key == ("A",))
    assert finding.category == detection.DAMAGED_REFERENCE
    assert finding.detail == "scientific notation; it is not copied back"


# F5 - a quote character in an unquoted header field fooled line-ending
# detection.
def test_line_ending_detection_ignores_quotes_inside_unquoted_fields(tmp_path) -> None:
    from csv_safe_restore.reader import read_csv

    path = tmp_path / "f.csv"
    path.write_bytes(b'id,sku,5" pipe\r\nA,00123,x\r\n')
    assert read_csv(path).newline == "\r\n"


# F6 - a file name that is not valid UTF-8 crashed the report writer.
@pytest.mark.skipif(sys.platform != "linux", reason="needs a file system that allows such names")
def test_file_name_with_undecodable_bytes(workspace: Workspace) -> None:
    original = workspace.write("original.csv", "id,sku,name\nA,00123,Ann\n")
    edited_name = os.fsdecode(b"edited\xff.csv")
    edited = workspace.write(edited_name, "id,sku,name\nA,123,Ann\n")
    output, report = workspace.folder / "out.csv", workspace.folder / "report.json"
    code, _, err = workspace.run(
        "repair", original, edited, "--key", "id", "--output", output,
        "--format", "json", "--report", report,
    )  # fmt: skip
    assert code == 0, err
    assert json.loads(report.read_text(encoding="utf-8"))["edited_file"].endswith("edited\\xff.csv")


# F7 - Ctrl+C printed a traceback.
def test_interrupt_is_reported_without_a_traceback(workspace: Workspace, monkeypatch) -> None:
    from csv_safe_restore import cli

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "analyze", interrupted)
    original = workspace.write("original.csv", "id,sku\nA,00123\n")
    edited = workspace.write("edited.csv", "id,sku\nA,123\n")
    output = workspace.folder / "out.csv"
    code, _, err = workspace.run("repair", original, edited, "--key", "id", "--output", output)
    assert code == 130
    assert "interrupted" in err and "Traceback" not in err
    assert not output.exists()


# F8 - --overwrite replaced an existing file with default permissions.
@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_overwrite_keeps_the_permissions_of_the_replaced_file(workspace: Workspace) -> None:
    original = workspace.write("original.csv", "id,sku,name\nA,00123,Ann\n")
    edited = workspace.write("edited.csv", "id,sku,name\nA,123,Ann\n")
    output = workspace.write("out.csv", "old")
    output.chmod(0o600)
    code, _, _ = workspace.run(
        "repair", original, edited, "--key", "id", "--output", output, "--overwrite"
    )
    assert code == 0
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert output.read_text(encoding="utf-8") == "id,sku,name\nA,00123,Ann\n"


# F9 - a long but legal output name failed because of the temporary file name.
@pytest.mark.skipif(
    sys.platform == "win32",
    reason="Windows limits a whole path to 260 characters unless long paths are enabled",
)
def test_long_output_file_name(workspace: Workspace) -> None:
    original = workspace.write("original.csv", "id,sku,name\nA,00123,Ann\n")
    edited = workspace.write("edited.csv", "id,sku,name\nA,123,Ann\n")
    output = workspace.folder / ("o" * 240 + ".csv")
    code, _, err = workspace.run("repair", original, edited, "--key", "id", "--output", output)
    assert code == 0, err
    assert output.exists()


# F10 - a closed standard output (sys.stdout is None) gave a traceback after
# the repaired file had been written.
def test_closed_standard_output_is_an_error_before_any_work(
    workspace: Workspace, monkeypatch
) -> None:
    original = workspace.write("original.csv", "id,sku\nA,00123\n")
    edited = workspace.write("edited.csv", "id,sku\nA,123\n")
    output = workspace.folder / "out.csv"
    before = digest(edited)
    monkeypatch.setattr(sys, "stdout", None)
    code, _, err = workspace.run("repair", original, edited, "--key", "id", "--output", output)
    assert code == 2
    assert "standard output" in err
    assert not output.exists()
    assert digest(edited) == before


# F11 - smaller rule and message problems.
def test_utf32_file_is_named_correctly(tmp_path) -> None:
    from csv_safe_restore.errors import CsvSafeRestoreError
    from csv_safe_restore.reader import read_csv

    path = tmp_path / "f.csv"
    path.write_bytes("id,sku\n".encode("utf-32"))
    with pytest.raises(CsvSafeRestoreError, match="UTF-32"):
        read_csv(path)


@pytest.mark.parametrize(("original", "observed"), [("0", "-0"), ("00", "-0"), ("0.0", "-0")])
def test_sign_of_zero_is_not_spreadsheet_damage(original: str, observed: str) -> None:
    assert explain_corruption(original, observed) is None


# Fuzzer finding - in strict mode, an unexplained change to another identifier
# of the same row is a sign that it may not be the same record.
def test_strict_mode_refuses_rows_with_an_unexplained_identifier_change() -> None:
    reference = "id,sku,code,name\n1,000,12-1,Ada\n2,0456,77,Ben"
    edited = "id,sku,code,name\n1,0,000999,Ada\n2,456,77,Ben"
    normal = run(reference, edited, identifiers="sku,code")
    strict = run(reference, edited, identifiers="sku,code", strict=True)
    assert [f.key[0] for f in normal.repairs] == ["1", "2"]
    assert [f.key[0] for f in strict.repairs] == ["2"]
    held = strict.cells(detection.UNCORROBORATED)
    assert [f.key[0] for f in held] == ["1"]
    assert "'code'" in held[0].detail


# Re-check of the fixes (second pass of the third review), R1-R4.


# R1 - strict mode: a "unique" corroborating value could itself be another
# record's value after spreadsheet damage.
@pytest.mark.parametrize(
    ("reference", "edited", "identifiers"),
    [
        # Ann deleted, Bob renumbered 2 -> 1; Q 0777 -> 777 equals Ann's Q.
        ("id,P,Q\n1,00150,777\n2,0150,0777", "id,P,Q\n1,150,777", "P,Q"),
        # Bob's amount 1.50 became 1.5, Ann's value.
        ("id,P,amount\n1,00150,1.5\n2,0150,1.50\n3,0042,2.25", "id,P,amount\n1,150,1.5\n2,42,2.25", "P"),
        # Found by random testing.
        ("id,P,Q,o0\n1,2,04,y\n2,04,001,w\n3,4,01,w", "id,P,Q,o0\n2,2,4,y\n3,4,1,w", "P,Q"),
    ],
)
def test_strict_corroboration_cannot_be_another_records_damaged_value(
    reference: str, edited: str, identifiers: str
) -> None:
    analysis = run(reference, edited, identifiers=identifiers, strict=True)
    restored = {(f.key[0], f.column): f.original for f in analysis.repairs}
    # Only repairs whose truth is certain may remain; in these files none is.
    assert restored == {}


# R2 - normal mode: identifier columns that were only loosely equal let the
# row skip the rival check.
def test_rival_check_counts_inexact_identifier_columns() -> None:
    reference = "id,qty,P,Q\n1,1,00150,0777\n2,1,0150,777"
    edited = "id,qty,P,Q\n1,1,150,777\n2,1,150,777"  # keys 1 and 2 swapped
    analysis = run(reference, edited, identifiers="P,Q")
    # Line 2 carries record 2's exact Q, so it is flagged. Line 3 cannot be
    # told apart from record 2 with a damaged P (inherent to the data).
    assert statuses(analysis)[0] == matching.KEY_REASSIGNED
    assert all(f.key != ("1",) for f in analysis.repairs)


# R3 - a sparse column with one fragile value passed the "more than half"
# rule because empty cells were not counted.
def test_sparse_column_is_not_detected_as_identifier() -> None:
    reference = "id,name,P,birthday\n1,Ann,0011,10-11\n2,Bob,0022,\n3,Cid,0033,\n4,Dan,0044,"
    edited = "id,name,P,birthday\n1,Ann,11,2011-10-11\n2,Bob,22,1990-02-03\n3,Cid,33,\n4,Dan,44,"
    analysis = run(reference, edited, identifiers=None)
    assert analysis.identifier_columns == ("P",)
    assert repaired_rows(analysis)[0][3] == "2011-10-11"


# R4 - numeric keys longer than the analysed length were never checked for
# ambiguity.
@pytest.mark.parametrize(
    ("long_key", "short_key"),
    [("0" * 398 + "123", "123"), ("1" * 401, "1" * 15 + "0" * 386)],
)
def test_numeric_keys_too_long_to_check_are_not_trusted(long_key: str, short_key: str) -> None:
    reference = f"id,P\n{long_key},0150\n{short_key},00150"
    edited = f"id,P\n{short_key},150"
    analysis = run(reference, edited, identifiers="P")
    assert analysis.repairs == []
    assert statuses(analysis) == [matching.AMBIGUOUS_KEY]
    assert "too long" in analysis.matches.rows[0].detail


# Found by Hypothesis on Python 3.12/3.13 after the R2 fix: the column being
# checked was counted as evidence for the rival that really has the observed
# value. Rows identical apart from the key must be decided by the key.
def test_the_checked_column_is_not_evidence_for_a_rival() -> None:
    reference = "id,code,price\nK0,10,1.00\nK1,010,1.00"
    edited = "id,code,price\nK1,10,9.99\nK0,10,9.99"
    analysis = run(reference, edited, identifiers="code")
    assert statuses(analysis) == [matching.MATCHED, matching.MATCHED]
    assert [row[1] for row in repaired_rows(analysis)] == ["010", "10"]
