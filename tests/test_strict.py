"""Strict mode, the default: repair only when another column confirms the key match.

The less conservative mode is ``strict=False`` in Python and ``--trust-key``
on the command line.
"""

from __future__ import annotations

import json

from csv_safe_restore import detection
from csv_safe_restore.detection import Analysis, analyze
from csv_safe_restore.repair import repaired_rows

from conftest import Workspace, digest, table_from_text


def run(original: str, edited: str, identifiers: str = "sku", strict: bool = True) -> Analysis:
    return analyze(
        table_from_text(original),
        table_from_text(edited),
        ("id",),
        tuple(identifiers.split(",")),
        strict=strict,
    )


def categories(analysis: Analysis) -> list[tuple[str, str]]:
    return [(f.category, f.key[0]) for f in analysis.cell_findings]


REFERENCE = """
id,sku,name
1,00123,Ada
2,0456,Ben
3,789,Cy
"""


def test_unique_unchanged_column_confirms_the_record() -> None:
    edited = "id,sku,name\n1,123,Ada\n2,456,Ben\n3,789,Cy"
    analysis = run(REFERENCE, edited)
    assert categories(analysis) == [
        (detection.CONFIRMED_DAMAGE, "1"),
        (detection.CONFIRMED_DAMAGE, "2"),
    ]
    assert "confirmed by column 'name'" in analysis.repairs[0].detail
    assert [row[1] for row in repaired_rows(analysis)] == ["00123", "0456", "789"]


def test_key_and_identifier_only_is_never_repaired() -> None:
    # The classic renumbering trap: with no other column, nothing can confirm
    # that key 1 still means the same record.
    reference = "id,sku\n1,00123\n2,0123"
    edited = "id,sku\n1,123\n2,123"
    normal = run(reference, edited, strict=False)
    strict = run(reference, edited)
    assert len(normal.repairs) == 2
    assert strict.repairs == []
    assert categories(strict) == [
        (detection.UNCORROBORATED, "1"),
        (detection.UNCORROBORATED, "2"),
    ]
    assert "key is the only evidence" in strict.cell_findings[0].detail
    assert [row[1] for row in repaired_rows(strict)] == ["123", "123"]


def test_renumbered_keys_are_caught_by_the_other_column() -> None:
    # Records were renumbered: Ada now carries key 2, Ben key 1.
    reference = "id,sku,name\n1,00123,Ada\n2,0123,Ben"
    edited = "id,sku,name\n1,123,Ben\n2,123,Ada"
    analysis = run(reference, edited)
    assert analysis.repairs == []
    assert [row[1] for row in repaired_rows(analysis)] == ["123", "123"]


def test_reused_key_for_a_different_record_is_not_repaired() -> None:
    # Key 1 was deleted and reused for a new product that happens to have a
    # sku that looks like damage of the old one.
    reference = "id,sku,name\n1,007,Old valve\n2,555,Pump"
    edited = "id,sku,name\n1,7,New hose\n2,555,Pump"
    assert run(reference, edited).repairs == []


def test_value_shared_by_two_reference_records_does_not_confirm() -> None:
    reference = "id,sku,city\n1,00123,Oslo\n2,0456,Oslo\n3,789,Rome"
    edited = "id,sku,city\n1,123,Oslo\n2,456,Oslo\n3,789,Rome"
    analysis = run(reference, edited)
    assert analysis.repairs == []
    assert len(analysis.cells(detection.UNCORROBORATED)) == 2


def test_value_repeated_in_the_edited_file_does_not_confirm() -> None:
    # Unique in the reference, but another edited row now has it too.
    reference = "id,sku,name\n1,00123,Ada\n2,0456,Ben"
    edited = "id,sku,name\n1,123,Ada\n2,456,Ada"
    analysis = run(reference, edited)
    assert analysis.repairs == []


def test_legitimately_edited_column_does_not_confirm() -> None:
    reference = "id,sku,name\n1,00123,Ada\n2,0456,Ben"
    edited = "id,sku,name\n1,123,Ada Lovelace\n2,456,Ben"
    analysis = run(reference, edited)
    assert categories(analysis) == [
        (detection.UNCORROBORATED, "1"),
        (detection.CONFIRMED_DAMAGE, "2"),
    ]


def test_spreadsheet_damaged_column_does_not_confirm() -> None:
    # "0042" -> "42" is damage the tool knows, but only an exact, unchanged
    # value counts as corroboration.
    reference = "id,sku,code\n1,00123,0042\n2,0456,0043"
    edited = "id,sku,code\n1,123,42\n2,456,0043"
    analysis = run(reference, edited, identifiers="sku")
    assert categories(analysis) == [
        (detection.UNCORROBORATED, "1"),
        (detection.CONFIRMED_DAMAGE, "2"),
    ]


def test_value_that_only_looks_alike_does_not_confirm() -> None:
    reference = "id,sku,name\n1,00123,Ada\n2,0456,Ben"
    edited = "id,sku,name\n1,123,ada\n2,456, Ben"
    assert run(reference, edited).repairs == []


def test_empty_values_never_confirm() -> None:
    reference = "id,sku,note\n1,00123,\n2,0456, "
    edited = "id,sku,note\n1,123,\n2,456, "
    assert run(reference, edited).repairs == []


def test_another_unchanged_identifier_column_can_confirm() -> None:
    reference = "id,sku,barcode\n1,00123,0036000291452\n2,0456,0012345678905"
    edited = "id,sku,barcode\n1,123,0036000291452\n2,456,12345678905"
    analysis = run(reference, edited, identifiers="sku,barcode")
    # Row 1: barcode unchanged and unique -> sku restored.
    # Row 2: both identifier cells damaged, nothing else -> neither restored.
    assert categories(analysis) == [
        (detection.CONFIRMED_DAMAGE, "1"),
        (detection.UNCORROBORATED, "2"),
        (detection.UNCORROBORATED, "2"),
    ]


def test_intentional_edit_that_looks_like_damage_is_still_restored() -> None:
    # Known limitation: someone really changed 00123 to 123 on purpose. The
    # change is indistinguishable from spreadsheet damage, and the record is
    # confirmed, so strict mode restores it too. It is listed in the report.
    reference = "id,sku,name\n1,00123,Ada"
    edited = "id,sku,name\n1,123,Ada"
    analysis = run(reference, edited)
    assert [(f.current, f.original) for f in analysis.repairs] == [("123", "00123")]


def test_strict_is_the_default_of_the_python_api() -> None:
    edited = "id,sku,name\n1,123,Ada\n2,456,Ben\n3,789,Cy"
    analysis = analyze(table_from_text(REFERENCE), table_from_text(edited), ("id",), ("sku",))
    assert analysis.strict is True


def test_trust_key_mode_lets_the_key_alone_decide() -> None:
    edited = "id,sku,name\n1,123,Ada Lovelace\n2,456,Ben\n3,789,Cy"
    normal = run(REFERENCE, edited, strict=False)
    assert not normal.strict
    assert categories(normal) == [
        (detection.CONFIRMED_DAMAGE, "1"),
        (detection.CONFIRMED_DAMAGE, "2"),
    ]


def test_strict_is_the_default_on_the_command_line(workspace: Workspace) -> None:
    original = workspace.write("original.csv", "id,sku,name\n1,00123,Ada\n2,0456,Ada\n3,0789,Cy\n")
    edited = workspace.write("edited.csv", "id,sku,name\n1,123,Ada\n2,456,Ada\n3,789,Cy\n")
    before = digest(original), digest(edited)
    output = workspace.folder / "repaired.csv"
    report = workspace.folder / "report.json"
    code, out, _ = workspace.run(
        "repair", original, edited, "--key", "id", "--identifier-columns", "sku",
        "--output", output, "--format", "json", "--report", report,
    )  # fmt: skip
    assert code == 1  # two findings are left for review
    assert (digest(original), digest(edited)) == before
    assert output.read_text(encoding="utf-8") == "id,sku,name\n1,123,Ada\n2,456,Ada\n3,0789,Cy\n"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["strict"] is True
    assert data["summary"]["confirmed_damage"] == 1
    assert data["summary"]["damage_not_restored_unconfirmed"] == 2
    assert {cell["category"] for cell in data["review_cells"]} == {detection.UNCORROBORATED}

    code, out, _ = workspace.run(
        "check", original, edited, "--key", "id", "--identifier-columns", "sku"
    )
    assert code == 1
    assert "Mode               : strict (default" in out
    assert "not restored because the record is not confirmed" in out
    assert "key is the only evidence" in out
